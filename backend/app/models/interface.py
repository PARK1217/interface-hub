from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ProtocolType(str, enum.Enum):
    REST = "REST"
    SOAP = "SOAP"
    FTP = "FTP"
    MQ = "MQ"
    BATCH = "BATCH"


class AuthType(str, enum.Enum):
    NONE = "NONE"
    BASIC = "BASIC"
    API_KEY = "API_KEY"
    OAUTH2 = "OAUTH2"
    BEARER = "BEARER"


class Interface(Base):
    __tablename__ = "interfaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    organization: Mapped[str | None] = mapped_column(String(120), default=None, index=True)

    protocol: Mapped[ProtocolType] = mapped_column(Enum(ProtocolType), default=ProtocolType.REST)
    endpoint: Mapped[str] = mapped_column(String(500))
    method: Mapped[str] = mapped_column(String(10), default="GET")  # for REST/SOAP

    headers: Mapped[dict | None] = mapped_column(JSON, default=None)
    request_template: Mapped[dict | None] = mapped_column(JSON, default=None)

    auth_type: Mapped[AuthType] = mapped_column(Enum(AuthType), default=AuthType.NONE)
    # encrypted blob (AES-GCM); see core.security
    auth_secret: Mapped[str | None] = mapped_column(Text, default=None)

    schedule_cron: Mapped[str | None] = mapped_column(String(120), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # threshold overrides; null → use system defaults
    response_ms_threshold: Mapped[int | None] = mapped_column(Integer, default=None)
    failure_rate_threshold: Mapped[float | None] = mapped_column(default=None)

    # --- Phase B.7 알림 룰 -------------------------------------------------
    # muted_until: 이 시각까지 알림 발송 중단 (정기 점검 시간 등). null=음소거 X.
    #   incident 자체는 그대로 생성되어 기록은 남고, 대시보드/incidents 페이지의
    #   카운트도 갱신됨. "알림 시끄러움"만 끔.
    # alert_channels: 발송할 채널 화이트리스트. 기본 3종 모두. 인터페이스별로
    #   "이 KIDI 는 Slack 만, 다른 건 in-app 만" 식으로 운영자 라우팅 가능.
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    alert_channels: Mapped[list | None] = mapped_column(
        JSON, default=lambda: ["in_app", "slack", "email"]
    )

    # --- Phase B.9 호출 안정성 (재시도 + timeout) ---------------------------
    # timeout_seconds: 호출당 타임아웃. null 이면 시스템 기본(10s).
    #   외부 기관별로 응답 SLA 가 다름 — KIDI 는 보통 < 2s, 보험개발원은 5s+
    #   허용 같은 식으로 운영자 조정. httpx.AsyncClient(timeout=...) 에 그대로 전달.
    # retry_max: 추가 재시도 횟수. 0 이면 재시도 안 함 (총 1회만 호출).
    #   2 면 최초 호출 + 재시도 2회 = 총 3회. exponential backoff 적용.
    # retry_backoff_seconds: 첫 재시도까지 대기 시간. 두 번째는 ×2, 세 번째 ×4...
    #   짧게 (0.5~2s) 두는 게 정석 — 외부 기관 jitter 회피용.
    # 재시도 대상: TIMEOUT / SERVER_ERROR (5xx) / network 에러만.
    #   AUTH_ERROR (401/403) 와 FORMAT_ERROR (422) 는 재시도해도 같은 결과 →
    #   즉시 실패 처리. 무의미한 호출 폭주 방지.
    timeout_seconds: Mapped[float | None] = mapped_column(default=None)
    retry_max: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_backoff_seconds: Mapped[float] = mapped_column(default=1.0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Soft-delete marker. Null = active. Non-null = in trash (kept for audit).
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )

    call_logs: Mapped[list["CallLog"]] = relationship(  # noqa: F821
        back_populates="interface", cascade="all, delete-orphan", lazy="noload"
    )
    incidents: Mapped[list["Incident"]] = relationship(  # noqa: F821
        back_populates="interface", cascade="all, delete-orphan", lazy="noload"
    )
    sla_target: Mapped["SlaTarget | None"] = relationship(  # noqa: F821
        back_populates="interface", uselist=False, cascade="all, delete-orphan", lazy="noload"
    )