from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.ai.anomaly import score_anomalies
from app.services.ai.rag import RagService, llm_status

router = APIRouter(prefix="/ai", tags=["ai"])


class AskRequest(BaseModel):
    question: str
    interface_id: int | None = None
    top_k: int = 3


class AskResponse(BaseModel):
    answer: str
    similar_cases: list[dict]
    mode: str = "llm"  # "llm" | "fallback"
    provider: str | None = None
    model: str | None = None


class AnomalyResponse(BaseModel):
    interface_id: int
    score: float
    is_anomaly: bool


@router.get("/status")
def status() -> dict:
    """현재 활성 LLM 프로바이더/모델 정보. 프론트가 화면에 표시."""
    return llm_status()


@router.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest, db: Session = Depends(get_db)) -> AskResponse:
    rag = RagService(db)
    return AskResponse(**(await rag.ask(payload.question, top_k=payload.top_k)))


@router.get("/anomaly/{interface_id}", response_model=AnomalyResponse)
def anomaly(interface_id: int, db: Session = Depends(get_db)) -> AnomalyResponse:
    score, is_anom = score_anomalies(db, interface_id)
    return AnomalyResponse(interface_id=interface_id, score=score, is_anomaly=is_anom)
