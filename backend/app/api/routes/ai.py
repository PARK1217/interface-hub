from __future__ import annotations

import random
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.time import now_kst
from app.models import AiQueryLog, Incident, Interface, User
from app.services.ai.anomaly import score_anomalies
from app.services.ai.rag import RagService, llm_status

router = APIRouter(prefix="/ai", tags=["ai"])


class AskRequest(BaseModel):
    question: str
    interface_id: int | None = None
    top_k: int = 3


class LLMErrorOut(BaseModel):
    """LLM 호출 실패 사유 — 사용자에게 표시 가능한 형태."""
    kind: str
    provider: str
    message: str
    status: int | None = None
    body_excerpt: str | None = None


class AskResponse(BaseModel):
    answer: str
    similar_cases: list[dict]
    mode: str = "llm"  # "llm" | "fallback"
    provider: str | None = None
    model: str | None = None
    llm_error: LLMErrorOut | None = None
    # Phase B.8 — 캐시 hit 여부 (UI ⚡ 칩 표시용)
    cached: bool = False


class AnomalyResponse(BaseModel):
    interface_id: int
    score: float
    is_anomaly: bool


class PopularQuestion(BaseModel):
    question: str            # 대표 질문 (그룹 내 가장 최근 1건의 원문)
    count: int               # 해당 hash 의 호출 횟수
    last_asked_at: str       # ISO 시각


class HistoryItem(BaseModel):
    id: int
    question: str
    mode: str
    provider: str | None
    cached: bool
    llm_error_kind: str | None
    response_excerpt: str | None
    asked_at: str


class SuggestionItem(BaseModel):
    text: str                # 자연어 prompt 본문
    source: str              # "popular" | "interface_template" | "incident_recent"
    interface_id: int | None = None


@router.get("/status")
def status() -> dict:
    """현재 활성 LLM 프로바이더/모델 정보. 프론트가 화면에 표시."""
    return llm_status()


