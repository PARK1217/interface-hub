from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SlaTargetBase(BaseModel):
    uptime_target: float = Field(99.0, ge=0.0, le=100.0)
    response_ms_target: int = Field(2000, ge=1)


class SlaTargetCreate(SlaTargetBase):
    interface_id: int


class SlaTargetUpdate(BaseModel):
    uptime_target: float | None = Field(default=None, ge=0.0, le=100.0)
    response_ms_target: int | None = Field(default=None, ge=1)


class SlaTargetOut(SlaTargetBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    interface_id: int
    created_at: datetime
    updated_at: datetime


class SlaReportRow(BaseModel):
    interface_id: int
    interface_name: str
    uptime_pct: float
    avg_response_ms: float
    target_uptime: float
    target_response_ms: int
    meets_uptime: bool
    meets_response: bool