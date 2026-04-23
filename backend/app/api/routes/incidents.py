from __future__ import annotations

from app.core.time import now_kst

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Incident
from app.schemas.incident import IncidentCreate, IncidentOut, IncidentUpdate

router = APIRouter(prefix="/incidents", tags=["incidents"])


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
    return [IncidentOut.model_validate(r) for r in db.scalars(stmt).all()]


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(payload: IncidentCreate, db: Session = Depends(get_db)) -> IncidentOut:
    obj = Incident(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return IncidentOut.model_validate(obj)


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
    return IncidentOut.model_validate(obj)


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
    return IncidentOut.model_validate(obj)
