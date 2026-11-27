"""config/agents.yaml 로더. 코드는 에이전트 역할·도구·한도를 이 파일에서만 읽습니다."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else BACKEND_ROOT / path


@lru_cache
def load_agent_config() -> dict[str, Any]:
    with resolve_path(get_settings().agent_config_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def router_config() -> dict[str, Any]:
    return load_agent_config()["router"]


def agent_config(agent_id: str) -> dict[str, Any]:
    cfg = load_agent_config()
    if agent_id == cfg["router"]["id"]:
        return cfg["router"]
    for a in cfg["agents"]:
        if a["id"] == agent_id:
            return a
    raise KeyError(f"agents.yaml 에 없는 에이전트: {agent_id}")


def agent_ids() -> list[str]:
    return [a["id"] for a in load_agent_config()["agents"]]


def agent_tools(agent_id: str) -> list[str]:
    return list(agent_config(agent_id).get("tools") or [])


def forbidden_tools() -> list[str]:
    return list(load_agent_config().get("forbidden_tools") or [])


def max_steps(agent_id: str) -> int:
    return int(agent_config(agent_id).get("max_steps") or get_settings().agent_max_steps)
