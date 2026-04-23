"""Performance dashboards — latency percentiles · throughput · slow top-N."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.time import now_kst
from app.models import CallLog, Interface
from app.models.call_log import CallStatus

router = APIRouter(prefix="/performance", tags=["performance"])


class PercentileRow(BaseModel):
    interface_id: int
    interface_name: str
    protocol: str
    organization: str | None = None
    total_calls: int
    failure_count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: int
    avg_ms: float
    throughput_per_min: float


class SlowCallRow(BaseModel):
    id: int
    interface_id: int
    interface_name: str
    protocol: str
    duration_ms: int
    status: str
    http_status: int | None
    called_at: datetime


class ThroughputPoint(BaseModel):
    bucket: datetime
    tps: float
    p95_ms: float


@router.get("/percentiles", response_model=list[PercentileRow])
def percentiles(
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
) -> list[PercentileRow]:
    """Per-interface latency percentiles using PG percentile_cont."""
    since = now_kst() - timedelta(days=days)
    window_minutes = days * 24 * 60

    sql = text(
        """
        SELECT
            i.id            AS interface_id,
            i.name          AS interface_name,
            i.protocol      AS protocol,
            i.organization  AS organization,
            COUNT(*)        AS total_calls,
            SUM(CASE WHEN c.status <> 'SUCCESS' THEN 1 ELSE 0 END) AS failure_count,
            percentile_cont(0.5)  WITHIN GROUP (ORDER BY c.duration_ms) AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY c.duration_ms) AS p95_ms,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY c.duration_ms) AS p99_ms,
            MAX(c.duration_ms)    AS max_ms,
            AVG(c.duration_ms)    AS avg_ms
        FROM call_logs c
        JOIN interfaces i ON i.id = c.interface_id
        WHERE c.called_at >= :since
        GROUP BY i.id, i.name, i.protocol, i.organization
        ORDER BY p95_ms DESC NULLS LAST
        """
    )
    rows = db.execute(sql, {"since": since}).mappings().all()

    out: list[PercentileRow] = []
    for r in rows:
        total = int(r["total_calls"] or 0)
        out.append(
            PercentileRow(
                interface_id=r["interface_id"],
                interface_name=r["interface_name"],
                protocol=r["protocol"],
                organization=r["organization"],
                total_calls=total,
                failure_count=int(r["failure_count"] or 0),
                p50_ms=float(r["p50_ms"] or 0),
                p95_ms=float(r["p95_ms"] or 0),
                p99_ms=float(r["p99_ms"] or 0),
                max_ms=int(r["max_ms"] or 0),
                avg_ms=float(r["avg_ms"] or 0),
                throughput_per_min=round(total / window_minutes, 4),
            )
        )
    return out


@router.get("/slow-top", response_model=list[SlowCallRow])
def slow_top(
    limit: int = Query(10, ge=1, le=100),
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
) -> list[SlowCallRow]:
    since = now_kst() - timedelta(days=days)
    rows = db.execute(
        select(
            CallLog.id,
            CallLog.interface_id,
            Interface.name.label("interface_name"),
            Interface.protocol.label("protocol"),
            CallLog.duration_ms,
            CallLog.status,
            CallLog.http_status,
            CallLog.called_at,
        )
        .join(Interface, Interface.id == CallLog.interface_id)
        .where(CallLog.called_at >= since)
        .order_by(CallLog.duration_ms.desc())
        .limit(limit)
    ).mappings().all()

    return [
        SlowCallRow(
            id=r["id"],
            interface_id=r["interface_id"],
            interface_name=r["interface_name"],
            protocol=str(r["protocol"]).split(".")[-1] if not isinstance(r["protocol"], str) else r["protocol"],
            duration_ms=r["duration_ms"],
            status=str(r["status"]).split(".")[-1] if not isinstance(r["status"], str) else r["status"],
            http_status=r["http_status"],
            called_at=r["called_at"],
        )
        for r in rows
    ]


@router.get("/throughput", response_model=list[ThroughputPoint])
def throughput(
    bucket_minutes: int = Query(15, ge=1, le=60),
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
) -> list[ThroughputPoint]:
    """TPS + p95 latency per time bucket — overall (all interfaces)."""
    since = now_kst() - timedelta(hours=hours)
    rows = db.scalars(select(CallLog).where(CallLog.called_at >= since)).all()

    width = timedelta(minutes=bucket_minutes)
    buckets: dict[datetime, list[CallLog]] = {}
    for r in rows:
        delta = r.called_at - since
        idx = int(delta.total_seconds() // (bucket_minutes * 60))
        key = since + idx * width
        buckets.setdefault(key, []).append(r)

    out: list[ThroughputPoint] = []
    seconds_per_bucket = bucket_minutes * 60
    for key in sorted(buckets):
        bucket = buckets[key]
        durations = sorted(r.duration_ms for r in bucket)
        if not durations:
            continue
        idx95 = max(0, int(len(durations) * 0.95) - 1)
        out.append(
            ThroughputPoint(
                bucket=key,
                tps=round(len(bucket) / seconds_per_bucket, 3),
                p95_ms=float(durations[idx95]),
            )
        )
    return out