@router.post("/ask", response_model=AskResponse)
async def ask(
    payload: AskRequest,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> AskResponse:
    """질의 — Phase B.8 부터 인증 필수 (질의 로그에 actor 기록).

    캐시 hit 시 LLM 호출 / TF-IDF 검색 모두 스킵하고 즉시 반환.
    """
    rag = RagService(db)
    return AskResponse(**(await rag.ask(payload.question, top_k=payload.top_k, actor=current)))


@router.get("/anomaly/{interface_id}", response_model=AnomalyResponse)
def anomaly(interface_id: int, db: Session = Depends(get_db)) -> AnomalyResponse:
    score, is_anom = score_anomalies(db, interface_id)
    return AnomalyResponse(interface_id=interface_id, score=score, is_anomaly=is_anom)


# ============================================================================
# Phase B.8 — Popular / History / Suggestions
# ============================================================================

@router.get("/popular-questions", response_model=list[PopularQuestion])
def popular_questions(
    days: int = Query(default=14, ge=1, le=180),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PopularQuestion]:
    """최근 N일간 가장 많이 호출된 질문 Top K (Phase B.8.3).

    question_hash 그룹화 (정규화 trim+lower+공백 단일화 적용된 키) — "보험개발원
    5xx ?" / " 보험개발원   5xx? " 는 같은 그룹.
    """
    since = now_kst() - timedelta(days=days)
    rows = db.execute(
        select(
            AiQueryLog.question_hash,
            func.count(AiQueryLog.id).label("cnt"),
            func.max(AiQueryLog.asked_at).label("last_asked"),
        )
        .where(AiQueryLog.asked_at >= since)
        .group_by(AiQueryLog.question_hash)
        .order_by(desc("cnt"))
        .limit(limit)
    ).all()
    if not rows:
        return []
    # 각 hash 의 대표 질문 (가장 최근 1건의 원문) 가져오기
    hashes = [r.question_hash for r in rows]
    last_q = {
        h: db.scalar(
            select(AiQueryLog.question)
            .where(AiQueryLog.question_hash == h)
            .order_by(desc(AiQueryLog.asked_at))
            .limit(1)
        )
        for h in hashes
    }
    return [
        PopularQuestion(
            question=last_q.get(r.question_hash) or "",
            count=r.cnt,
            last_asked_at=r.last_asked.isoformat() if r.last_asked else "",
        )
        for r in rows
    ]


@router.get("/my-history", response_model=list[HistoryItem])
def my_history(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[HistoryItem]:
    """본인의 최근 질의 이력 (Phase B.8.6). 클릭 시 prompt 재사용."""
    rows = db.scalars(
        select(AiQueryLog)
        .where(AiQueryLog.actor_user_id == current.id)
        .order_by(desc(AiQueryLog.asked_at))
        .limit(limit)
    ).all()
    return [
        HistoryItem(
            id=r.id,
            question=r.question,
            mode=r.mode,
            provider=r.provider,
            cached=r.hit_cache,
            llm_error_kind=r.llm_error_kind,
            response_excerpt=r.response_excerpt,
            asked_at=r.asked_at.isoformat() if r.asked_at else "",
        )
        for r in rows
    ]


@router.get("/suggestions", response_model=list[SuggestionItem])
def suggestions(
    limit: int = Query(default=4, ge=1, le=10),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SuggestionItem]:
    """프롬프트 추천 (Phase B.8.5).

    우선순위:
      1. **popular** — 최근 14일 자주 묻는 질문 (있으면)
      2. **incident_recent** — 최근 미해결 incident 기반 자연어 prompt
      3. **interface_template** — 등록된 인터페이스 무작위 선정 + 템플릿

    AI 페이지 chip 영역 / 빈 상태 모두에서 사용. "데이터 없으면 인터페이스
    설정으로 만들어달라" 요구사항 (Phase B.8) 충족.
    """
    out: list[SuggestionItem] = []

    # 1) popular — 최근 14일 Top
    since = now_kst() - timedelta(days=14)
    pop = db.execute(
        select(
            AiQueryLog.question_hash,
            func.count(AiQueryLog.id).label("cnt"),
        )
        .where(AiQueryLog.asked_at >= since)
        .group_by(AiQueryLog.question_hash)
        .order_by(desc("cnt"))
        .limit(limit)
    ).all()
    for r in pop:
        last_q = db.scalar(
            select(AiQueryLog.question)
            .where(AiQueryLog.question_hash == r.question_hash)
            .order_by(desc(AiQueryLog.asked_at))
            .limit(1)
        )
        if last_q:
            out.append(SuggestionItem(text=last_q, source="popular"))
            if len(out) >= limit:
                return out

    # 2) 최근 미해결 incident 기반
    open_incidents = db.scalars(
        select(Incident)
        .where(Incident.resolved_at.is_(None))
        .order_by(desc(Incident.detected_at))
        .limit(limit)
    ).all()
    for inc in open_incidents:
        itf = db.get(Interface, inc.interface_id)
        if not itf:
            continue
        text = (
            f"{itf.name} 에서 {inc.type.value if hasattr(inc.type, 'value') else inc.type} "
            f"장애가 발생 중이야 ({inc.summary or '상세 없음'}). 원인 분석과 권장 조치 알려줘."
        )
        out.append(SuggestionItem(text=text, source="incident_recent", interface_id=itf.id))
        if len(out) >= limit:
            return out

    # 3) 인터페이스 템플릿 — 활성 인터페이스 무작위
    if len(out) < limit:
        active_itfs = db.scalars(
            select(Interface)
            .where(Interface.deleted_at.is_(None))
            .where(Interface.enabled.is_(True))
        ).all()
        random.shuffle(active_itfs)
        templates = [
            "{name} ({org}) 응답 시간이 평소보다 느려졌어. 원인 후보 짚어줘.",
            "{name} 호출이 {http} 에러로 막혀. 어떤 점검부터 하지?",
            "{name} 의 최근 SLA 미달 원인이 뭘까?",
        ]
        for itf in active_itfs:
            if len(out) >= limit:
                break
            tmpl = random.choice(templates)
            text = tmpl.format(
                name=itf.name,
                org=itf.organization or "외부 기관",
                http=random.choice(["401", "5xx", "Timeout"]),
            )
            out.append(SuggestionItem(text=text, source="interface_template", interface_id=itf.id))

    return out