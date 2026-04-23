from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.time import now_kst
from app.models import CallLog, Incident, Interface
from app.models.call_log import CallStatus
from app.models.incident import IncidentType
from app.schemas.call_log import CallLogOut
from app.schemas.incident import IncidentCreate, IncidentOut, IncidentUpdate

router = APIRouter(prefix="/incidents", tags=["incidents"])


# Inverse of detector._STATUS_TO_INCIDENT — given an incident type, which
# CallStatus values are considered "related"?
_INCIDENT_TO_STATUSES: dict[IncidentType, list[CallStatus]] = {
    IncidentType.TIMEOUT: [CallStatus.TIMEOUT],
    IncidentType.AUTH_ERROR: [CallStatus.AUTH_ERROR],
    IncidentType.FORMAT_ERROR: [CallStatus.FORMAT_ERROR],
    IncidentType.SERVER_ERROR: [CallStatus.SERVER_ERROR],
    IncidentType.UNKNOWN: [CallStatus.FAILURE],
    IncidentType.SLOW_RESPONSE: [],  # special: filter by duration_ms instead
    IncidentType.HIGH_FAILURE_RATE: [
        s for s in CallStatus if s != CallStatus.SUCCESS
    ],
}


def _related_log_query(incident: Incident, interface: Interface | None):
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


def _to_out(db: Session, incident: Incident, *, count: int | None = None) -> IncidentOut:
    if count is None:
        itf = db.get(Interface, incident.interface_id)
        count = len(db.scalars(_related_log_query(incident, itf)).all())
    payload = IncidentOut.model_validate(incident)
    payload.related_log_count = count
    return payload


@router.get("", response_model=list[IncidentOut])
def list_incidents(
    interface_id: int | None = None,
    unresolved_only: bool = False,
    db: Session = Depends(get_db),
) -> list[IncidentOut]:
    stmt = select(Incident).order_by(Incident.detected_at.desc())
    if interface_id is not None:
        stmt = stmt.where(Incident.interface_id == interface_id)
    if unresolved_only:
        stmt = stmt.where(Incident.resolved_at.is_(None))
    incidents = db.scalars(stmt).all()
    # cache interfaces to avoid N+1
    iface_ids = {i.interface_id for i in incidents}
    interfaces = {
        i.id: i
        for i in db.scalars(select(Interface).where(Interface.id.in_(iface_ids))).all()
    }
    out = []
    for inc in incidents:
        count = len(db.scalars(_related_log_query(inc, interfaces.get(inc.interface_id))).all())
        out.append(_to_out(db, inc, count=count))
    return out


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(payload: IncidentCreate, db: Session = Depends(get_db)) -> IncidentOut:
    obj = Incident(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return _to_out(db, obj)


@router.patch("/{incident_id}", response_model=IncidentOut)
def update_incident(
    incident_id: int, payload: IncidentUpdate, db: Session = Depends(get_db)
) -> IncidentOut:
    obj = db.get(Incident, incident_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return _to_out(db, obj)


@router.post("/{incident_id}/resolve", response_model=IncidentOut)
def resolve_incident(
    incident_id: int, resolution: str | None = None, db: Session = Depends(get_db)
) -> IncidentOut:
    obj = db.get(Incident, incident_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    obj.resolved_at = now_kst()
    if resolution:
        obj.resolution = resolution
    db.commit()
    db.refresh(obj)
    return _to_out(db, obj)


@router.get("/{incident_id}/related-logs", response_model=list[CallLogOut])
def related_logs(
    incident_id: int,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[CallLogOut]:
    obj = db.get(Incident, incident_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    itf = db.get(Interface, obj.interface_id)
    rows = db.scalars(
        _related_log_query(obj, itf).order_by(CallLog.called_at.desc()).limit(limit)
    ).all()
    return [CallLogOut.model_validate(r) for r in rows]
