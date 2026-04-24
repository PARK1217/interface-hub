"""전역 알림 룰.

인터페이스별 알림 설정(muted_until / alert_channels)은 에서 이미
처리. 이 테이블은 그 위에 얹히는 **전역 정책**:

1. severity 별 채널 라우팅
   info     → in_app 만 (대시보드 뱃지로만 노출)
   warning  → in_app + slack (운영팀 채널 알림)
   critical → in_app + slack + email (담당자 호출)

2. 근무시간 외 silence (quiet_hours)
   심야/주말에 info·warning 알림 폭주 방지. critical 은 별도 토글로 제외 가능
   (기본 ON — 진짜 장애는 새벽이라도 깨워야 함).

룰 1행만 존재 (id=1 고정 — 마이그레이션에서 INSERT ON CONFLICT 로 보장).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # severity 별 발송 채널 화이트리스트 (in_app / slack / email).
    # 인터페이스의 alert_channels 와 교집합 — 둘 다 허용해야 발송됨.
    info_channels: Mapped[list] = mapped_column(JSON, default=lambda: ["in_app"])
    warning_channels: Mapped[list] = mapped_column(
        JSON, default=lambda: ["in_app", "slack"]
    )
    critical_channels: Mapped[list] = mapped_column(
        JSON, default=lambda: ["in_app", "slack", "email"]
    )

    # 근무시간 외 silence
    # quiet_hours_start/end 는 KST 기준 시 (0~23). start > end 면 자정 넘김
    # (예: 22→8 은 22시부터 다음 날 8시까지 silence).
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quiet_hours_start: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    quiet_hours_end: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    # critical 은 quiet hours 에도 깨움 (기본 ON). 끄면 critical 도 silence.
    quiet_hours_skip_critical: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    # 주말 (토/일) 종일 silence — quiet hours 와 OR.
    weekend_silence: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
