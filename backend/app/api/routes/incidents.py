from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.time import now_kst
from app.models import CallLog, Incident, Interface
from app.models.call_log import CallStatus
from app.schemas.call_log import BulkRetryResponse, CallLogOut
from app.schemas.incident import IncidentCreate, IncidentOut, IncidentUpdate
from app.services.executor import execute_interface
from app.services.incident_helpers import mark_related_handled, related_log_query

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _to_out(db: Session, incident: Incident, *, count: int | None = None) -> IncidentOut:
    if count is None:
        itf = db.get(Interface, incident.interface_id)
        count = len(db.scalars(related_log_query(incident, itf)).all())
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
    iface_ids = {i.interface_id for i in incidents}
    interfaces = {
        i.id: i
        for i in db.scalars(select(Interface).where(Interface.id.in_(iface_ids))).all()
    }
    out = []
    for inc in incidents:
        count = len(
            db.scalars(related_log_query(inc, interfaces.get(inc.interface_id))).all()
        )
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
    was_unresolved = obj.resolved_at is None
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    # If this PATCH is what closes the incident, sweep the related call_logs.
    if was_unresolved and obj.resolved_at is not None:
        itf = db.get(Interface, obj.interface_id)
        mark_related_handled(db, obj, itf)
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
    if obj.resolved_at is None:
        obj.resolved_at = now_kst()
    if resolution:
        obj.resolution = resolution
    itf = db.get(Interface, obj.interface_id)
    handled = mark_related_handled(db, obj, itf)
    db.commit()
    db.refresh(obj)
    out = _to_out(db, obj)
    # bonus: hint how many logs were swept (kept out of schema for simplicity)
    return out


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
        related_log_query(obj, itf).order_by(CallLog.called_at.desc()).limit(limit)
    ).all()
    return [CallLogOut.model_validate(r) for r in rows]


class IncidentRetryRequest(BaseModel):
    mode: Literal["latest", "all"] = "latest"


@router.post("/{incident_id}/retry-related", response_model=BulkRetryResponse)
async def retry_related(
    incident_id: int,
    payload: IncidentRetryRequest,
    db: Session = Depends(get_db),
) -> BulkRetryResponse:
    """Retry the call_logs that belong to this incident.

    mode=latest → re-run only the most recent failed call (safe default —
        guards against billing/duplicating the same business operation).
    mode=all    → re-run every still-unprocessed failed call related to the
        incident (use when each call represents a distinct business request).

    Already-reprocessed rows are always skipped.
    """
    obj = db.get(Incident, incident_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    itf = db.get(Interface, obj.interface_id)
    if not itf or not itf.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "interface missing or disabled")

    base = (
        related_log_query(obj, itf)
        .where(CallLog.is_reprocessed.is_(False))
        .where(CallLog.status != CallStatus.SUCCESS)
        .order_by(CallLog.called_at.desc())
    )
    if payload.mode == "latest":
        base = base.limit(1)
    parents = db.scalars(base).all()

    new_ids: list[int] = []
    skipped = 0
    for parent in parents:
        try:
            new_log = await execute_interface(itf, db, parent_log=parent)
            new_ids.append(new_log.id)
        except Exception:  # noqa: BLE001
            skipped += 1
    return BulkRetryResponse(submitted=len(new_ids), skipped=skipped, new_log_ids=new_ids)
