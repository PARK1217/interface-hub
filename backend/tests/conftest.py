"""공용 픽스처 — sqlite 메모리 DB (CI 와 같은 조건)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def sqlite_db(monkeypatch):
    """모든 테이블을 가진 sqlite 메모리 DB. 도구·기록이 쓰는 SessionLocal 을 이것으로 바꿔 끼움."""
    from app import models  # noqa: F401
    from app.agents import tools
    from app.core import database

    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(tools, "SESSION_FACTORY", factory)
    yield factory
    engine.dispose()
