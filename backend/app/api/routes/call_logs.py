from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import CallLog, Interface
from app.models.call_log import CallStatus
from app.schemas.call_log import (
    BulkRetryRequest,
    BulkRetryResponse,
    CallLogOut,
    CallLogStats,
    TimeSeriesPoint,
)
from app.services.executor import execute_interface

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


# ============================================================================
# Reprocessing — 재처리
# ============================================================================
@router.post("/{log_id}/retry", response_model=CallLogOut)
async def retry_call(log_id: int, db: Session = Depends(get_db)) -> CallLogOut:
    parent = db.get(CallLog, log_id)
    if not parent:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "call log not found")
    itf = db.get(Interface, parent.interface_id)
    if not itf:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "interface not found")
    new_log = await execute_interface(itf, db, parent_log=parent)
    return CallLogOut.model_validate(new_log)


@router.post("/bulk-retry", response_model=BulkRetryResponse)
async def bulk_retry(payload: BulkRetryRequest, db: Session = Depends(get_db)) -> BulkRetryResponse:
    stmt = select(CallLog)
    if payload.interface_id is not None:
        stmt = stmt.where(CallLog.interface_id == payload.interface_id)
    if payload.only_failed:
        stmt = stmt.where(CallLog.status != CallStatus.SUCCESS)
    if payload.status is not None:
        stmt = stmt.where(CallLog.status == payload.status)
    if payload.since is not None:
        stmt = stmt.where(CallLog.called_at >= payload.since)
    if payload.until is not None:
        stmt = stmt.where(CallLog.called_at < payload.until)
    if payload.skip_already_reprocessed:
        stmt = stmt.where(CallLog.is_reprocessed.is_(False))
    stmt = stmt.order_by(CallLog.called_at.desc()).limit(payload.max_count)

    parents = db.scalars(stmt).all()
    new_ids: list[int] = []
    skipped = 0
    for p in parents:
        itf = db.get(Interface, p.interface_id)
        if not itf or not itf.enabled:
            skipped += 1
            continue
        try:
            new_log = await execute_interface(itf, db, parent_log=p)
            new_ids.append(new_log.id)
        except Exception:  # noqa: BLE001 — keep going through the batch
            skipped += 1
    return BulkRetryResponse(submitted=len(new_ids), skipped=skipped, new_log_ids=new_ids)


@router.get("/{log_id}/chain", response_model=list[CallLogOut])
def call_chain(log_id: int, db: Session = Depends(get_db)) -> list[CallLogOut]:
    """Return the full retry lineage starting from the original call."""
    log = db.get(CallLog, log_id)
    if not log:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "call log not found")

    # walk to root
    while log.parent_log_id:
        parent = db.get(CallLog, log.parent_log_id)
        if not parent:
            break
        log = parent

    chain: list[CallLog] = [log]
    queue: list[int] = [log.id]
    while queue:
        current_id = queue.pop(0)
        children = db.scalars(
            select(CallLog).where(CallLog.parent_log_id == current_id).order_by(CallLog.called_at)
        ).all()
        for c in children:
            chain.append(c)
            queue.append(c.id)
    return [CallLogOut.model_validate(c) for c in chain]
