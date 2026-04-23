from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.services.ai.anomaly import score_anomalies
from app.services.ai.rag import RagService

router = APIRouter(prefix="/ai", tags=["ai"])


class AskRequest(BaseModel):
    question: str
    interface_id: int | None = None
    top_k: int = 3


class AskResponse(BaseModel):
    answer: str
    similar_cases: list[dict]
    mode: str = "llm"  # "llm" | "fallback"


class AnomalyResponse(BaseModel):
    interface_id: int
    score: float
    is_anomaly: bool


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_db)) -> AskResponse:
    rag = RagService(db)
    # No key → straight to fallback. Key present but real RAG raises (no index, etc.) → also fall back.
    if not get_settings().openai_api_key:
        return AskResponse(**rag.ask_fallback(payload.question, top_k=payload.top_k))
    try:
        return AskResponse(**rag.ask(payload.question, top_k=payload.top_k))
    except RuntimeError:
        return AskResponse(**rag.ask_fallback(payload.question, top_k=payload.top_k))


@router.get("/anomaly/{interface_id}", response_model=AnomalyResponse)
def anomaly(interface_id: int, db: Session = Depends(get_db)) -> AnomalyResponse:
    score, is_anom = score_anomalies(db, interface_id)
    return AnomalyResponse(interface_id=interface_id, score=score, is_anomaly=is_anom)
