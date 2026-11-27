"""기동 안전장치 — 필수 컬럼 확인, 빈 환경 변수, 원인 분석 권한."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.api import deps
from app.api.routes import ai
from app.core.config import Settings
from app.core.database import Base, verify_required_columns
from app.models import User, UserRole


def test_missing_run_id_column_stops_startup():
    from app import models  # noqa: F401

    engine = create_engine("sqlite+pysqlite://")
    with engine.begin() as conn:  # 마이그레이션이 실패해 run_id 가 없는 옛 테이블
        conn.execute(text("CREATE TABLE ai_query_logs (id INTEGER PRIMARY KEY, question TEXT)"))
    with pytest.raises(RuntimeError, match="ai_query_logs.run_id"):
        verify_required_columns(engine)

    ok = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(ok)
    verify_required_columns(ok)  # 정상 스키마는 통과


def test_blank_env_values_use_defaults(monkeypatch):
    for name in ("AGENT_MAX_STEPS", "AGENT_RECURSION_LIMIT", "USD_KRW_RATE", "ROUTER_LLM_FALLBACK"):
        monkeypatch.setenv(name, "")
    s = Settings()
    assert (s.agent_max_steps, s.agent_recursion_limit, s.usd_krw_rate, s.router_llm_fallback) == (8, 25, None, False)


def test_rca_requires_operator():
    app = FastAPI()
    app.include_router(ai.router, prefix="/api")
    app.dependency_overrides[deps.get_current_user] = lambda: User(id=1, username="v", role=UserRole.VIEWER)
    app.dependency_overrides[deps.get_db] = lambda: None
    r = TestClient(app).post("/api/ai/rca", json={"incident_id": 1})
    assert r.status_code == 403
