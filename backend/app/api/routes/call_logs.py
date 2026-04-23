from __future__ import annotations

from datetime import datetime, timedelta

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status as http_status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_optional_user, require_role
from app.core.config import get_settings
from app.core.time import now_kst
from app.core.websocket import ws_manager
from app.models import CallLog, Interface, User, UserRole
from app.models.call_log import CallStatus
from app.models.interface import ProtocolType
from app.schemas.call_log import (
    BulkRetryRequest,
    BulkRetryResponse,
    CallLogOut,
    CallLogStats,
    HeatmapCell,
    HeatmapRow,
    IngestRequest,
    TimeSeriesPoint,
)
from app.services.audit import record_audit
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


def _scope_by_interface(stmt, protocol: ProtocolType | None, organization: str | None):
    """Join + scope a CallLog query by interface attributes (protocol/organization)."""
    if protocol is None and organization is None:
        return stmt
    stmt = stmt.join(Interface, Interface.id == CallLog.interface_id)
    if protocol is not None:
        stmt = stmt.where(Interface.protocol == protocol)
    if organization:
        stmt = stmt.where(Interface.organization == organization)
    return stmt


@router.get("", response_model=list[CallLogOut])
def search_call_logs(
    interface_id: int | None = None,
    status: CallStatus | None = None,
    keyword: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    protocol: ProtocolType | None = None,
    organization: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[CallLogOut]:
    stmt = select(CallLog)
    where = _filters(interface_id, status, keyword, since, until)
    if where is not None:
        stmt = stmt.where(where)
    stmt = _scope_by_interface(stmt, protocol, organization)
    stmt = stmt.order_by(CallLog.called_at.desc()).limit(limit).offset(offset)
    return [CallLogOut.model_validate(r) for r in db.scalars(stmt).all()]


@router.get("/stats", response_model=CallLogStats)
def stats(
    interface_id: int | None = None,
    since: datetime | None = None,
    db: Session = Depends(get_db),
) -> CallLogStats:
    if since is None:
        since = now_kst() - timedelta(hours=24)
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
        since = now_kst() - timedelta(hours=6)
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
# Heatmap — 시간대 × 인터페이스 호출 빈도
# ============================================================================
@router.get("/heatmap", response_model=list[HeatmapRow])
def heatmap(
    days: int = Query(7, ge=1, le=30),
    protocol: ProtocolType | None = None,
    organization: str | None = None,
    db: Session = Depends(get_db),
) -> list[HeatmapRow]:
    """Aggregate calls into a 24-hour-of-day × interface grid (KST)."""
    since = now_kst() - timedelta(days=days)
    iface_stmt = select(Interface).order_by(Interface.id)
    if protocol is not None:
        iface_stmt = iface_stmt.where(Interface.protocol == protocol)
    if organization:
        iface_stmt = iface_stmt.where(Interface.organization == organization)
    interfaces = db.scalars(iface_stmt).all()

    out: list[HeatmapRow] = []
    for itf in interfaces:
        rows = db.scalars(
            select(CallLog)
            .where(CallLog.interface_id == itf.id)
            .where(CallLog.called_at >= since)
        ).all()
        buckets: dict[int, list[CallLog]] = {h: [] for h in range(24)}
        for r in rows:
            buckets[r.called_at.hour].append(r)
        cells = []
        for h in range(24):
            bucket = buckets[h]
            total = len(bucket)
            failures = sum(1 for r in bucket if r.status != CallStatus.SUCCESS)
            cells.append(
                HeatmapCell(
                    hour=h,
                    count=total,
                    failure_rate=round(failures / total, 3) if total else 0.0,
                )
            )
        out.append(
            HeatmapRow(
                interface_id=itf.id,
                interface_name=itf.name,
                protocol=itf.protocol.value,
                organization=itf.organization,
                cells=cells,
                deleted_at=itf.deleted_at,
            )
        )
    return out


# ============================================================================
# Ingest — 외부 시스템이 자기 호출 결과를 보고하는 통로
# ============================================================================
@router.post("/ingest", response_model=CallLogOut, status_code=http_status.HTTP_201_CREATED)
async def ingest_call_log(
    payload: IngestRequest,
    request: Request,
    x_ingest_key: str | None = Header(default=None, alias="X-Ingest-Key"),
    db: Session = Depends(get_db),
    # 외부 시스템이 사용 → 사용자 로그인 + 헤더 키 양쪽 다 옵션. 둘 중 하나는 있어야.
    actor: User | None = Depends(get_optional_user),
) -> CallLogOut:
    """Accept a call_log row pushed by another internal system.

    Useful when the external API call wasn't executed by the Hub itself —
    e.g. a sales system called KIDI directly, then POSTs the outcome here so
    that all observability stays centralized.
    """
    settings = get_settings()
    # 인증 정책: (1) 유효한 X-Ingest-Key OR (2) 로그인 토큰 — 둘 중 하나 필수
    key_ok = settings.ingest_api_key and x_ingest_key == settings.ingest_api_key
    if not key_ok and actor is None:
        raise HTTPException(
            http_status.HTTP_401_UNAUTHORIZED,
            "X-Ingest-Key 헤더 또는 로그인 토큰이 필요합니다.",
        )

    itf = db.get(Interface, payload.interface_id)
    if not itf:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "interface not found")

    log_row = CallLog(
        interface_id=payload.interface_id,
        request=payload.request,
        response=payload.response,
        status=payload.status,
        http_status=payload.http_status,
        duration_ms=payload.duration_ms,
        error_message=payload.error_message,
        error_type=payload.error_type,
        error_trace=payload.error_trace,
        triggered_by="ingest",
        called_at=payload.called_at or now_kst(),
        actor_user_id=actor.id if actor is not None else None,
    )
    db.add(log_row)
    db.commit()
    db.refresh(log_row)

    # run threshold detector on ingested events too
    try:
        from app.services.detector import evaluate_after_call
        evaluate_after_call(db, itf, log_row)
    except Exception:  # noqa: BLE001
        pass

    await ws_manager.broadcast(
        "call_log",
        {
            "id": log_row.id,
            "interface_id": itf.id,
            "interface_name": itf.name,
            "status": log_row.status.value,
            "http_status": log_row.http_status,
            "duration_ms": log_row.duration_ms,
            "called_at": log_row.called_at.isoformat() if log_row.called_at else None,
            "triggered_by": "ingest",
        },
    )
    return CallLogOut.model_validate(log_row)


