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
from app.services.ai.rag import NO_MATCH_THRESHOLD, RagService, llm_status, score_questions

router = APIRouter(prefix="/ai", tags=["ai"])


class AskRequest(BaseModel):
    question: str
    interface_id: int | None = None
    top_k: int = 3


class LLMErrorOut(BaseModel):
    """LLM 호출 단계 실패 사유 (auth_failed / rate_limited / timeout / ...)."""
    kind: str
    provider: str
    title: str
    detail: str
    suggestion: str
    message: str  # 레거시 — detail+suggestion 합본
    status: int | None = None
    body_excerpt: str | None = None


class LLMAttemptOut(BaseModel):
    """Fallback 체인 시도 중 실패한 프로바이더 기록."""
    provider: str
    kind: str
    status: int | None = None
    title: str


class AnalysisNoteOut(BaseModel):
    """검색/분석 단계 사유 (no_history / no_match / empty_question / scikit_missing).

    LLM 호출과 무관 — "LLM 은 잘 됐는데 분석할 데이터가 없다" 같은 경우.
    """
    kind: str
    title: str
    detail: str
    suggestion: str


class AskResponse(BaseModel):
    answer: str
    similar_cases: list[dict]
    mode: str = "llm"  # "llm" | "fallback"
    provider: str | None = None
    model: str | None = None
    llm_error: LLMErrorOut | None = None
    # 체인 순회 중 실패했던 프로바이더들 (성공해도 여기 담길 수 있음).
    llm_attempts: list[LLMAttemptOut] = []
    analysis_note: AnalysisNoteOut | None = None
    # 질문 의도 분류 (UI 칩 표시 + 동작 분기)
    intent: str = "general"  # "stats_query" | "case_lookup" | "config_query" | "general"
    # 분석 성공/실패 유형 (popular/history/suggestions 필터)
    outcome: str = "success"
    # 최근 24h 내 같은 질문이 이미 실패로 판정돼 재호출 없이 반환
    repeated_failure: bool = False
    # 캐시 hit 여부 (UI ⚡ 칩 표시용)
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
    outcome: str  # 성공/실패 유형
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
    """질의 — 부터 인증 필수 (질의 로그에 actor 기록).

    캐시 hit 시 LLM 호출 / TF-IDF 검색 모두 스킵하고 즉시 반환.
    """
    rag = RagService(db)
    return AskResponse(**(await rag.ask(payload.question, top_k=payload.top_k, actor=current)))


@router.get("/anomaly/{interface_id}", response_model=AnomalyResponse)
def anomaly(interface_id: int, db: Session = Depends(get_db)) -> AnomalyResponse:
    score, is_anom = score_anomalies(db, interface_id)
    return AnomalyResponse(interface_id=interface_id, score=score, is_anomaly=is_anom)


# ============================================================================
# Popular / History / Suggestions
# ============================================================================

