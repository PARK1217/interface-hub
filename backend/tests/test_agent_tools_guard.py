"""위험 도구 검사 — 실행·재처리·해결·음소거·시크릿·알림 함수가 어떤 에이전트 도구에도 없어야 함."""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import text

from app.agents import tools
from app.agents.config import agent_ids, agent_tools, forbidden_tools, router_config
from app.models import Interface


def test_every_configured_tool_exists():
    for aid in agent_ids():
        assert tools.tools_for(aid), f"{aid} 에 도구가 없음"


def test_no_forbidden_tool_in_any_agent():
    forbidden = set(forbidden_tools())
    assert "execute_interface" in forbidden and "reveal_secret" in forbidden
    assert not forbidden & set(tools.TOOLS), "금지 함수가 도구로 등록됨"
    for aid in agent_ids():
        assert not forbidden & set(agent_tools(aid)), f"{aid} 도구 목록에 금지 함수"
    assert "tools" not in router_config()


@pytest.mark.parametrize("name", sorted(tools.TOOLS))
def test_tool_source_does_not_call_forbidden(name):
    src = inspect.getsource(tools.TOOLS[name].func)
    for bad in [*forbidden_tools(), ".commit(", ".delete(", "decrypt"]:
        assert bad not in src, f"도구 {name} 이 {bad} 를 부름"


def test_readonly_session_blocks_writes(sqlite_db):
    with tools.readonly_session() as db:
        db.add(Interface(name="x", endpoint="http://x"))
        with pytest.raises(tools.ReadOnlyViolation):
            db.flush()
    with tools.readonly_session() as db:
        with pytest.raises(tools.ReadOnlyViolation):
            db.execute(text("DELETE FROM interfaces"))
        assert db.execute(text("SELECT count(*) FROM interfaces")).scalar() == 0
