from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VectorCase(Base):
    """Pointer row for FAISS-indexed incident cases.

    The actual embeddings live in the FAISS index file on disk; this row keeps
    the metadata that the RAG pipeline needs to render an answer.
    """

    __tablename__ = "vector_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), unique=True, index=True
    )
    faiss_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    summary: Mapped[str] = mapped_column(String(255))
    resolution_text: Mapped[str | None] = mapped_column(Text, default=None)
    embedding_model: Mapped[str] = mapped_column(String(64), default="text-embedding-3-small")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())