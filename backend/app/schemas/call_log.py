from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    error_type: str | None = None
    error_trace: str | None = None
    triggered_by: str
    called_at: datetime
    parent_log_id: int | None = None
    retry_count: int = 0
    is_reprocessed: bool = False


class BulkRetryRequest(BaseModel):
    interface_id: int | None = None
    status: CallStatus | None = None
    since: datetime | None = None
    until: datetime | None = None
    only_failed: bool = True
    skip_already_reprocessed: bool = True
    max_count: int = Field(50, ge=1, le=500)


class BulkRetryResponse(BaseModel):
    submitted: int
    skipped: int
    new_log_ids: list[int]


class IngestRequest(BaseModel):
    """Payload from external systems reporting their own external-call result."""

    interface_id: int
    status: CallStatus
    duration_ms: int = Field(..., ge=0)
    http_status: int | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    error_message: str | None = None
    error_type: str | None = None
    error_trace: str | None = None
    called_at: datetime | None = None


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


class HeatmapCell(BaseModel):
    hour: int  # 0~23 KST
    count: int
    failure_rate: float


class HeatmapRow(BaseModel):
    interface_id: int
    interface_name: str
    protocol: str
    organization: str | None = None
    cells: list[HeatmapCell]