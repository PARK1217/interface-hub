"""LangGraph 상태."""

from __future__ import annotations

from typing import Any, TypedDict

from app.agents.tracing import StepRecord, Tracer


class AgentState(TypedDict, total=False):
    # 입력
    entry: str                     # chat | incident
    question: str
    top_k: int
    incident_id: int | None
    actor_user_id: int | None
    actor_username: str | None
    tracer: Tracer
    # 가드
    rule_intent: str
    qhash: str
    provider: str
    model: str | None
    guard: str | None              # short_question | repeat_failure | cache — 걸린 가드
    hit_cache: bool
    # 라우터
    intent: str
    route: str                     # 일한 에이전트 id (guard 에 걸리면 "guard")
    route_method: str
    router_step: StepRecord
    # 결과
    response: dict[str, Any]
    cacheable: bool                # LLM 성공 답만 캐시 (전환 전과 같음)
    error: str | None
