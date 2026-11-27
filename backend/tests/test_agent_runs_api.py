"""백오피스 API — 권한(ADMIN·VIEWER 만), 목록·상세·사용량."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.routes import agent_runs
from app.core.time import now_kst
from app.models import AgentRun, AgentStep, User, UserRole


@pytest.fixture
def client_as(sqlite_db):
    run_id = uuid.uuid4()
    agent_step = uuid.uuid4()
    with sqlite_db() as db:
        db.add(AgentRun(run_id=run_id, entry="chat", request_summary="지금 장애 잦은 인터페이스", route="stats_agent",
                        status="ok", outcome="success", total_input_tokens=100, total_output_tokens=20,
                        total_cost_krw=Decimal("0"), started_at=now_kst(), finished_at=now_kst()))
        db.flush()
        db.add(AgentStep(step_id=uuid.uuid4(), run_id=run_id, seq=1, kind="router", name="router", agent_id="router",
                         started_at=now_kst()))
        db.add(AgentStep(step_id=agent_step, run_id=run_id, seq=2, kind="agent", name="stats_agent",
                         agent_id="stats_agent", started_at=now_kst()))
        db.flush()
        db.add(AgentStep(step_id=uuid.uuid4(), run_id=run_id, parent_step_id=agent_step, seq=3, kind="llm",
                         name="openai:gpt-4o-mini", agent_id="stats_agent", model="gpt-4o-mini", input_tokens=100,
                         output_tokens=20, cost_krw=None, started_at=now_kst()))
        db.commit()

    def make(role: UserRole) -> TestClient:
        app = FastAPI()
        app.include_router(agent_runs.router, prefix="/api")

        def _db():
            db = sqlite_db()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[deps.get_db] = _db
        app.dependency_overrides[deps.get_current_user] = lambda: User(id=1, username="u", role=role)
        c = TestClient(app)
        c.run_id = str(run_id)  # type: ignore[attr-defined]
        return c

    return make


def test_operator_is_blocked(client_as):
    c = client_as(UserRole.OPERATOR)
    assert c.get("/api/agent-runs").status_code == 403


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.VIEWER])
def test_list_detail_usage(client_as, role):
    c = client_as(role)
    runs = c.get("/api/agent-runs").json()
    assert runs[0]["route"] == "stats_agent" and runs[0]["llm_calls"] == 1 and runs[0]["unpriced_llm_calls"] == 1

    d = c.get(f"/api/agent-runs/{c.run_id}").json()
    assert d["path"] == ["__start__", "router", "stats_agent", "__end__"]
    assert [s["kind"] for s in d["steps"]] == ["router", "agent", "llm"]

    u = c.get("/api/agent-runs/usage", params={"period": "today"}).json()
    row = u["rows"][0]
    assert (row["agent_id"], row["model"], row["input_tokens"], row["unpriced_calls"]) == ("stats_agent", "gpt-4o-mini", 100, 1)
    assert row["cost_krw"] is None  # 단가를 모르면 비우고 화면에서 "단가 확인 필요"

    g = c.get("/api/agent-runs/graph").json()
    assert "안내 에이전트" in g["mermaid"] and g["labels"]["rca_agent"] == "장애 원인 분석"
