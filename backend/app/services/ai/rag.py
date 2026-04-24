"""과거 장애 이력 기반 RAG — 멀티 프로바이더 LLM + TF-IDF 검색 + Redis 캐싱.

흐름:
  1. **캐시 조회** (Phase B.8): question_hash 로 Redis 1차 lookup → hit 면 즉시 반환
  2. **검색**: TF-IDF char n-gram 으로 과거 incident Top-K
  3. **생성**: services/ai/llm.chat() 위임 (멀티 프로바이더)
  4. **Fallback**: 키 없거나 실패 → 템플릿 응답 + llm_error 동봉
  5. **로깅** (Phase B.8): ai_query_logs 1행 기록 (popular questions / 본인 히스토리 용도)
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AiQueryLog, Incident, User
from app.models.incident import IncidentType
from app.services.ai import cache as ai_cache
from app.services.ai.llm import LLMError, LLMResponse, chat as llm_chat
from app.services.ai.llm import current_model, current_provider, is_configured

log = logging.getLogger("noahub.ai.rag")


def _excerpt(text: str, n: int = 500) -> str:
    if not text:
        return ""
    s = text.strip()
    return s if len(s) <= n else s[:n] + "…"


class RagService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- 공통: 과거 장애 검색 (TF-IDF) ---------------------------------------
    def _retrieve(self, question: str, top_k: int) -> tuple[list[Incident], list[dict]]:
        incidents = self.db.scalars(
            select(Incident).where(Incident.resolved_at.is_not(None))
        ).all()
        if not incidents:
            return [], []

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            log.warning("scikit-learn 미설치 — 빈 결과 반환")
            return [], []

        docs = [
            f"{r.summary}\n원인:{r.root_cause or ''}\n조치:{r.resolution or ''}"
            for r in incidents
        ]
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
        matrix = vec.fit_transform([*docs, question])
        sims = cosine_similarity(matrix[-1], matrix[:-1])[0]
        order = sims.argsort()[::-1][:top_k]
        cases = []
        picked = []
        for i in order:
            inc = incidents[i]
            picked.append(inc)
            cases.append(
                {
                    "incident_id": inc.id,
                    "type": inc.type.value if isinstance(inc.type, IncidentType) else str(inc.type),
                    "content": docs[i],
                    "score": float(sims[i]),
                }
            )
        return picked, cases

    # --- Fallback: 템플릿 응답 ------------------------------------------------
    def _template_answer(self, cases: list[dict]) -> str:
        if not cases:
            return "분석할 과거 장애 이력이 아직 없습니다. 장애 발생 후 다시 시도하세요."
        top = cases[0]
        lines = [
            "[Fallback 모드 — LLM 미설정, TF-IDF 키워드 유사도 매칭]",
            "",
            f"가장 유사한 과거 사례 (유사도 {top['score']:.2f}):",
            top["content"],
            "",
            "권장 조치:",
            "  · 위 사례의 root cause / 조치 절차가 현재 상황과 일치하는지 먼저 확인",
            "  · 인터페이스별 임계값 / 인증서 만료 / 상대방 측 공지를 동시에 점검",
            "  · 동일 root cause로 판명되면 위 사례의 resolution 그대로 적용 가능",
        ]
        if len(cases) > 1:
            lines.append("")
            lines.append(f"추가 후보: {len(cases) - 1}건 더 있음 (아래 카드 참조)")
        return "\n".join(lines)

    # --- 로깅 (Phase B.8) ----------------------------------------------------
    def _log_query(
        self,
        *,
        question: str,
        question_hash: str,
        result: dict,
        actor: User | None,
        hit_cache: bool,
    ) -> None:
        try:
            cases = result.get("similar_cases") or []
            err = result.get("llm_error") or {}
            row = AiQueryLog(
                actor_user_id=actor.id if actor else None,
                actor_username=actor.username if actor else None,
                question=question,
                question_hash=question_hash,
                mode=result.get("mode") or "fallback",
                provider=result.get("provider"),
                model=result.get("model"),
                llm_error_kind=err.get("kind") if err else None,
                hit_cache=hit_cache,
                similarity_top=cases[0]["score"] if cases else None,
                response_excerpt=_excerpt(result.get("answer", ""), 500),
            )
            self.db.add(row)
            self.db.commit()
        except Exception:  # noqa: BLE001
            log.exception("ai_query_log 기록 실패 — 본 응답은 정상")
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass

    # --- 메인 진입점 ---------------------------------------------------------
    def ask_fallback(
        self, question: str, top_k: int = 3, *, llm_error: dict | None = None
    ) -> dict:
        _, cases = self._retrieve(question, top_k)
        return {
            "mode": "fallback",
            "provider": "fallback",
            "model": None,
            "answer": self._template_answer(cases),
            "similar_cases": cases,
            "llm_error": llm_error,
            "cached": False,
        }

    async def ask(
        self,
        question: str,
        top_k: int = 3,
        *,
        actor: User | None = None,
    ) -> dict:
        """검색 + (가능하면) LLM 생성. 캐시 hit 시 LLM/검색 모두 스킵.

        actor 가 주어지면 ai_query_logs 에 기록 (popular questions / 본인 히스토리).
        """
        provider = current_provider()
        model = current_model()
        qhash = ai_cache.question_hash(question, provider=provider, model=model, top_k=top_k)

        # 1) 캐시 조회 — hit 시 LLM 호출 / TF-IDF 모두 스킵 (비용·지연 절감)
        cached = await ai_cache.get_cached(qhash)
        if cached is not None:
            cached["cached"] = True
            self._log_query(
                question=question, question_hash=qhash,
                result=cached, actor=actor, hit_cache=True,
            )
            return cached

        # 2) 과거 사례 검색
        _, cases = self._retrieve(question, top_k)
        if not cases:
            res = self.ask_fallback(question, top_k)
            self._log_query(
                question=question, question_hash=qhash,
                result=res, actor=actor, hit_cache=False,
            )
            return res

        # 3) LLM 비활성 → fallback
        if not is_configured():
            res = {
                "mode": "fallback",
                "provider": "fallback",
                "model": None,
                "answer": self._template_answer(cases),
                "similar_cases": cases,
                "llm_error": None,
                "cached": False,
            }
            self._log_query(
                question=question, question_hash=qhash,
                result=res, actor=actor, hit_cache=False,
            )
            return res

        # 4) LLM 호출
        context = "\n\n---\n\n".join(c["content"] for c in cases)
        system = (
            "당신은 보험사 인터페이스 운영 어시스턴트입니다. "
            "운영자에게 한국어로 명확하고 실용적인 답변을 제공하세요."
        )
        prompt = (
            f"[과거 장애 사례 Top-{len(cases)}]\n{context}\n\n"
            f"[신규 장애 질문]\n{question}\n\n"
            "위 과거 사례를 참고하여 다음 형식으로 답변해주세요:\n"
            "1) 원인 가설 (Top-1)\n"
            "2) 추가 점검 항목\n"
            "3) 권장 조치 절차"
        )
        llm_res = await llm_chat(prompt, system=system)
        if isinstance(llm_res, LLMError):
            res = {
                "mode": "fallback",
                "provider": "fallback",
                "model": None,
                "answer": self._template_answer(cases),
                "similar_cases": cases,
                "llm_error": llm_res.to_dict(),
                "cached": False,
            }
            self._log_query(
                question=question, question_hash=qhash,
                result=res, actor=actor, hit_cache=False,
            )
            # fallback 은 캐시 안 함 — 키 복구 후 LLM 다시 시도하도록
            return res

        # 5) LLM 성공 → 캐시 저장 + 로깅
        res = {
            "mode": "llm",
            "provider": llm_res.provider,
            "model": llm_res.model,
            "answer": llm_res.content,
            "similar_cases": cases,
            "llm_error": None,
            "cached": False,
        }
        await ai_cache.set_cached(qhash, res)
        self._log_query(
            question=question, question_hash=qhash,
            result=res, actor=actor, hit_cache=False,
        )
        return res


def llm_status() -> dict:
    return {
        "configured": is_configured(),
        "provider": current_provider(),
        "model": current_model(),
    }