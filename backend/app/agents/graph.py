"""에이전트 연결 (LangGraph) 과 실행 함수.

  START → guard ─┬→ finalize (가드에 걸림: 짧은 질문 / 24시간 실패 재사용 / 캐시 적중)
                 └→ router ─┬→ stats_agent ──→ finalize
                            ├→ config_agent ─→ finalize
                            ├→ case_agent ───→ finalize
                            └→ rca_agent ─┬──→ finalize
                                          └→ clarify → finalize      (대상 불명 → 되묻기)
  /ai/rca (장애 화면) 은 entry=incident 로 router 에서 시작 (실시간 데이터라 가드·캐시 건너뜀)
  finalize → END
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from app.agents.nodes import (
    after_guard,
    after_rca,
    after_router,
    case_agent,
    clarify_node,
    config_agent,
    finalize_node,
    guard_node,
    rca_agent,
    router_node,
    run_status,
    stats_agent,
)
from app.agents.state import AgentState
from app.agents.tracing import Tracer
from app.core.config import get_settings

log = logging.getLogger("noahub.agents.graph")

AGENT_NODES = ("stats_agent", "config_agent", "case_agent", "rca_agent")

_graphs: dict[str, object] = {}


def build_graph(entry: str = "chat"):
    g = StateGraph(AgentState)
    g.add_node("guard", guard_node)
    g.add_node("router", router_node)
    g.add_node("stats_agent", stats_agent)
    g.add_node("config_agent", config_agent)
    g.add_node("case_agent", case_agent)
    g.add_node("rca_agent", rca_agent)
    g.add_node("clarify", clarify_node)
    g.add_node("finalize", finalize_node)

    if entry == "incident":
        g.add_edge(START, "router")
    else:
        g.add_edge(START, "guard")
        g.add_conditional_edges("guard", after_guard, {"router": "router", "finalize": "finalize"})
    g.add_conditional_edges("router", after_router, {n: n for n in AGENT_NODES})
    for n in ("stats_agent", "config_agent", "case_agent"):
        g.add_edge(n, "finalize")
    g.add_conditional_edges("rca_agent", after_rca, {"clarify": "clarify", "finalize": "finalize"})
    g.add_edge("clarify", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def graph(entry: str = "chat"):
    if entry not in _graphs:
        _graphs[entry] = build_graph(entry)
    return _graphs[entry]


def mermaid() -> str:
    """README·백오피스용 구성도 (채팅 진입 그래프)."""
    return graph("chat").get_graph().draw_mermaid()


async def _run(state: AgentState) -> dict:
    tracer: Tracer = state["tracer"]
    await tracer.open()
    try:
        out = await graph(state["entry"]).ainvoke(state, config={"recursion_limit": get_settings().agent_recursion_limit})
    except Exception as e:
        await tracer.close(route=None, status="error", outcome=None, error=f"{type(e).__name__}: {e}")
        raise
    res = out["response"]
    await tracer.close(route=out.get("route"), status=run_status(res), outcome=res.get("outcome"))
    return res


async def run_ask(question: str, *, top_k: int = 3, actor_user_id: int | None = None,
                  actor_username: str | None = None) -> dict:
    """채팅 질의 (/ai/ask)."""
    tracer = Tracer(entry="chat", user_id=actor_user_id, request=question)
    return await _run({"entry": "chat", "question": question, "top_k": top_k, "incident_id": None,
                       "actor_user_id": actor_user_id, "actor_username": actor_username, "tracer": tracer})


async def run_rca(incident_id: int, *, actor_user_id: int | None = None, actor_username: str | None = None) -> dict:
    """장애 화면 "원인 분석" (/ai/rca)."""
    question = f"장애 #{incident_id} 원인 분석"
    tracer = Tracer(entry="incident", user_id=actor_user_id, request=question)
    return await _run({"entry": "incident", "question": question, "top_k": 3, "incident_id": incident_id,
                       "actor_user_id": actor_user_id, "actor_username": actor_username, "tracer": tracer,
                       "qhash": "", "rule_intent": "root_cause"})
