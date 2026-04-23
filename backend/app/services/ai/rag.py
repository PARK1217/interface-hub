"""LangChain + FAISS RAG over historical incident cases (Phase 3).

The pipeline:
  1. Embed each (incident.summary + resolution) into a FAISS index on disk.
  2. On a new question, embed it and retrieve top-K similar past incidents.
  3. Hand the retrieved context + question to an LLM for "원인 가설 + 권장 조치".

Falls back to a no-LLM mode (returns retrieved cases verbatim) when
OPENAI_API_KEY is missing — useful for demos without API credits.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Incident, VectorCase
from app.models.incident import IncidentType

log = logging.getLogger("noahub.ai.rag")
INDEX_DIR = Path("faiss_index")


class RagService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    # ---- index maintenance --------------------------------------------------
    def _ensure_deps(self) -> None:
        try:
            import faiss  # noqa: F401
            import langchain_openai  # noqa: F401
        except ImportError as e:
            raise RuntimeError(f"AI dependencies missing: {e}") from e

    def reindex(self) -> int:
        """Rebuild the FAISS index from all resolved incidents. Returns count."""
        self._ensure_deps()
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        os.environ["OPENAI_API_KEY"] = self.settings.openai_api_key
        from langchain_community.vectorstores import FAISS
        from langchain_core.documents import Document
        from langchain_openai import OpenAIEmbeddings

        rows = self.db.scalars(
            select(Incident).where(Incident.resolved_at.is_not(None))
        ).all()
        docs = [
            Document(
                page_content=f"{r.summary}\n원인:{r.root_cause or ''}\n조치:{r.resolution or ''}",
                metadata={"incident_id": r.id, "type": r.type.value},
            )
            for r in rows
        ]
        if not docs:
            return 0
        emb = OpenAIEmbeddings(model=self.settings.embedding_model)
        store = FAISS.from_documents(docs, emb)
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        store.save_local(str(INDEX_DIR))

        # mirror metadata in vector_cases for downstream joins
        self.db.query(VectorCase).delete()
        for i, r in enumerate(rows):
            self.db.add(
                VectorCase(
                    incident_id=r.id,
                    faiss_id=i,
                    summary=r.summary,
                    resolution_text=r.resolution,
                    embedding_model=self.settings.embedding_model,
                )
            )
        self.db.commit()
        return len(docs)

    # ---- query --------------------------------------------------------------
    def ask_fallback(self, question: str, top_k: int = 3) -> dict:
        """No-LLM fallback: TF-IDF char-ngram matching over resolved incidents.

        Returned shape matches `ask()` so the route/frontend can stay agnostic.
        Uses character n-grams (2-4) so it works on Korean without a tokenizer.
        """
        incidents = self.db.scalars(
            select(Incident).where(Incident.resolved_at.is_not(None))
        ).all()
        if not incidents:
            return {
                "mode": "fallback",
                "answer": "분석할 과거 장애 이력이 아직 없습니다. 장애 발생 후 다시 시도하세요.",
                "similar_cases": [],
            }

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            return {
                "mode": "fallback",
                "answer": "scikit-learn이 설치되어 있지 않아 fallback도 사용할 수 없습니다.",
                "similar_cases": [],
            }

        docs = [
            f"{r.summary}\n원인:{r.root_cause or ''}\n조치:{r.resolution or ''}"
            for r in incidents
        ]
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
        matrix = vec.fit_transform([*docs, question])
        sims = cosine_similarity(matrix[-1], matrix[:-1])[0]

        order = sims.argsort()[::-1][:top_k]
        cases = []
        for i in order:
            inc = incidents[i]
            cases.append(
                {
                    "incident_id": inc.id,
                    "type": inc.type.value if isinstance(inc.type, IncidentType) else str(inc.type),
                    "content": docs[i],
                    "score": float(sims[i]),
                }
            )

        top = cases[0]
        answer_lines = [
            "[Fallback 모드 — OpenAI 키 없음, TF-IDF 키워드 유사도 매칭]",
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
            answer_lines.append("")
            answer_lines.append(f"추가 후보: {len(cases) - 1}건 더 있음 (아래 카드 참조)")
        return {
            "mode": "fallback",
            "answer": "\n".join(answer_lines),
            "similar_cases": cases,
        }

    def ask(self, question: str, top_k: int = 3) -> dict:
        self._ensure_deps()
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        os.environ["OPENAI_API_KEY"] = self.settings.openai_api_key
        from langchain_community.vectorstores import FAISS
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        if not INDEX_DIR.exists():
            raise RuntimeError("FAISS index not built yet — call reindex() first")
        emb = OpenAIEmbeddings(model=self.settings.embedding_model)
        store = FAISS.load_local(str(INDEX_DIR), emb, allow_dangerous_deserialization=True)
        hits = store.similarity_search_with_score(question, k=top_k)
        cases = [
            {
                "incident_id": h.metadata.get("incident_id"),
                "type": h.metadata.get("type"),
                "content": h.page_content,
                "score": float(score),
            }
            for h, score in hits
        ]

        llm = ChatOpenAI(model=self.settings.llm_model, temperature=0.2)
        context = "\n\n---\n\n".join(c["content"] for c in cases)
        prompt = (
            "당신은 보험사 인터페이스 운영 어시스턴트입니다. 아래 과거 장애 사례를 참고하여 "
            "신규 장애의 원인 가설과 권장 조치를 한국어로 제시하세요.\n\n"
            f"[과거 사례]\n{context}\n\n[신규 질문]\n{question}\n\n"
            "응답 형식:\n1) 원인 가설 (Top-1)\n2) 추가 점검 항목\n3) 권장 조치 절차"
        )
        answer = llm.invoke(prompt).content
        return {"mode": "llm", "answer": answer, "similar_cases": cases}
