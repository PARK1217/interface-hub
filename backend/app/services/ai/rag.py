"""과거 장애 이력 기반 RAG — 멀티 프로바이더 LLM + TF-IDF 검색 + Redis 캐싱.

흐름:
  1. **캐시 조회**: question_hash 로 Redis 1차 lookup → hit 면 즉시 반환
  2. **검색**: TF-IDF char n-gram 으로 과거 incident Top-K
  3. **생성**: services/ai/llm.chat() 위임 (멀티 프로바이더)
  4. **Fallback**: 키 없거나 실패 → 템플릿 응답 + llm_error 동봉
  5. **로깅**: ai_query_logs 1행 기록 (popular questions / 본인 히스토리 용도)
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.time import now_kst

from app.models import AiQueryLog, Incident, User
from app.models.incident import IncidentType
from app.services.ai import cache as ai_cache
from app.services.ai.context import build_config_context, build_stats_context
from app.services.ai.intent import Intent, classify_intent
from app.services.ai.llm import LLMError, LLMResponse, chat as llm_chat
from app.services.ai.llm import current_model, current_provider, is_configured, resolve_chain

log = logging.getLogger("noahub.ai.rag")

# 유사도가 이 값 미만이면 "관련 사례 없음" 으로 간주 (TF-IDF char-ngram 기준 경험치)
NO_MATCH_THRESHOLD = 0.05
_NO_MATCH_THRESHOLD = NO_MATCH_THRESHOLD  # 내부 호환

# 같은 질문의 분석 실패 이력이 이 시간 내에 있으면 재호출하지 않고
# 저장된 안내를 그대로 반환. 하루 지나면 인프라 상태가 바뀌었을 수 있으니 다시 허용.
_FAILURE_REUSE_HOURS = 24

# 분석 실패 유형 — 두 그룹:
# (1) 사용자 입력 결함: 같은 질문은 영원히 같은 결과 → DB 기록도 안 함, 매번 즉시 안내
_USER_INPUT_FAILURES = {"empty_question", "no_match"}
# (2) 환경 결함: 운영자가 고치면 자동 해제 → DB 기록 + 24h 재사용 가드
_ENVIRONMENT_FAILURES = {"no_history", "scikit_missing", "llm_failed"}
# 재사용 판정 키 — 환경 결함 한정 (사용자 입력 결함은 애초에 DB 없음)
_FAILED_OUTCOMES = _ENVIRONMENT_FAILURES


def _excerpt(text: str, n: int = 500) -> str:
    if not text:
        return ""
    s = text.strip()
    return s if len(s) <= n else s[:n] + "…"


def _analysis_note(kind: str, title: str, detail: str, suggestion: str) -> dict:
    """검색/분석 단계의 사용자 안내 (LLM 호출과 무관한 사유).

    kind:
      - "no_history":     해결된 과거 incident 가 0건 — 비교 대상 자체 없음
      - "no_match":       과거 데이터는 있지만 질문과 유사도가 임계 미만
      - "empty_question": 질문이 비어있거나 너무 짧음
      - "scikit_missing": sklearn 미설치 — 시스템 설정 문제
    """
    return {
        "kind": kind,
        "title": title,
        "detail": detail,
        "suggestion": suggestion,
    }


class RagService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- 공통: 과거 장애 검색 (TF-IDF) ---------------------------------------
    def _retrieve(
        self, question: str, top_k: int
    ) -> tuple[list[Incident], list[dict], dict | None]:
        """반환: (incidents, cases dict, analysis_note dict | None).

        analysis_note 는 검색 단계에서 사용자에게 알려줄 사유 (no_history /
        no_match / scikit_missing). cases 가 비었어도 LLM 자체는 정상이라
        LLMError 와는 별개 채널로 노출.
        """
        incidents = self.db.scalars(
            select(Incident).where(Incident.resolved_at.is_not(None))
        ).all()
        if not incidents:
            return [], [], _analysis_note(
                "no_history",
                "분석할 과거 사례가 없습니다",
                "DB 의 incidents 테이블에 resolved (해결 완료) 상태인 사례가 한 건도 없습니다.",
                "장애가 발생해 운영자가 '해결로 표시' 처리한 사례가 1건이라도 쌓이면 자동 활성화됩니다. 데모는 docker compose exec backend python -m app.scripts.seed_demo 로 시드 가능.",
            )

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            log.warning("scikit-learn 미설치 — 빈 결과 반환")
            return [], [], _analysis_note(
                "scikit_missing",
                "검색 엔진 미설치",
                "백엔드 컨테이너에 scikit-learn 이 설치되어 있지 않아 TF-IDF 검색을 수행할 수 없습니다.",
                "backend/requirements.txt 의 scikit-learn 의존성을 확인하고 컨테이너 이미지를 다시 빌드하세요.",
            )

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

        note: dict | None = None
        # Top-1 유사도가 임계 미만 → 사실상 매칭 실패. cases 는 그래도 표시 (운영자 판단)
        if cases and cases[0]["score"] < _NO_MATCH_THRESHOLD:
            note = _analysis_note(
                "no_match",
                "유사한 과거 사례를 찾지 못함",
                f"Top-1 유사도 {cases[0]['score']:.2f} 가 임계({_NO_MATCH_THRESHOLD}) 미만입니다. "
                "질문 키워드가 과거 incident 어느 것과도 충분히 겹치지 않습니다.",
                "인터페이스명·기관명·HTTP 상태코드 같은 구체 키워드를 포함해 다시 질문하거나, "
                "'예시 프롬프트' 칩을 클릭해 보세요.",
            )
        return picked, cases, note

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

    # --- 실패 이력 재사용 ---------------------------------------
    def _find_recent_failure(self, question_hash: str, *, intent: Intent) -> AiQueryLog | None:
        """같은 question_hash 의 최근 24h **환경 결함** 실패 로그 (사용자 입력 결함은
        DB 에 없으니 여기서 걸리지 않음 — 24h 가드는 환경 결함에만 의미 있음).
        """
        since = now_kst() - timedelta(hours=_FAILURE_REUSE_HOURS)
        return self.db.scalar(
            select(AiQueryLog)
            .where(AiQueryLog.question_hash == question_hash)
            .where(AiQueryLog.outcome.in_(list(_FAILED_OUTCOMES)))
            .where(AiQueryLog.asked_at >= since)
            .order_by(desc(AiQueryLog.asked_at))
            .limit(1)
        )

    def _replay_failure(self, prev: AiQueryLog, *, question: str) -> dict:
        """이전 실패 로그 → 사용자에게 돌려줄 응답 dict. LLM/DB 검색 전부 생략."""
        # 환경 결함 3종 (no_history / scikit_missing / llm_failed) 만 해당.
        # 사용자 입력 결함 (empty_question / no_match) 은 DB 에 아예 없어 여기 오지 않음.
        note_map = {
            "no_history": _analysis_note(
                "no_history",
                "최근 분석 실패한 질문",
                "같은 질문이 24시간 안에 '분석할 과거 사례 없음' 으로 처리된 이력이 있습니다.",
                "장애가 한 건도 resolved 된 적이 없는 상태입니다. 시드 데이터 또는 실제 장애 처리 후 재시도.",
            ),
            "scikit_missing": _analysis_note(
                "scikit_missing",
                "최근 분석 실패한 질문",
                "같은 질문이 24시간 안에 '검색 엔진 미설치' 로 처리된 이력이 있습니다.",
                "backend 컨테이너에 scikit-learn 설치 후 재기동 필요.",
            ),
            "llm_failed": _analysis_note(
                "no_match",  # UI 아이콘 재사용
                "최근 LLM 호출 실패한 질문",
                "같은 질문이 24시간 안에 LLM 호출 실패로 처리된 이력이 있습니다.",
                "AI_PROVIDER 키 / 네트워크를 점검한 뒤 다시 시도하거나, 질문을 수정하세요.",
            ),
        }
        note = note_map.get(prev.outcome, _analysis_note(
            prev.outcome, "최근 분석 실패한 질문",
            f"같은 질문이 24시간 안에 '{prev.outcome}' 사유로 실패했습니다.",
            "질문을 다르게 표현하거나 24시간 후 다시 시도하세요.",
        ))
        return self._make_response(
            mode="fallback", provider="fallback", model=None,
            answer=(
                f"이 질문은 24시간 안에 이미 분석 불가로 판정되었습니다.\n"
                f"(이전 사유: {prev.outcome} · {prev.asked_at.isoformat() if prev.asked_at else '-'})\n\n"
                f"같은 질문을 다시 보내도 동일한 결과가 나오므로 LLM/DB 호출을 건너뛰었습니다.\n"
                f"다르게 표현해 재시도하세요."
            ),
            cases=[],
            intent="general", outcome=prev.outcome,
            analysis_note=note,
            repeated_failure=True,
        )

    # --- 로깅 ----------------------------------------------------
    def _log_query(
        self,
        *,
        question: str,
        question_hash: str,
        result: dict,
        actor: User | None,
        hit_cache: bool,
    ) -> None:
        # 사용자 입력 결함 (empty_question / no_match) 은 DB 저장 안 함.
        # 같은 질문 재입력 시 매번 같은 결과라 로그 가치 없음, 히스토리 / popular 도
        # 자동 제외됨 (DB 에 없으니까). 환경 결함만 append-only 로 추적.
        outcome = result.get("outcome") or "success"
        if outcome in _USER_INPUT_FAILURES:
            return
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
                outcome=result.get("outcome") or "success",
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
    def _make_response(
        self,
        *,
        mode: str,
        provider: str | None,
        model: str | None,
        answer: str,
        cases: list[dict],
        intent: Intent = "general",
        outcome: str = "success",
        llm_error: dict | None = None,
        llm_attempts: list[dict] | None = None,
        analysis_note: dict | None = None,
        cached: bool = False,
        repeated_failure: bool = False,
    ) -> dict:
        return {
            "mode": mode,
            "provider": provider,
            "model": model,
            "answer": answer,
            "similar_cases": cases,
            "intent": intent,
            "outcome": outcome,
            "llm_error": llm_error,
            "llm_attempts": llm_attempts or [],
            "analysis_note": analysis_note,
            "cached": cached,
            "repeated_failure": repeated_failure,
        }

    # --- LLM fallback 체인 --------------------------------------
    async def _call_llm(
        self, prompt: str, *, system: str | None = None,
    ) -> tuple[LLMResponse | LLMError, list[dict]]:
        """resolve_chain() 순서로 시도. 성공하면 즉시 반환, 실패는 attempts 에 누적.

        반환: (최종 결과, attempts). 체인이 비어있으면 not_configured LLMError.
        """
        chain = resolve_chain()
        attempts: list[dict] = []
        if not chain:
            err = LLMError(
                kind="not_configured", provider="fallback",
                title="AI 분석기 비활성화",
                detail="설정된 프로바이더 키가 하나도 없습니다.",
                suggestion="backend/.env 에 MISTRAL_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY 중 하나 이상을 설정하세요.",
            )
            return err, attempts

        last_err: LLMError | None = None
        for provider in chain:
            res = await llm_chat(prompt, system=system, provider=provider)
            if isinstance(res, LLMResponse):
                return res, attempts
            # 실패 기록 (다음 체인으로)
            attempts.append({
                "provider": res.provider,
                "kind": res.kind,
                "status": res.status,
                "title": res.title,
            })
            last_err = res
            log.info(
                "LLM chain step failed — provider=%s kind=%s status=%s → next",
                res.provider, res.kind, res.status,
            )
        # 체인 전부 실패
        return last_err or LLMError(
            kind="unknown", provider="fallback",
            title="모든 프로바이더 실패", detail="체인 시도 결과 모두 실패했습니다.",
            suggestion="각 프로바이더 상태를 확인하세요.",
        ), attempts

    async def ask(
        self,
        question: str,
        top_k: int = 3,
        *,
        actor: User | None = None,
    ) -> dict:
        """검색·통계·설정 컨텍스트 → LLM. intent 별 분기로 환각 차단.

        intent:
          - stats_query   → 최근 운영 통계 markdown 표를 LLM 컨텍스트로 사용
          - case_lookup   → 기존 RAG (resolved incident 검색)
          - config_query  → 인터페이스 메타 정보 컨텍스트
          - general       → case_lookup 과 동일 동작 (기본값)

        case_lookup + no_match 시 LLM 호출하지 않음 (환각 방지). 사용자에게는
        "관련 사례 없음 + 키워드 구체화 안내" 만 표시.
        """
        # 0) 빈 질문 / 너무 짧은 질문 — LLM 호출 전에 차단
        q_stripped = (question or "").strip()
        if len(q_stripped) < 5:
            res = self._make_response(
                mode="fallback", provider="fallback", model=None,
                answer="질문이 너무 짧아 분석을 시작할 수 없습니다.",
                cases=[],
                intent="general", outcome="empty_question",
                analysis_note=_analysis_note(
                    "empty_question",
                    "질문이 비어있거나 너무 짧음",
                    f"입력된 질문 길이가 {len(q_stripped)}자 입니다. 최소 5자 이상 필요.",
                    "어떤 인터페이스의 / 어떤 증상이 / 언제부터 발생했는지를 한 문장으로 적어주세요. "
                    "예) '보험개발원 API 가 오후 3시부터 401 에러로 막힘'",
                ),
            )
            self._log_query(
                question=question, question_hash="<too_short>",
                result=res, actor=actor, hit_cache=False,
            )
            return res

        intent: Intent = classify_intent(question)
        provider = current_provider()
        model = current_model()
        # 캐시 키에 intent 포함 — 같은 질문이라도 분류가 달라지면 다른 응답.
        qhash = ai_cache.question_hash(
            question, provider=provider, model=model, top_k=top_k, intent=intent,
        )

        # 0.5) 같은 질문의 최근 24h 실패 이력이 있으면 LLM/DB 호출
        # 모두 생략하고 저장된 안내를 반환. 무관 / 너무 짧은 질문 반복 입력 방지.
        prev = self._find_recent_failure(qhash, intent=intent)
        if prev is not None:
            res = self._replay_failure(prev, question=q_stripped)
            self._log_query(
                question=question, question_hash=qhash,
                result=res, actor=actor, hit_cache=False,
            )
            return res

        # 1) 캐시 조회
        cached = await ai_cache.get_cached(qhash)
        if cached is not None:
            cached["cached"] = True
            cached.setdefault("analysis_note", None)
            cached.setdefault("intent", intent)
            self._log_query(
                question=question, question_hash=qhash,
                result=cached, actor=actor, hit_cache=True,
            )
            return cached

        # 2) intent 별 분기
        if intent == "stats_query":
            return await self._handle_stats(
                question, intent=intent, provider=provider, model=model,
                qhash=qhash, actor=actor,
            )
        if intent == "config_query":
            return await self._handle_config(
                question, intent=intent, provider=provider, model=model,
                qhash=qhash, actor=actor,
            )
        # case_lookup / general → 기존 RAG 흐름
        return await self._handle_case_lookup(
            question, top_k=top_k, intent=intent, provider=provider, model=model,
            qhash=qhash, actor=actor,
        )

    # --- intent 별 핸들러 ----------------------------------------------------
    async def _handle_stats(
        self, question: str, *, intent: Intent, provider: str, model: str | None,
        qhash: str, actor: User | None,
    ) -> dict:
        """운영 통계 질문 — 과거 사례 무시, 현재 DB 통계 컨텍스트만 사용."""
        stats_md = build_stats_context(self.db, days=7, top_n=10)

        if not is_configured():
            # LLM 없이도 통계 표 자체가 답이 됨 — 그대로 노출
            res = self._make_response(
                mode="fallback", provider="fallback", model=None,
                answer=("[운영 통계 분석 — Fallback 모드]\n\n"
                        "LLM 미설정 상태라 통계 데이터를 그대로 표시합니다.\n\n" + stats_md),
                cases=[], intent=intent,
            )
            self._log_query(question=question, question_hash=qhash,
                            result=res, actor=actor, hit_cache=False)
            return res

        system = (
            "당신은 보험사 인터페이스 운영 분석가입니다. 아래 제공된 운영 통계 표만 "
            "근거로 한국어로 답하세요. 표에 없는 일반론(인증서 만료/OOM 같은 추측)은 "
            "절대 추가하지 마세요. 데이터에 답이 없으면 '제공된 통계로는 답할 수 없음' 이라고 명시하세요."
        )
        prompt = (
            f"[현재 운영 통계 데이터]\n{stats_md}\n\n"
            f"[운영자 질문]\n{question}\n\n"
            "위 통계 데이터에서 사실 기반으로 답변해주세요:\n"
            "1) 답변 (구체 인터페이스명·수치 인용)\n"
            "2) 함께 보면 좋을 데이터 (있다면)\n"
            "3) 권장 후속 행동 (예: incidents 페이지 확인, mute 검토 등)"
        )
        llm_res, attempts = await self._call_llm(prompt, system=system)
        if isinstance(llm_res, LLMError):
            res = self._make_response(
                mode="fallback", provider="fallback", model=None,
                answer=("[운영 통계 분석 — LLM 호출 실패, fallback]\n\n" + stats_md),
                cases=[], intent=intent, outcome="llm_failed",
                llm_error=llm_res.to_dict(), llm_attempts=attempts,
            )
            self._log_query(question=question, question_hash=qhash,
                            result=res, actor=actor, hit_cache=False)
            return res

        res = self._make_response(
            mode="llm", provider=llm_res.provider, model=llm_res.model,
            answer=llm_res.content,
            cases=[], intent=intent, llm_attempts=attempts,
        )
        await ai_cache.set_cached(qhash, res)
        self._log_query(question=question, question_hash=qhash,
                        result=res, actor=actor, hit_cache=False)
        return res

    async def _handle_config(
        self, question: str, *, intent: Intent, provider: str, model: str | None,
        qhash: str, actor: User | None,
    ) -> dict:
        """설정 정보 질문 — 인터페이스 메타 컨텍스트."""
        config_md = build_config_context(self.db, top_n=30)

        if not is_configured():
            res = self._make_response(
                mode="fallback", provider="fallback", model=None,
                answer=("[설정 정보 조회 — Fallback 모드]\n\n" + config_md),
                cases=[], intent=intent,
            )
            self._log_query(question=question, question_hash=qhash,
                            result=res, actor=actor, hit_cache=False)
            return res

        system = (
            "당신은 보험사 인터페이스 시스템 관리자입니다. 아래 인터페이스 메타 표만 "
            "근거로 한국어로 답하세요. 표에 없는 정보는 '등록된 정보 내에 없음' 이라고 명시하세요."
        )
        prompt = (
            f"[등록된 인터페이스 메타]\n{config_md}\n\n[질문]\n{question}\n\n"
            "표에서 사실 기반으로 답변해주세요. 필요한 추가 작업(수정/등록)이 있으면 마지막에 안내."
        )
        llm_res, attempts = await self._call_llm(prompt, system=system)
        if isinstance(llm_res, LLMError):
            res = self._make_response(
                mode="fallback", provider="fallback", model=None,
                answer=("[설정 정보 조회 — LLM 호출 실패, fallback]\n\n" + config_md),
                cases=[], intent=intent, outcome="llm_failed",
                llm_error=llm_res.to_dict(), llm_attempts=attempts,
            )
            self._log_query(question=question, question_hash=qhash,
                            result=res, actor=actor, hit_cache=False)
            return res

        res = self._make_response(
            mode="llm", provider=llm_res.provider, model=llm_res.model,
            answer=llm_res.content,
            cases=[], intent=intent, llm_attempts=attempts,
        )
        await ai_cache.set_cached(qhash, res)
        self._log_query(question=question, question_hash=qhash,
                        result=res, actor=actor, hit_cache=False)
        return res

    async def _handle_case_lookup(
        self, question: str, *, top_k: int, intent: Intent, provider: str, model: str | None,
        qhash: str, actor: User | None,
    ) -> dict:
        """과거 사례 검색 질문 — 기존 RAG. no_match 면 LLM 강행 안 함 (환각 차단)."""
        _, cases, analysis_note = self._retrieve(question, top_k)

        # no_history / scikit_missing → 명확한 안내, LLM 호출 안 함
        if analysis_note and analysis_note["kind"] in ("no_history", "scikit_missing"):
            res = self._make_response(
                mode="fallback", provider="fallback", model=None,
                answer=f"분석을 시작할 수 없습니다 — {analysis_note['title']}",
                cases=[], intent=intent, outcome=analysis_note["kind"],
                analysis_note=analysis_note,
            )
            self._log_query(question=question, question_hash=qhash,
                            result=res, actor=actor, hit_cache=False)
            return res

        # no_match — 핵심 변경: LLM 호출 강행 안 함 (환각 답변 방지)
        if analysis_note and analysis_note["kind"] == "no_match":
            res = self._make_response(
                mode="fallback", provider="fallback", model=None,
                answer=(
                    "관련된 과거 장애 사례를 찾지 못해 LLM 분석을 진행하지 않았습니다.\n"
                    "키워드를 더 구체적으로 (인터페이스명·기관명·HTTP 상태) 다시 질문해 주세요.\n"
                    "운영 현황을 보고 싶다면 '지금 장애가 잦은 인터페이스' 같이 통계 질문으로 다시 시도해 보세요."
                ),
                cases=cases,  # 그래도 Top 후보는 보여줌 (운영자가 직접 판단)
                intent=intent, outcome="no_match",
                analysis_note=analysis_note,
            )
            self._log_query(question=question, question_hash=qhash,
                            result=res, actor=actor, hit_cache=False)
            return res

        # LLM 비활성 → fallback
        if not is_configured():
            res = self._make_response(
                mode="fallback", provider="fallback", model=None,
                answer=self._template_answer(cases),
                cases=cases, intent=intent, analysis_note=analysis_note,
            )
            self._log_query(question=question, question_hash=qhash,
                            result=res, actor=actor, hit_cache=False)
            return res

        # LLM 호출 (정상 경로 — 사례가 충분히 매칭됨)
        context = "\n\n---\n\n".join(c["content"] for c in cases)
        system = (
            "당신은 보험사 인터페이스 운영 어시스턴트입니다. 아래 제공된 과거 사례만 "
            "근거로 한국어로 답하세요. 사례에 없는 일반론은 추가하지 마세요."
        )
        prompt = (
            f"[과거 장애 사례 Top-{len(cases)}]\n{context}\n\n"
            f"[신규 장애 질문]\n{question}\n\n"
            "위 사례를 참고해 다음 형식으로 답변:\n"
            "1) 원인 가설 (Top-1)\n2) 추가 점검 항목\n3) 권장 조치 절차"
        )
        llm_res, attempts = await self._call_llm(prompt, system=system)
        if isinstance(llm_res, LLMError):
            res = self._make_response(
                mode="fallback", provider="fallback", model=None,
                answer=self._template_answer(cases),
                cases=cases, intent=intent, outcome="llm_failed",
                llm_error=llm_res.to_dict(), llm_attempts=attempts,
                analysis_note=analysis_note,
            )
            self._log_query(question=question, question_hash=qhash,
                            result=res, actor=actor, hit_cache=False)
            return res

        res = self._make_response(
            mode="llm", provider=llm_res.provider, model=llm_res.model,
            answer=llm_res.content,
            cases=cases, intent=intent, llm_attempts=attempts,
            analysis_note=analysis_note,
        )
        await ai_cache.set_cached(qhash, res)
        self._log_query(question=question, question_hash=qhash,
                        result=res, actor=actor, hit_cache=False)
        return res


def llm_status() -> dict:
    return {
        "configured": is_configured(),
        "provider": current_provider(),
        "model": current_model(),
    }


def score_questions(db: Session, questions: list[str]) -> list[float]:
    """여러 후보 질문의 Top-1 유사도를 일괄 계산.

    /suggestions 가 생성한 후보 중 no_match 임계 미만인 것을 사전에 걸러내는 용도.
    TfidfVectorizer 를 한 번만 fit 하고 여러 query 를 transform — 개별 _retrieve
    호출보다 훨씬 저렴.

    반환: 각 question 의 Top-1 cosine similarity (0.0~1.0). 과거 사례 없거나
    sklearn 미설치 시 모든 값 0.0 (= 전부 필터 대상).
    """
    if not questions:
        return []
    incidents = db.scalars(
        select(Incident).where(Incident.resolved_at.is_not(None))
    ).all()
    if not incidents:
        return [0.0] * len(questions)
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return [0.0] * len(questions)

    docs = [
        f"{r.summary}\n원인:{r.root_cause or ''}\n조치:{r.resolution or ''}"
        for r in incidents
    ]
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
    matrix = vec.fit_transform([*docs, *questions])
    doc_count = len(docs)
    scores: list[float] = []
    for i in range(len(questions)):
        q_idx = doc_count + i
        sim = cosine_similarity(matrix[q_idx], matrix[:doc_count])[0]
        scores.append(float(sim.max()) if len(sim) else 0.0)
    return scores