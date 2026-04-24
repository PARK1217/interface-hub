from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.incident import IncidentType


class IncidentBase(BaseModel):
    type: IncidentType
    severity: str = "warning"
    summary: str
    root_cause: str | None = None
    resolution: str | None = None


class IncidentCreate(IncidentBase):
    interface_id: int


class IncidentUpdate(BaseModel):
    severity: str | None = None
    root_cause: str | None = None
    resolution: str | None = None
    resolved_at: datetime | None = None


class IncidentOut(IncidentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    interface_id: int
    detected_at: datetime
    resolved_at: datetime | None = None
    related_log_count: int = 0
    # 묶인 call_logs 중 가장 최근 발생 시각. detected_at (첫 감지) 와 함께 보여주면
    # "지금도 발생 중인지" / "한 번만 발생하고 끝났는지" 운영자가 바로 판단 가능.
    last_call_at: datetime | None = None