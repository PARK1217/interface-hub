"""에이전트 실행 기록 (백오피스) — 요청 목록 · 실행 흐름 · 단계별 펼쳐 보기 · 에이전트/모델별 사용량.

권한: 감사 로그와 같음 (ADMIN · VIEWER). 다른 사용자의 질문 요약이 보이기 때문에 OPERATOR 는 제외.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.core.time import now_kst
from app.models import AgentRun, AgentStep, UserRole

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])

_VIEW_ROLES = [UserRole.ADMIN, UserRole.VIEWER]

# 그래프 노드 → 한국어 이름 (README·화면 공용)
NODE_LABELS = {
    "guard": "앞단 가드",
    "router": "안내 에이전트",
    "stats_agent": "통계 분석",
    "config_agent": "설정 점검",
    "case_agent": "장애 사례 검색",
    "rca_agent": "장애 원인 분석",
    "clarify": "되묻기",
    "finalize": "마무리",
}


class RunOut(BaseModel):
    run_id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    entry: str
    user_ref: str | None
    request_summary: str | None
    route: str | None
    status: str
    outcome: str | None
    total_input_tokens: int
    total_output_tokens: int
    total_cost_krw: Decimal
    duration_ms: int | None
    llm_calls: int = 0
    unpriced_llm_calls: int = 0   # 토큰은 있는데 단가·환율이 없어 비용 NULL 인 호출 수 → "단가 확인 필요"
    error: str | None


class StepOut(BaseModel):
    step_id: uuid.UUID
    parent_step_id: uuid.UUID | None
    seq: int
    kind: str
    name: str
    agent_id: str | None
    model: str | None
    input_summary: str | None
    output_summary: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_krw: Decimal | None
    latency_ms: int | None
    started_at: datetime
    error: str | None


class RunDetail(BaseModel):
    run: RunOut
    steps: list[StepOut]
    path: list[str]               # 지나간 그래프 노드 (순서대로)


class UsageRow(BaseModel):
    agent_id: str | None
    model: str | None
    calls: int
    input_tokens: int
    output_tokens: int
    cost_krw: Decimal | None      # 단가 있는 호출만 합산. 전부 없으면 None
    unpriced_calls: int


class UsageOut(BaseModel):
    period: str
    since: datetime
    runs: int
    rows: list[UsageRow]
    total_input_tokens: int
    total_output_tokens: int
    total_cost_krw: Decimal


def _llm_counts(db: Session, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    if not run_ids:
        return {}
    rows = db.execute(
        select(
            AgentStep.run_id,
            func.count(AgentStep.step_id),
            func.sum(case(((AgentStep.cost_krw.is_(None)) & (AgentStep.input_tokens.is_not(None)), 1), else_=0)),
        )
        .where(AgentStep.run_id.in_(run_ids))
        .where(AgentStep.kind == "llm")
        .group_by(AgentStep.run_id)
    ).all()
    return {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}


def _run_out(r: AgentRun, counts: dict[uuid.UUID, tuple[int, int]]) -> RunOut:
    dur = int((r.finished_at - r.started_at).total_seconds() * 1000) if r.finished_at and r.started_at else None
    calls, unpriced = counts.get(r.run_id, (0, 0))
    return RunOut(
        run_id=r.run_id, started_at=r.started_at, finished_at=r.finished_at, entry=r.entry, user_ref=r.user_ref,
        request_summary=r.request_summary, route=r.route, status=r.status, outcome=r.outcome,
        total_input_tokens=r.total_input_tokens, total_output_tokens=r.total_output_tokens,
        total_cost_krw=r.total_cost_krw, duration_ms=dur, llm_calls=calls, unpriced_llm_calls=unpriced,
        error=r.error,
    )


@router.get("", response_model=list[RunOut])
def list_runs(
    route: str | None = None,
    status: str | None = None,
    entry: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(require_role(_VIEW_ROLES)),
) -> list[RunOut]:
    """요청 목록 (최근 순)."""
    stmt = select(AgentRun)
    if route:
        stmt = stmt.where(AgentRun.route == route)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    if entry:
        stmt = stmt.where(AgentRun.entry == entry)
    runs = db.scalars(stmt.order_by(desc(AgentRun.started_at)).limit(limit).offset(offset)).all()
    counts = _llm_counts(db, [r.run_id for r in runs])
    return [_run_out(r, counts) for r in runs]


@router.get("/usage", response_model=UsageOut)
def usage(
    period: Literal["today", "7d", "30d"] = "7d",
    db: Session = Depends(get_db),
    _=Depends(require_role(_VIEW_ROLES)),
) -> UsageOut:
    """에이전트별·모델별 LLM 토큰·비용 합계 (오늘 / 7일 / 30일)."""
    now = now_kst()
    since = now.replace(hour=0, minute=0, second=0, microsecond=0) if period == "today" else (
        now - timedelta(days=7 if period == "7d" else 30))
    unpriced = func.sum(case(((AgentStep.cost_krw.is_(None)) & (AgentStep.input_tokens.is_not(None)), 1), else_=0))
    rows = db.execute(
        select(
            AgentStep.agent_id, AgentStep.model, func.count(AgentStep.step_id),
            func.coalesce(func.sum(AgentStep.input_tokens), 0), func.coalesce(func.sum(AgentStep.output_tokens), 0),
            func.sum(AgentStep.cost_krw), unpriced,
        )
        .where(AgentStep.kind == "llm")
        .where(AgentStep.started_at >= since)
        .group_by(AgentStep.agent_id, AgentStep.model)
        .order_by(desc(func.coalesce(func.sum(AgentStep.input_tokens), 0)))
    ).all()
    out = [UsageRow(agent_id=r[0], model=r[1], calls=int(r[2]), input_tokens=int(r[3]), output_tokens=int(r[4]),
                    cost_krw=r[5], unpriced_calls=int(r[6] or 0)) for r in rows]
    runs = db.scalar(select(func.count(AgentRun.run_id)).where(AgentRun.started_at >= since)) or 0
    return UsageOut(
        period=period, since=since, runs=int(runs), rows=out,
        total_input_tokens=sum(r.input_tokens for r in out), total_output_tokens=sum(r.output_tokens for r in out),
        total_cost_krw=sum((r.cost_krw for r in out if r.cost_krw is not None), Decimal("0")),
    )


def mermaid_ko() -> str:
    """LangGraph draw_mermaid() 결과에 한국어 노드 이름을 붙인 것."""
    from app.agents.graph import mermaid

    text = mermaid()
    for node, label in NODE_LABELS.items():
        text = text.replace(f"\t{node}({node})", f"\t{node}({label})")
    return text.replace("<p>__start__</p>", "<p>요청</p>").replace("<p>__end__</p>", "<p>응답</p>")


@router.get("/graph")
def graph_mermaid(_=Depends(require_role(_VIEW_ROLES))) -> dict:
    """에이전트 구성도 (mermaid) + 노드 이름."""
    return {"mermaid": mermaid_ko(), "labels": NODE_LABELS}


@router.get("/{run_id}", response_model=RunDetail)
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    _=Depends(require_role(_VIEW_ROLES)),
) -> RunDetail:
    """실행 흐름 + 단계별 펼쳐 보기."""
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    steps = db.scalars(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq)).all()
    path: list[str] = ["__start__"]
    for s in steps:
        node = {"guard": "guard", "router": "router", "finalize": "finalize"}.get(s.kind)
        if s.kind == "agent":
            node = s.name
        if node and node not in path:
            path.append(node)
    path.append("__end__")
    counts = _llm_counts(db, [run.run_id])
    return RunDetail(run=_run_out(run, counts), steps=[StepOut.model_validate(s, from_attributes=True) for s in steps],
                     path=path)
