from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SlaTarget(Base):
    __tablename__ = "sla_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), unique=True, index=True
    )

    uptime_target: Mapped[float] = mapped_column(default=99.0)  # %
    response_ms_target: Mapped[int] = mapped_column(Integer, default=2000)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    interface: Mapped["Interface"] = relationship(back_populates="sla_target", lazy="noload")  # noqa: F821