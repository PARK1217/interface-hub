"""Shared helpers for incident ↔ call_log linkage.

Used by both the incidents HTTP route and the detector (auto-resolve path)
so they agree on which call_logs 'belong' to which incident.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import now_kst
from app.models import CallLog, Incident, Interface
from app.models.call_log import CallStatus
from app.models.incident import IncidentType


# Inverse of detector._STATUS_TO_INCIDENT — for a given incident type,
# which CallStatus values should be considered 'related'?
_INCIDENT_TO_STATUSES: dict[IncidentType, list[CallStatus]] = {
    IncidentType.TIMEOUT: [CallStatus.TIMEOUT],
    IncidentType.AUTH_ERROR: [CallStatus.AUTH_ERROR],
    IncidentType.FORMAT_ERROR: [CallStatus.FORMAT_ERROR],
    IncidentType.SERVER_ERROR: [CallStatus.SERVER_ERROR],
    IncidentType.UNKNOWN: [CallStatus.FAILURE],
    IncidentType.SLOW_RESPONSE: [],  # special: filter by duration_ms
    IncidentType.HIGH_FAILURE_RATE: [s for s in CallStatus if s != CallStatus.SUCCESS],
}


def related_log_query(incident: Incident, interface: Interface | None):
    """Build the WHERE clause selecting call_logs that 'belong' to this incident."""
    end = incident.resolved_at or now_kst()
    base = (
        select(CallLog)
        .where(CallLog.interface_id == incident.interface_id)
        .where(CallLog.called_at >= incident.detected_at)
        .where(CallLog.called_at <= end)
    )
    if incident.type == IncidentType.SLOW_RESPONSE:
        threshold = (
            (interface.response_ms_threshold if interface else None)
            or get_settings().default_response_ms_threshold
        )
        base = base.where(CallLog.duration_ms > threshold)
    else:
        statuses = _INCIDENT_TO_STATUSES.get(incident.type, [])
        if statuses:
            base = base.where(CallLog.status.in_(statuses))
    return base


def mark_related_handled(
    db: Session, incident: Incident, interface: Interface | None
) -> int:
    """Mark all unprocessed related call_logs as ``is_reprocessed=True``.

    Called whenever an incident closes (manual or auto). Prevents the operator
    from accidentally re-clicking ↻ on the Logs page after the incident has
    already been administratively closed — which would cause duplicate
    external calls.

    Returns the number of rows marked.
    """
    rows = db.scalars(
        related_log_query(incident, interface).where(CallLog.is_reprocessed.is_(False))
    ).all()
    if not rows:
        return 0
    log_ids = [r.id for r in rows]
    db.execute(update(CallLog).where(CallLog.id.in_(log_ids)).values(is_reprocessed=True))
    return len(log_ids)
