"""인증 키 (auth_secret) 만료 임박 모니터링.

매일 1회 (스케줄러가 호출) DB 의 모든 인터페이스를 훑어:
  - auth_secret_expires_at 이 D-7 이내인 것
  - 같은 날 아직 알림 안 보낸 것 (auth_secret_warning_sent_at dedup)
→ SECRET_EXPIRY_WARNING incident 생성 + 기존 알림 룰 흐름으로 발송.

이미 만료된 키는 critical, D-3 이내는 critical, D-7 이내는 warning.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.time import now_kst
from app.models import Incident, Interface
from app.models.incident import IncidentType
from app.services.notifier import dispatch_alert

log = logging.getLogger("noahub.secret_expiry")

# 만료 임박 판정 기준 (일)
WARNING_THRESHOLD_DAYS = 7
CRITICAL_THRESHOLD_DAYS = 3


def _severity_for(days_remaining: int) -> str:
    """남은 일수 → severity. 이미 만료(음수) 또는 D-3 이내는 critical."""
    if days_remaining <= CRITICAL_THRESHOLD_DAYS:
        return "critical"
    return "warning"


def _summary_for(itf: Interface, days_remaining: int) -> str:
    if days_remaining < 0:
        return f"{itf.name} 인증 키가 {-days_remaining}일 전에 만료되었습니다."
    if days_remaining == 0:
        return f"{itf.name} 인증 키가 오늘 만료됩니다."
    return f"{itf.name} 인증 키가 {days_remaining}일 후 만료됩니다 (D-{days_remaining})."


def check_expiring_secrets(db: Session | None = None) -> int:
    """만료 임박 키를 검사해 incident 를 생성하고 알림을 발송.

    반환: 새로 생성된 경고 incident 개수.
    """
    own_session = db is None
    if db is None:
        db = SessionLocal()
    try:
        now = now_kst()
        cutoff = now + timedelta(days=WARNING_THRESHOLD_DAYS)

        # 만료일이 cutoff 이내인 인터페이스 모두 (이미 만료된 것 포함)
        candidates = db.scalars(
            select(Interface)
            .where(Interface.deleted_at.is_(None))
            .where(Interface.auth_secret_expires_at.is_not(None))
            .where(Interface.auth_secret_expires_at <= cutoff)
        ).all()

        new_count = 0
        for itf in candidates:
            # 같은 날 이미 알림 발송한 인터페이스는 스킵 (dedup)
            if (
                itf.auth_secret_warning_sent_at is not None
                and itf.auth_secret_warning_sent_at.date() == now.date()
            ):
                continue

            days_remaining = (itf.auth_secret_expires_at - now).days
            severity = _severity_for(days_remaining)
            summary = _summary_for(itf, days_remaining)

            incident = Incident(
                interface_id=itf.id,
                type=IncidentType.SECRET_EXPIRY_WARNING,
                severity=severity,
                summary=summary,
            )
            db.add(incident)
            itf.auth_secret_warning_sent_at = now
            db.commit()
            db.refresh(incident)
            new_count += 1

            log.warning(
                "secret expiry: interface=%s days=%d severity=%s",
                itf.name, days_remaining, severity,
            )
            # 기존 알림 흐름 (인터페이스 muted_until + 전역 룰) 그대로 적용.
            # event loop 가 없는 환경 (검증 스크립트, CLI) 에서는 알림은 스킵 — incident
            # 자체는 DB 에 남아 다음 정상 호출 시 노출됨.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(dispatch_alert(itf, incident))
            except RuntimeError:
                log.info("event loop 없음 — 알림 발송 스킵 (incident 는 DB 기록 완료)")

        return new_count
    finally:
        if own_session:
            db.close()