# ============================================================================
# Reprocessing — 재처리
# ============================================================================
@router.post("/{log_id}/retry", response_model=CallLogOut)
async def retry_call(
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.OPERATOR, UserRole.ADMIN])),
) -> CallLogOut:
    parent = db.get(CallLog, log_id)
    if not parent:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "call log not found")
    itf = db.get(Interface, parent.interface_id)
    if not itf:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "interface not found")
    new_log = await execute_interface(itf, db, parent_log=parent, actor=actor)
    record_audit(
        db, actor=actor, action="call_log.retry",
        resource_type="call_log", resource_id=parent.id,
        after={"new_log_id": new_log.id, "status": new_log.status.value},
        request=request,
    )
    return CallLogOut.model_validate(new_log)


@router.post("/bulk-retry", response_model=BulkRetryResponse)
async def bulk_retry(
    payload: BulkRetryRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.OPERATOR, UserRole.ADMIN])),
) -> BulkRetryResponse:
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
            new_log = await execute_interface(itf, db, parent_log=p, actor=actor)
            new_ids.append(new_log.id)
        except Exception:  # noqa: BLE001 — keep going through the batch
            skipped += 1
    record_audit(
        db, actor=actor, action="call_log.bulk_retry",
        after={"submitted": len(new_ids), "skipped": skipped, "filter": payload.model_dump()},
        request=request,
    )
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
