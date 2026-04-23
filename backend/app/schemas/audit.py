from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_user_id: int | None = None
    actor_username: str
    actor_role: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    before_value: dict[str, Any] | None = None
    after_value: dict[str, Any] | None = None
    ip: str | None = None
    user_agent: str | None = None
    occurred_at: datetime