@router.get("/popular-questions", response_model=list[PopularQuestion])
def popular_questions(
    days: int = Query(default=14, ge=1, le=180),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PopularQuestion]:
    """최근 N일간 가장 많이 호출된 질문 Top K.

    question_hash 그룹화 (정규화 trim+lower+공백 단일화 적용된 키) — "보험개발원
    5xx ?" / " 보험개발원   5xx? " 는 같은 그룹.
    """
    since = now_kst() - timedelta(days=days)
    # outcome='success' 만 popular 에 노출 (실패 질문은 통계에서 제외)
    rows = db.execute(
        select(
            AiQueryLog.question_hash,
            func.count(AiQueryLog.id).label("cnt"),
            func.max(AiQueryLog.asked_at).label("last_asked"),
        )
        .where(AiQueryLog.asked_at >= since)
        .where(AiQueryLog.outcome == "success")
        .group_by(AiQueryLog.question_hash)
        .order_by(desc("cnt"))
        .limit(limit)
    ).all()
    if not rows:
        return []
    hashes = [r.question_hash for r in rows]
    last_q = {
        h: db.scalar(
            select(AiQueryLog.question)
            .where(AiQueryLog.question_hash == h)
            .where(AiQueryLog.outcome == "success")
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
    include_failed: bool = Query(default=False, description="실패 (no_match/empty_question 등) 포함 여부"),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[HistoryItem]:
    """본인의 최근 질의 이력. 기본은 성공만."""
    stmt = (
        select(AiQueryLog)
        .where(AiQueryLog.actor_user_id == current.id)
        .order_by(desc(AiQueryLog.asked_at))
        .limit(limit)
    )
    if not include_failed:
        stmt = stmt.where(AiQueryLog.outcome == "success")
    rows = db.scalars(stmt).all()
    return [
        HistoryItem(
            id=r.id,
            question=r.question,
            mode=r.mode,
            provider=r.provider,
            cached=r.hit_cache,
            llm_error_kind=r.llm_error_kind,
            outcome=r.outcome or "success",
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
    """프롬프트 추천.

    우선순위:
      1. **popular** — 최근 14일 자주 묻는 질문 (있으면)
      2. **incident_recent** — 최근 미해결 incident 기반 자연어 prompt
      3. **interface_template** — 등록된 인터페이스 무작위 선정 + 템플릿

    AI 페이지 chip 영역 / 빈 상태 모두에서 사용. "데이터 없으면 인터페이스
    설정으로 만들어달라" 요구사항 충족.
    """
    # 후보를 일단 넉넉히 (limit * 3) 생성해놓고, TF-IDF 사전 매칭으로 임계 미달
    # (no_match 가 날 것들) 을 제거한 뒤 상위 limit 만 반환 —.
    # popular / incident_recent 는 이미 자기 매칭 보장이지만, interface_template 은
    # 랜덤 템플릿이라 매칭 안 되는 chip 이 UI 에 뜨면 사용자 클릭 시 no_match 가 나옴.
    candidates: list[SuggestionItem] = []
    overfetch = max(limit * 3, 12)

    # 1) popular — 최근 14일 성공한 질문만
    since = now_kst() - timedelta(days=14)
    pop = db.execute(
        select(
            AiQueryLog.question_hash,
            func.count(AiQueryLog.id).label("cnt"),
        )
        .where(AiQueryLog.asked_at >= since)
        .where(AiQueryLog.outcome == "success")
        .group_by(AiQueryLog.question_hash)
        .order_by(desc("cnt"))
        .limit(overfetch)
    ).all()
    for r in pop:
        last_q = db.scalar(
            select(AiQueryLog.question)
            .where(AiQueryLog.question_hash == r.question_hash)
            .where(AiQueryLog.outcome == "success")
            .order_by(desc(AiQueryLog.asked_at))
            .limit(1)
        )
        if last_q:
            candidates.append(SuggestionItem(text=last_q, source="popular"))

    # 2) 최근 미해결 incident 기반 — incident summary 를 본문에 포함해 매칭 높음
    open_incidents = db.scalars(
        select(Incident)
        .where(Incident.resolved_at.is_(None))
        .order_by(desc(Incident.detected_at))
        .limit(overfetch)
    ).all()
    for inc in open_incidents:
        itf = db.get(Interface, inc.interface_id)
        if not itf:
            continue
        text = (
            f"{itf.name} 에서 {inc.type.value if hasattr(inc.type, 'value') else inc.type} "
            f"장애가 발생 중이야 ({inc.summary or '상세 없음'}). 원인 분석과 권장 조치 알려줘."
        )
        candidates.append(SuggestionItem(text=text, source="incident_recent", interface_id=itf.id))

    # 3) 인터페이스 템플릿 — 활성 인터페이스 무작위
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
    for itf in active_itfs[:overfetch]:
        tmpl = random.choice(templates)
        text = tmpl.format(
            name=itf.name,
            org=itf.organization or "외부 기관",
            http=random.choice(["401", "5xx", "Timeout"]),
        )
        candidates.append(SuggestionItem(text=text, source="interface_template", interface_id=itf.id))

    if not candidates:
        return []

    # 4) 사전 매칭 — Top-1 유사도 < NO_MATCH_THRESHOLD 면 사용자 클릭 시 no_match
    # 가 날 것이라 제외. popular / incident_recent 는 대부분 임계 통과, interface_template
    # 중 일부만 걸러짐.
    scores = score_questions(db, [c.text for c in candidates])
    kept = [
        (c, s) for c, s in zip(candidates, scores)
        if s >= NO_MATCH_THRESHOLD
    ]
    # 원래 순서 유지 (popular → incident_recent → interface_template 우선)
    return [c for c, _ in kept[:limit]]