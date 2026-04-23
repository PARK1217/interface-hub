"""Threshold-based incident detector + error classifier (Phase 2)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import CallLog, Incident, Interface
from app.models.call_log import CallStatus
from app.models.incident import IncidentType
from app.services.notifier import dispatch_alert

log = logging.getLogger("noahub.detector")
ROLLING_WINDOW_MIN = 15  # rolling failure-rate window


_STATUS_TO_INCIDENT: dict[CallStatus, IncidentType] = {
    CallStatus.TIMEOUT: IncidentType.TIMEOUT,
    CallStatus.AUTH_ERROR: IncidentType.AUTH_ERROR,
    CallStatus.FORMAT_ERROR: IncidentType.FORMAT_ERROR,
    CallStatus.SERVER_ERROR: IncidentType.SERVER_ERROR,
    CallStatus.FAILURE: IncidentType.UNKNOWN,
}


def _resp_threshold(itf: Interface) -> int:
    return itf.response_ms_threshold or get_settings().default_response_ms_threshold


def _fail_threshold(itf: Interface) -> float:
    return itf.failure_rate_threshold or get_settings().default_failure_rate_threshold


def _open_incident(db: Session, interface_id: int, type_: IncidentType) -> Incident | None:
    return db.scalar(
        select(Incident)
        .where(Incident.interface_id == interface_id)
        .where(Incident.type == type_)
        .where(Incident.resolved_at.is_(None))
        .order_by(Incident.detected_at.desc())
    )


def _open_or_update(
    db: Session, itf: Interface, type_: IncidentType, summary: str, severity: str = "warning"
) -> Incident | None:
    """Create a new open incident if none exists for (interface, type); else no-op."""
    existing = _open_incident(db, itf.id, type_)
    if existing:
        return None
    incident = Incident(
        interface_id=itf.id, type=type_, severity=severity, summary=summary
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    log.info("incident opened: interface=%s type=%s", itf.name, type_)
    asyncio.create_task(  # noqa: RUF006
        dispatch_alert(itf, incident)
    )
    return incident


def evaluate_after_call(db: Session, itf: Interface, call: CallLog) -> None:
    """Run the lightweight checks after a single call_log row is persisted."""
    # 1) per-call hard failures → immediate incident (one open per type)
    if call.status in _STATUS_TO_INCIDENT:
        type_ = _STATUS_TO_INCIDENT[call.status]
        severity = "critical" if call.status == CallStatus.SERVER_ERROR else "warning"
        _open_or_update(
            db,
            itf,
            type_,
            summary=f"{type_.value} on {itf.name} (HTTP {call.http_status})",
            severity=severity,
        )

    # 2) slow response
    if call.duration_ms > _resp_threshold(itf):
        _open_or_update(
            db,
            itf,
            IncidentType.SLOW_RESPONSE,
            summary=f"slow response {call.duration_ms}ms > threshold {_resp_threshold(itf)}ms",
        )

    # 3) rolling failure rate
    since = datetime.now(timezone.utc) - timedelta(minutes=ROLLING_WINDOW_MIN)
    recent = db.scalars(
        select(CallLog)
        .where(CallLog.interface_id == itf.id)
        .where(CallLog.called_at >= since)
    ).all()
    total = len(recent)
    if total >= 5:
        failures = sum(1 for r in recent if r.status != CallStatus.SUCCESS)
        rate = failures / total
        if rate >= _fail_threshold(itf):
            _open_or_update(
                db,
                itf,
                IncidentType.HIGH_FAILURE_RATE,
                summary=f"failure rate {rate:.0%} over last {ROLLING_WINDOW_MIN}m (n={total})",
                severity="critical",
            )

    # 4) auto-resolve "slow response" / "high failure" if a healthy SUCCESS came in
    if call.status == CallStatus.SUCCESS and call.duration_ms <= _resp_threshold(itf):
        for type_ in (IncidentType.SLOW_RESPONSE, IncidentType.HIGH_FAILURE_RATE):
            opened = _open_incident(db, itf.id, type_)
            if opened:
                opened.resolved_at = datetime.now(timezone.utc)
                opened.resolution = "auto-resolved by healthy call"
                db.commit()
