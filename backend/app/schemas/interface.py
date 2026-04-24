from __future__ import annotations

from datetime import datetime
from typing import Any

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.interface import AuthType, InterfaceCategory, InterfaceDirection, ProtocolType


class InterfaceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    organization: str | None = None
    # 통합 관제 분류 (내부 핵심 / 외부 제휴 / 외부 규제기관)
    category: InterfaceCategory = InterfaceCategory.EXTERNAL_PARTNER
    # 호출 방향 (OUTBOUND=우리가 호출 / INBOUND=외부가 우리를 호출)
    direction: InterfaceDirection = InterfaceDirection.OUTBOUND
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
    # 알림 룰
    muted_until: datetime | None = None
    alert_channels: list[str] | None = None
    # 호출 안정성
    timeout_seconds: float | None = Field(default=None, ge=0.5, le=300.0)
    retry_max: int = Field(default=0, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    # 인증 키 만료일 (선택). 임박 시 자동 알림.
    auth_secret_expires_at: datetime | None = None

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
    category: InterfaceCategory | None = None
    direction: InterfaceDirection | None = None
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
    alert_channels: list[str] | None = None
    # 호출 안정성
    timeout_seconds: float | None = Field(default=None, ge=0.5, le=300.0)
    retry_max: int | None = Field(default=None, ge=0, le=5)
    retry_backoff_seconds: float | None = Field(default=None, ge=0.0, le=30.0)
    # 인증 키 만료일 (선택)
    auth_secret_expires_at: datetime | None = None

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