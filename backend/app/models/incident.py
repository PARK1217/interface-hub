from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class IncidentType(str, enum.Enum):
    TIMEOUT = "TIMEOUT"
    AUTH_ERROR = "AUTH_ERROR"
    FORMAT_ERROR = "FORMAT_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    SLOW_RESPONSE = "SLOW_RESPONSE"
    HIGH_FAILURE_RATE = "HIGH_FAILURE_RATE"
    SECRET_EXPIRY_WARNING = "SECRET_EXPIRY_WARNING"  # 인증 키 만료 임박
    UNKNOWN = "UNKNOWN"


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), index=True
    )

    type: Mapped[IncidentType] = mapped_column(Enum(IncidentType), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="warning")  # info|warning|critical

    summary: Mapped[str] = mapped_column(String(255))
    root_cause: Mapped[str | None] = mapped_column(Text, default=None)
    resolution: Mapped[str | None] = mapped_column(Text, default=None)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    interface: Mapped["Interface"] = relationship(back_populates="incidents", lazy="noload")  # noqa: F821
