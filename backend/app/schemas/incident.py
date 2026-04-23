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