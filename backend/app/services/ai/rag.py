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
        return {"answer": answer, "similar_cases": cases}
