from __future__ import annotations

from datetime import datetime
from typing import Any

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.interface import AuthType, ProtocolType


class InterfaceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    organization: str | None = None
    protocol: ProtocolType = ProtocolType.REST
    endpoint: str = Field(..., min_length=1, max_length=500)
    method: str = Field("GET", max_length=10)
    headers: dict[str, Any] | None = None
    request_template: dict[str, Any] | None = None
    auth_type: AuthType = AuthType.NONE
    schedule_cron: str | None = None
    enabled: bool = True
    response_ms_threshold: int | None = Field(default=None, ge=1)
    failure_rate_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    # Phase B.7 알림 룰
    muted_until: datetime | None = None
    alert_channels: list[str] | None = None

    @field_validator("schedule_cron")
    @classmethod
    def _check_cron(cls, v: str | None) -> str | None:
        if v and not croniter.is_valid(v):
            raise ValueError(f"invalid cron expression: {v}")
        return v


class InterfaceCreate(InterfaceBase):
    auth_secret: str | None = None  # plaintext on the way in; encrypted at rest


class InterfaceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    organization: str | None = None
    protocol: ProtocolType | None = None
    endpoint: str | None = None
    method: str | None = None
    headers: dict[str, Any] | None = None
    request_template: dict[str, Any] | None = None
    auth_type: AuthType | None = None
    auth_secret: str | None = None
    # 시크릿 변경 시 필수 (개인정보보호법·내부 보안 감사 대응).
    # 라우트에서 검증 — 시크릿 변경 안 하면 무시됨.
    secret_change_reason: str | None = None
    schedule_cron: str | None = None
    enabled: bool | None = None
    response_ms_threshold: int | None = None
    failure_rate_threshold: float | None = None
    alert_channels: list[str] | None = None  # Phase B.7

    @field_validator("schedule_cron")
    @classmethod
    def _check_cron(cls, v: str | None) -> str | None:
        if v and not croniter.is_valid(v):
            raise ValueError(f"invalid cron expression: {v}")
        return v


class RevealSecretRequest(BaseModel):
    reason: str  # 왜 시크릿을 보려는지 — 감사 로그에 영구 기록


class RevealSecretResponse(BaseModel):
    interface_id: int
    interface_name: str
    auth_type: AuthType
    secret: str  # 평문 — 응답으로만 1회 노출. UI 에서 자동 숨김 권장.


class InterfaceOut(InterfaceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    has_secret: bool = False