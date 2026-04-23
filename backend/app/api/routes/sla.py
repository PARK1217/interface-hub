from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.time import now_kst
from app.models import CallLog, Interface, SlaTarget
from app.models.call_log import CallStatus
from app.schemas.sla_target import SlaReportRow, SlaTargetCreate, SlaTargetOut, SlaTargetUpdate

router = APIRouter(prefix="/sla", tags=["sla"])


@router.get("/targets", response_model=list[SlaTargetOut])
def list_targets(db: Session = Depends(get_db)) -> list[SlaTargetOut]:
    return [SlaTargetOut.model_validate(t) for t in db.scalars(select(SlaTarget)).all()]


@router.post("/targets", response_model=SlaTargetOut, status_code=status.HTTP_201_CREATED)
def upsert_target(payload: SlaTargetCreate, db: Session = Depends(get_db)) -> SlaTargetOut:
    existing = db.scalar(select(SlaTarget).where(SlaTarget.interface_id == payload.interface_id))
    if existing:
        for k, v in payload.model_dump(exclude={"interface_id"}).items():
            setattr(existing, k, v)
        obj = existing
    else:
        obj = SlaTarget(**payload.model_dump())
        db.add(obj)
    db.commit()
    db.refresh(obj)
    return SlaTargetOut.model_validate(obj)


@router.patch("/targets/{target_id}", response_model=SlaTargetOut)
def update_target(
    target_id: int, payload: SlaTargetUpdate, db: Session = Depends(get_db)
) -> SlaTargetOut:
    obj = db.get(SlaTarget, target_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sla target not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return SlaTargetOut.model_validate(obj)


@router.get("/report", response_model=list[SlaReportRow])
def report(
    days: int = 30,
    db: Session = Depends(get_db),
) -> list[SlaReportRow]:
    since = now_kst() - timedelta(days=days)
    interfaces = db.scalars(select(Interface)).all()
    rows: list[SlaReportRow] = []
    for itf in interfaces:
        target = db.scalar(select(SlaTarget).where(SlaTarget.interface_id == itf.id))
        target_uptime = target.uptime_target if target else 99.0
        target_resp = target.response_ms_target if target else 2000

        logs = db.scalars(
            select(CallLog)
            .where(CallLog.interface_id == itf.id)
            .where(CallLog.called_at >= since)
        ).all()
        total = len(logs)
        success = sum(1 for log in logs if log.status == CallStatus.SUCCESS)
        uptime = (success / total * 100.0) if total else 100.0
        avg = (sum(log.duration_ms for log in logs) / total) if total else 0.0

        rows.append(
            SlaReportRow(
                interface_id=itf.id,
                interface_name=itf.name,
                uptime_pct=round(uptime, 3),
                avg_response_ms=round(avg, 1),
                target_uptime=target_uptime,
                target_response_ms=target_resp,
                meets_uptime=uptime >= target_uptime,
                meets_response=avg <= target_resp,
            )
        )
    return rows
