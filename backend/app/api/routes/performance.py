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
    deleted_at: datetime | None = None


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


class RetryEffectRow(BaseModel):
    """인터페이스별 자동 재시도 효과 분석.

    재시도 정책 (retry_max>0) 이 실제로 의미가 있는지를 운영자가 한눈에
    보기 위한 통계. attempt_count > 1 인 호출 중 최종 성공한 비율 = "복구율".
    복구율이 낮으면 재시도가 무의미 → backoff 늘리거나 retry_max 줄이기 권장.
    """
    interface_id: int
    interface_name: str
    organization: str | None = None
    retry_max: int
    retry_backoff_seconds: float
    timeout_seconds: float | None
    total_calls: int
    single_attempt_calls: int   # attempt_count == 1 (재시도 없음)
    multi_attempt_calls: int    # attempt_count > 1 (재시도 발생)
    recovered_calls: int        # 재시도 후 SUCCESS — 재시도 덕분에 살아난 호출
    failed_after_retry: int     # 재시도해도 결국 실패
    avg_attempts: float         # 호출당 평균 시도 횟수
    deleted_at: datetime | None = None


class RetryEffectSummary(BaseModel):
    """전체 합산 KPI — 페이지 헤더용."""
    days: int
    total_calls: int
    multi_attempt_calls: int
    recovered_calls: int
    failed_after_retry: int
    # "재시도 도입 덕분에 추가 성공" — multi_attempt 중 SUCCESS 비율
    recovery_rate: float
    # 재시도 정책이 설정된 인터페이스 수 (retry_max > 0)
    interfaces_with_retry: int
    interfaces_total: int


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
            i.deleted_at    AS deleted_at,
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
        GROUP BY i.id, i.name, i.protocol, i.organization, i.deleted_at
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
                deleted_at=r["deleted_at"],
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


@router.get("/retry-effects", response_model=list[RetryEffectRow])
def retry_effects(
    days: int = Query(7, ge=1, le=30),
    only_with_policy: bool = Query(False, description="retry_max > 0 인 인터페이스만"),
    db: Session = Depends(get_db),
) -> list[RetryEffectRow]:
    """인터페이스별 자동 재시도 효과 분석.

    각 인터페이스에 대해:
      - 단일 시도 호출 vs 재시도 발생 호출
      - 재시도 후 복구된 호출 (multi_attempt + SUCCESS)
      - 재시도해도 실패한 호출
      - 평균 시도 횟수

    REST/SOAP 만 자동 재시도 적용 — SFTP/MQ/BATCH 는 멱등성 문제로 attempt_count 항상 1.
    """
    since = now_kst() - timedelta(days=days)

    sql = text(
        """
        SELECT
            i.id              AS interface_id,
            i.name            AS interface_name,
            i.organization    AS organization,
            i.retry_max       AS retry_max,
            i.retry_backoff_seconds AS retry_backoff_seconds,
            i.timeout_seconds AS timeout_seconds,
            i.deleted_at      AS deleted_at,
            COUNT(c.id)       AS total_calls,
            SUM(CASE WHEN c.attempt_count = 1 THEN 1 ELSE 0 END) AS single_attempt_calls,
            SUM(CASE WHEN c.attempt_count > 1 THEN 1 ELSE 0 END) AS multi_attempt_calls,
            SUM(CASE WHEN c.attempt_count > 1 AND c.status = 'SUCCESS' THEN 1 ELSE 0 END) AS recovered_calls,
            SUM(CASE WHEN c.attempt_count > 1 AND c.status <> 'SUCCESS' THEN 1 ELSE 0 END) AS failed_after_retry,
            COALESCE(AVG(c.attempt_count), 1.0) AS avg_attempts
        FROM interfaces i
        LEFT JOIN call_logs c
          ON c.interface_id = i.id AND c.called_at >= :since
        WHERE i.deleted_at IS NULL
        GROUP BY i.id, i.name, i.organization, i.retry_max,
                 i.retry_backoff_seconds, i.timeout_seconds, i.deleted_at
        ORDER BY recovered_calls DESC NULLS LAST, multi_attempt_calls DESC NULLS LAST
        """
    )
    rows = db.execute(sql, {"since": since}).mappings().all()

    out: list[RetryEffectRow] = []
    for r in rows:
        if only_with_policy and (r["retry_max"] or 0) == 0:
            continue
        out.append(
            RetryEffectRow(
                interface_id=r["interface_id"],
                interface_name=r["interface_name"],
                organization=r["organization"],
                retry_max=int(r["retry_max"] or 0),
                retry_backoff_seconds=float(r["retry_backoff_seconds"] or 1.0),
                timeout_seconds=float(r["timeout_seconds"]) if r["timeout_seconds"] is not None else None,
                total_calls=int(r["total_calls"] or 0),
                single_attempt_calls=int(r["single_attempt_calls"] or 0),
                multi_attempt_calls=int(r["multi_attempt_calls"] or 0),
                recovered_calls=int(r["recovered_calls"] or 0),
                failed_after_retry=int(r["failed_after_retry"] or 0),
                avg_attempts=float(r["avg_attempts"] or 1.0),
                deleted_at=r["deleted_at"],
            )
        )
    return out


@router.get("/retry-effects/summary", response_model=RetryEffectSummary)
def retry_effects_summary(
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
) -> RetryEffectSummary:
    """전체 재시도 효과 KPI — 페이지 헤더용."""
    since = now_kst() - timedelta(days=days)
    row = db.execute(text(
        """
        SELECT
            COUNT(*) AS total_calls,
            SUM(CASE WHEN attempt_count > 1 THEN 1 ELSE 0 END) AS multi_attempt_calls,
            SUM(CASE WHEN attempt_count > 1 AND status = 'SUCCESS' THEN 1 ELSE 0 END) AS recovered_calls,
            SUM(CASE WHEN attempt_count > 1 AND status <> 'SUCCESS' THEN 1 ELSE 0 END) AS failed_after_retry
        FROM call_logs WHERE called_at >= :since
        """
    ), {"since": since}).mappings().one()

    itf_total = db.scalar(select(func.count(Interface.id)).where(Interface.deleted_at.is_(None))) or 0
    itf_with_retry = db.scalar(
        select(func.count(Interface.id))
        .where(Interface.deleted_at.is_(None))
        .where(Interface.retry_max > 0)
    ) or 0

    multi = int(row["multi_attempt_calls"] or 0)
    recovered = int(row["recovered_calls"] or 0)
    return RetryEffectSummary(
        days=days,
        total_calls=int(row["total_calls"] or 0),
        multi_attempt_calls=multi,
        recovered_calls=recovered,
        failed_after_retry=int(row["failed_after_retry"] or 0),
        recovery_rate=(recovered / multi) if multi > 0 else 0.0,
        interfaces_with_retry=int(itf_with_retry),
        interfaces_total=int(itf_total),
    )


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