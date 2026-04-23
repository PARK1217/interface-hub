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