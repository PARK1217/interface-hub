from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.call_log import CallStatus


class CallLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    interface_id: int
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    status: CallStatus
    http_status: int | None = None
    duration_ms: int
    error_message: str | None = None
    triggered_by: str
    called_at: datetime


class CallLogStats(BaseModel):
    total: int
    success: int
    failure: int
    avg_duration_ms: float
    success_rate: float


class TimeSeriesPoint(BaseModel):
    bucket: datetime
    total: int
    success: int
    failure: int
    avg_duration_ms: float