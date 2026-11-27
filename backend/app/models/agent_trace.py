"""에이전트 실행 기록 — agent_runs(요청 1건) / agent_steps(단계).

양식: 에이전트 전환 기준의 agent_trace_schema.sql
- 양식에 더한 컬럼: agent_runs.outcome (기존 ai_query_logs.outcome 값 그대로 — success / no_match / ...)
- 기존 ai_query_logs 는 ai_query_logs.run_id 로 이 테이블과 연결 (지우거나 바꾸지 않음)
- 요약 컬럼에는 가린 요약만 저장 (app/agents/tracing.redact)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    entry: Mapped[str] = mapped_column(String(20), nullable=False)          # chat | incident
    user_ref: Mapped[str | None] = mapped_column(String(40), default=None)  # 가린 값 (u_xxxxxxxx)
    request_summary: Mapped[str | None] = mapped_column(Text, default=None)
    route: Mapped[str | None] = mapped_column(String(40), default=None, index=True)
    # running | ok | handoff | refused | error
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running", index=True)
    outcome: Mapped[str | None] = mapped_column(String(30), default=None)
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_krw: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (Index("idx_agent_runs_started", started_at.desc()),)


class AgentStep(Base):
    __tablename__ = "agent_steps"

    step_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    parent_step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_steps.step_id"), default=None
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)   # guard | router | agent | tool | llm | finalize
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(40), default=None)
    model: Mapped[str | None] = mapped_column(String(120), default=None)
    input_summary: Mapped[str | None] = mapped_column(Text, default=None)
    output_summary: Mapped[str | None] = mapped_column(Text, default=None)
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)   # API 응답 usage 그대로
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    cost_krw: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), default=None)  # 단가·환율 없으면 NULL
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        Index("idx_agent_steps_run", "run_id", "seq"),
        Index("idx_agent_steps_agent", "agent_id", started_at.desc()),
    )
