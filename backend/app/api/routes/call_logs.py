from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import CallLog
from app.models.call_log import CallStatus
from app.schemas.call_log import CallLogOut, CallLogStats, TimeSeriesPoint

router = APIRouter(prefix="/call-logs", tags=["call-logs"])


def _filters(
    interface_id: int | None,
    status_: CallStatus | None,
    keyword: str | None,
    since: datetime | None,
    until: datetime | None,
):
    conds = []
    if interface_id is not None:
        conds.append(CallLog.interface_id == interface_id)
    if status_ is not None:
        conds.append(CallLog.status == status_)
    if keyword:
        # crude full-text over error message; production would use tsvector
        conds.append(CallLog.error_message.ilike(f"%{keyword}%"))
    if since is not None:
        conds.append(CallLog.called_at >= since)
    if until is not None:
        conds.append(CallLog.called_at < until)
    return and_(*conds) if conds else None


@router.get("", response_model=list[CallLogOut])
def search_call_logs(
    interface_id: int | None = None,
    status: CallStatus | None = None,
    keyword: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[CallLogOut]:
    stmt = select(CallLog)
    where = _filters(interface_id, status, keyword, since, until)
    if where is not None:
        stmt = stmt.where(where)
    stmt = stmt.order_by(CallLog.called_at.desc()).limit(limit).offset(offset)
    return [CallLogOut.model_validate(r) for r in db.scalars(stmt).all()]


@router.get("/stats", response_model=CallLogStats)
def stats(
    interface_id: int | None = None,
    since: datetime | None = None,
    db: Session = Depends(get_db),
) -> CallLogStats:
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
    base = select(CallLog).where(CallLog.called_at >= since)
    if interface_id is not None:
        base = base.where(CallLog.interface_id == interface_id)
    rows = db.scalars(base).all()
    total = len(rows)
    success = sum(1 for r in rows if r.status == CallStatus.SUCCESS)
    failure = total - success
    avg = (sum(r.duration_ms for r in rows) / total) if total else 0.0
    rate = (success / total) if total else 0.0
    return CallLogStats(
        total=total, success=success, failure=failure, avg_duration_ms=avg, success_rate=rate
    )


@router.get("/timeseries", response_model=list[TimeSeriesPoint])
def timeseries(
    interface_id: int | None = None,
    bucket_minutes: int = Query(5, ge=1, le=60),
    since: datetime | None = None,
    db: Session = Depends(get_db),
) -> list[TimeSeriesPoint]:
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=6)
    # Postgres date_bin would be cleaner, but we keep cross-DB by bucketing in Python
    stmt = select(CallLog).where(CallLog.called_at >= since)
    if interface_id is not None:
        stmt = stmt.where(CallLog.interface_id == interface_id)
    rows = db.scalars(stmt).all()
    width = timedelta(minutes=bucket_minutes)
    buckets: dict[datetime, list[CallLog]] = {}
    for r in rows:
        delta = r.called_at - since
        idx = int(delta.total_seconds() // (bucket_minutes * 60))
        key = since + idx * width
        buckets.setdefault(key, []).append(r)
    out: list[TimeSeriesPoint] = []
    for key in sorted(buckets):
        bucket_rows = buckets[key]
        total = len(bucket_rows)
        success = sum(1 for r in bucket_rows if r.status == CallStatus.SUCCESS)
        avg = sum(r.duration_ms for r in bucket_rows) / total if total else 0.0
        out.append(
            TimeSeriesPoint(
                bucket=key, total=total, success=success, failure=total - success, avg_duration_ms=avg
            )
        )
    return out
