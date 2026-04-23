"""과거 장애 이력 기반 RAG — 멀티 프로바이더 LLM + TF-IDF 검색.

새 설계 (멀티 프로바이더):
  1. **검색**: TF-IDF char n-gram (한국어 토크나이저 불필요, 의존성 가벼움)
  2. **생성**: services/ai/llm.chat() 으로 위임 → AI_PROVIDER 환경변수에 따라
     Mistral / Anthropic / HuggingFace / OpenAI 자동 선택
  3. **Fallback**: 프로바이더 키 없거나 호출 실패 → 템플릿 응답 (mode='fallback')

기존 LangChain+FAISS+OpenAIEmbeddings 경로는 제거 — 단일 프로바이더 의존
+ 임베딩 비용/설정 부담 큼. TF-IDF 도 한국어 보험사 incident 도메인 (사례
30건 이내) 에선 충분히 정확.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Incident
from app.models.incident import IncidentType
from app.services.ai.llm import chat as llm_chat
from app.services.ai.llm import current_model, current_provider, is_configured

log = logging.getLogger("noahub.ai.rag")


class RagService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- 공통: 과거 장애 검색 (TF-IDF) ---------------------------------------
    def _retrieve(self, question: str, top_k: int) -> tuple[list[Incident], list[dict]]:
        """질문으로 관련 incident Top-K 를 TF-IDF char n-gram 매칭으로 검색.

        반환: (Incident 객체 리스트, 응답용 dict 리스트)
        """
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

    # --- 메인 진입점 ---------------------------------------------------------
    def ask_fallback(self, question: str, top_k: int = 3) -> dict:
        """LLM 없이 TF-IDF + 템플릿. 호환성 위해 유지 — 라우트는 ask() 우선."""
        _, cases = self._retrieve(question, top_k)
        return {
            "mode": "fallback",
            "provider": "fallback",
            "model": None,
            "answer": self._template_answer(cases),
            "similar_cases": cases,
        }

    async def ask(self, question: str, top_k: int = 3) -> dict:
        """검색 + (가능하면) LLM 생성, 아니면 fallback.

        프로바이더는 ``AI_PROVIDER`` 환경변수로 결정 (mistral/anthropic/
        huggingface/openai). 키 없거나 호출 실패 → fallback.
        """
        _, cases = self._retrieve(question, top_k)
        if not cases:
            return self.ask_fallback(question, top_k)

        if not is_configured():
            return {
                "mode": "fallback",
                "provider": "fallback",
                "model": None,
                "answer": self._template_answer(cases),
                "similar_cases": cases,
            }

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
        res = await llm_chat(prompt, system=system)
        if res is None:
            # LLM 호출 실패 → fallback 템플릿
            return {
                "mode": "fallback",
                "provider": "fallback",
                "model": None,
                "answer": self._template_answer(cases) + "\n\n(주의: LLM 호출 실패로 fallback)",
                "similar_cases": cases,
            }
        return {
            "mode": "llm",
            "provider": res.provider,
            "model": res.model,
            "answer": res.content,
            "similar_cases": cases,
        }


# 라우트에서 빠르게 현재 상태 확인용
def llm_status() -> dict:
    return {
        "configured": is_configured(),
        "provider": current_provider(),
        "model": current_model(),
    }