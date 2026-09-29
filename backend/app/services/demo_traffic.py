"""시연용 합성 트래픽 생성기 — 실제 운영 중인 관제 시스템처럼 호출 이력을 계속 쌓음.

시드 인터페이스의 대상 기관(KIDI/KCIS/토스 등) endpoint 는 실존하지 않거나 외부에서
호출 불가라, 실제 호출만으로는 성공률이 0% 에 수렴하고 며칠만 지나도 대시보드·토폴로지가
"데이터 없음" 이 됨. DEMO_TRAFFIC_ENABLED=true 일 때:

- 백엔드 기동 시 마지막 합성 호출 이후 공백 구간을 채움 (최대 WINDOW_DAYS)
- 이후 TICK_SECONDS 마다 인터페이스별 트래픽 프로파일(시드 rps/실패율/지연)대로 호출 생성
  → triggered_by='ingest' (업무 시스템이 호출 결과를 보고한 것과 같은 경로)
- 가끔 인터페이스 하나에 장애 구간(5xx 급증/타임아웃/지연) 발생 → incident 열림 →
  구간 종료 시 원인·조치와 함께 자동 해결. 전체 성공률은 95% 이상 유지
- REST/SOAP 수동·스케줄 실행도 simulate_call() 로 대체 (executor 참조)

시드 스크립트(app.scripts.seed_demo)도 과거 이력을 simulate_range() 로 만들어
과거 이력과 실시간 트래픽의 밀도·분포가 끊김 없이 이어짐.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from croniter import croniter
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.time import KST, now_kst
from app.core.websocket import ws_manager
from app.models import CallLog, Incident, Interface
from app.models.call_log import CallStatus
from app.models.incident import IncidentType
from app.models.interface import InterfaceDirection

log = logging.getLogger("noahub.demo_traffic")

TRIGGER = "ingest"
TICK_SECONDS = 30
WINDOW_DAYS = 60          # 공백 채우기 최대 범위 (= 시드 이력 기간)
RETENTION_DAYS = 90       # 합성 호출 보관 기간 — DB 무한 증가 방지
STALE_INCIDENT_HOURS = 3  # 이보다 오래 열린 호출성 incident 는 자동 해결 (시연 환경 한정)
OUTAGE_PER_HOUR = 0.008   # 인터페이스당 시간당 장애 구간 발생 확률 (전체 기준 하루 1~2건)
OUTAGE_MIN_RATE = 3.0     # 장애 구간은 시간당 호출 3건 이상 인터페이스만 — 관련 호출 0건 incident 방지
BURST_PER_HOUR = 40.0     # 장애 구간 중 확정 실패 호출 발생률 (재시도 폭주 재현)
RETRY_RECOVER_RATE = 0.6  # 5xx/timeout 중 자동 재시도로 복구되는 비율

_DEFAULT_PROFILE = {"rps": 0.0, "fail_rate": 0.02, "latency_ms": (300, 100)}

# 장애 구간 유형 — (incident 유형, 심각도, 강제 실패 종류, 요약, 원인, 조치)
_OUTAGE_KINDS: list[tuple[IncidentType, str, str | None, str, str, str]] = [
    (
        IncidentType.SERVER_ERROR, "critical", "SERVER",
        "{org} 측 5xx 응답 급증",
        "상대 기관 WAS 배포 직후 커넥션 풀 고갈로 502/503 응답이 연속 발생",
        "상대 기관 운영팀 통보 후 롤백 완료, 실패 건은 자동 재시도로 재처리",
    ),
    (
        IncidentType.TIMEOUT, "warning", "TIMEOUT",
        "{org} 응답 지연으로 타임아웃 다발",
        "상대 기관 전용회선 구간 패킷 손실로 응답이 타임아웃 한도(10s)를 초과",
        "회선사업자 우회 경로 전환 후 정상화, 미처리 건 재처리 완료",
    ),
    (
        IncidentType.SLOW_RESPONSE, "warning", None,
        "{org} 평균 응답시간 3배 이상 증가",
        "상대 기관 야간 배치와 조회 트래픽이 겹치며 DB 락 대기 증가",
        "상대 기관 배치 시간 조정 합의, 응답시간 평시 수준 회복 확인",
    ),
    (
        IncidentType.HIGH_FAILURE_RATE, "critical", None,
        "{org} 실패율 임계치(10%) 초과",
        "상대 기관 인증 게이트웨이 일시 장애로 인증·처리 오류가 혼재",
        "게이트웨이 재기동 후 실패율 1% 미만으로 회복, 모니터링 유지",
    ),
]


@dataclass
class _Outage:
    start: datetime
    end: datetime
    kind: int            # _OUTAGE_KINDS 인덱스
    incident_id: int


# interface_id → 진행 중인 장애 구간 (프로세스 메모리)
_active: dict[int, _Outage] = {}


def _profiles() -> dict[str, dict]:
    from app.scripts.seed_demo import SEED_INTERFACES  # 순환 import 회피
    return {p["name"]: p for p in SEED_INTERFACES}


def _hourly_rate(itf: Interface, prof: dict) -> float:
    """인터페이스 시간당 평균 호출 수. cron 전용 배치(저빈도)는 스케줄러가 담당."""
    rps = prof.get("rps", 0.0)
    if itf.schedule_cron and rps < 1:
        return 0.0
    # 저빈도(금감원 보고 등)도 7일 창에 몇 건은 남도록 하한 (하루 1건 남짓)
    return max(min(rps * 0.6, 12.0), 0.05)


def _diurnal(hour: int) -> float:
    """KST 시간대별 트래픽 가중치 (업무시간 집중, 새벽 저조). 하루 평균 ≈ 1."""
    if 9 <= hour < 19:
        return 1.6
    if 19 <= hour < 24:
        return 0.8
    if 7 <= hour < 9:
        return 1.0
    return 0.3


def _poisson(lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(random.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= random.random()
        if p <= limit:
            return k
        k += 1


def _outage_at(itf_id: int, ts: datetime) -> _Outage | None:
    o = _active.get(itf_id)
    if o and o.start <= ts < o.end:
        return o
    return None


# incident 유형 → 장애 구간 호출의 강제 실패 종류 (None = 유형 혼재)
_TYPE_TO_KIND: dict[IncidentType, str | None] = {
    IncidentType.SERVER_ERROR: "SERVER",
    IncidentType.TIMEOUT: "TIMEOUT",
    IncidentType.AUTH_ERROR: "AUTH",
    IncidentType.FORMAT_ERROR: "FORMAT",
    IncidentType.HIGH_FAILURE_RATE: None,
}


def _outcome(
    itf: Interface, prof: dict, ts: datetime,
    mode: IncidentType | None = None, force_fail: bool = False,
):
    """한 번의 호출 결과 → (status, http, err, err_type, trace, latency, attempts).

    mode: 장애 구간 유형 (없으면 진행 중인 장애 구간에서 자동 판단)
    force_fail: 장애 구간 폭주 호출 — 재시도 복구 없이 확정 실패 (SLOW 는 느린 성공)
    """
    from app.scripts.seed_demo import _classify_log

    mean, _std = prof.get("latency_ms", _DEFAULT_PROFILE["latency_ms"])
    fail_rate = prof.get("fail_rate", _DEFAULT_PROFILE["fail_rate"])
    latency_mul = 1.0

    if mode is None and (outage := _outage_at(itf.id, ts)) is not None:
        mode = _OUTAGE_KINDS[outage.kind][0]
    forced_kind = _TYPE_TO_KIND.get(mode) if mode else None
    if mode == IncidentType.SLOW_RESPONSE:
        latency_mul = 4.0
        fail_rate = max(fail_rate, 0.03)
    elif mode is not None:
        fail_rate = 1.0 if force_fail else 0.45
        latency_mul = 2.0

    latency = max(5, int(random.lognormvariate(math.log(max(mean * latency_mul, 1)), 0.4)))
    if mode == IncidentType.SLOW_RESPONSE:
        latency = max(latency, 3200 + random.randint(0, 2500))  # detector 임계치(3s) 초과
    success = random.random() > fail_rate
    status, http, err, err_type, trace = _classify_log(success, latency, prof, kind=forced_kind)

    # 자동 재시도 — 5xx/timeout 일부는 재시도로 복구 (RetryAnalytics 데이터)
    attempts = 1
    if (
        not success
        and not force_fail
        and itf.retry_max
        and status in (CallStatus.SERVER_ERROR, CallStatus.TIMEOUT)
    ):
        attempts = random.randint(2, itf.retry_max + 1)
        if random.random() < RETRY_RECOVER_RATE:
            success = True
            status, http, err, err_type, trace = _classify_log(True, latency, prof)
            latency *= attempts
    if status == CallStatus.TIMEOUT:
        latency = max(latency, 10_000)
    return status, http, err, err_type, trace, latency, attempts


def _make_log(
    itf: Interface, prof: dict, ts: datetime, trigger: str = TRIGGER,
    mode: IncidentType | None = None, force_fail: bool = False,
) -> CallLog:
    from app.scripts.seed_demo import _failure_response, _payloads_for, _trace_id

    status, http, err, err_type, trace, latency, attempts = _outcome(
        itf, prof, ts, mode=mode, force_fail=force_fail
    )
    req_body, succ_body = _payloads_for(itf.name)
    return CallLog(
        interface_id=itf.id,
        request={
            "method": itf.method,
            "endpoint": itf.endpoint,
            "headers": {"X-Request-ID": _trace_id(), "Content-Type": "application/json"},
            "body": req_body if req_body is not None else itf.request_template,
        },
        response=succ_body if status == CallStatus.SUCCESS else _failure_response(status),
        status=status,
        http_status=http,
        duration_ms=latency,
        error_message=err,
        error_type=err_type,
        error_trace=trace,
        triggered_by=trigger,
        called_at=ts,
        attempt_count=attempts,
        retry_count=0,
        is_reprocessed=False,
    )


def burst_logs(
    itf: Interface, prof: dict, mode: IncidentType, start: datetime, end: datetime, n: int
) -> list[CallLog]:
    """장애 구간의 실패 폭주 호출 n건 — incident 에 관련 호출이 반드시 묶이도록."""
    span = max((end - start).total_seconds(), 1)
    return [
        _make_log(
            itf, prof, start + timedelta(seconds=random.uniform(0, span)),
            mode=mode, force_fail=True,
        )
        for _ in range(n)
    ]


def _open_outage(db: Session, itf: Interface, at: datetime) -> None:
    kind = random.randrange(len(_OUTAGE_KINDS))
    itype, severity, _forced, summary, *_ = _OUTAGE_KINDS[kind]
    inc = Incident(
        interface_id=itf.id,
        type=itype,
        severity=severity,
        summary=summary.format(org=itf.organization or itf.name),
        detected_at=at,
    )
    db.add(inc)
    db.flush()
    _active[itf.id] = _Outage(
        start=at, end=at + timedelta(minutes=random.randint(15, 90)),
        kind=kind, incident_id=inc.id,
    )


def _close_outages(db: Session, until: datetime) -> list[Incident]:
    from app.services.incident_helpers import mark_related_handled

    closed: list[Incident] = []
    for itf_id, o in list(_active.items()):
        if o.end > until:
            continue
        del _active[itf_id]
        inc = db.get(Incident, o.incident_id)
        if inc is None or inc.resolved_at is not None:
            continue
        _t, _s, _f, _summary, root_cause, resolution = _OUTAGE_KINDS[o.kind]
        inc.resolved_at = o.end
        inc.root_cause = root_cause
        inc.resolution = resolution
        mark_related_handled(db, inc, db.get(Interface, itf_id))
        closed.append(inc)
    return closed


def _targets(db: Session) -> list[tuple[Interface, dict]]:
    profiles = _profiles()
    rows = db.scalars(
        select(Interface)
        .where(Interface.enabled.is_(True))
        .where(Interface.deleted_at.is_(None))
    ).all()
    return [(i, profiles[i.name]) for i in rows if i.name in profiles]


def _cron_times(cron: str, start: datetime, end: datetime) -> list[datetime]:
    try:
        it = croniter(cron, start.astimezone(KST) - timedelta(microseconds=1))
    except (ValueError, KeyError):
        return []
    out = []
    while (ts := it.get_next(datetime)) < end:
        out.append(ts)
    return out


def simulate_range(
    db: Session, start: datetime, end: datetime, *, include_cron: bool = False
) -> list[CallLog]:
    """[start, end) 구간의 합성 호출·장애 구간을 생성해 커밋. 생성된 호출 목록 반환.

    include_cron=True 면 스케줄 인터페이스의 cron 실행 이력도 생성 — 시드·기동 시
    공백 채우기용 (그 구간엔 스케줄러가 돌지 않았음). 실시간 tick 에서는 실제
    스케줄러가 실행하므로 False.
    """
    targets = _targets(db)
    if not targets or start >= end:
        return []
    created: list[CallLog] = []
    t = start
    while t < end:
        seg_end = min(t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1), end)
        seg_sec = (seg_end - t).total_seconds()
        frac = seg_sec / 3600
        mult = _diurnal(t.astimezone(KST).hour)
        rows: list[CallLog] = []
        for itf, prof in targets:
            if include_cron and itf.schedule_cron and itf.direction != InterfaceDirection.INBOUND:
                for ts in _cron_times(itf.schedule_cron, t, seg_end):
                    rows.append(_make_log(itf, prof, ts, trigger="schedule"))
            rate = _hourly_rate(itf, prof)
            if rate <= 0:
                continue
            if (
                rate >= OUTAGE_MIN_RATE
                and itf.id not in _active
                and random.random() < OUTAGE_PER_HOUR * frac
            ):
                _open_outage(db, itf, t + timedelta(seconds=random.uniform(0, seg_sec)))
            for _ in range(_poisson(rate * mult * frac)):
                ts = t + timedelta(seconds=random.uniform(0, seg_sec))
                rows.append(_make_log(itf, prof, ts))
            if (o := _active.get(itf.id)) is not None:
                b_start, b_end = max(o.start, t), min(o.end, seg_end)
                if b_start < b_end:
                    n = _poisson(BURST_PER_HOUR * (b_end - b_start).total_seconds() / 3600)
                    mode = _OUTAGE_KINDS[o.kind][0]
                    rows.extend(burst_logs(itf, prof, mode, b_start, b_end, n))
        db.add_all(rows)
        db.flush()
        _close_outages(db, seg_end)
        created.extend(rows)
        t = seg_end
    db.commit()
    return created


# ---------------------------------------------------------------------------
# executor 연동 — REST/SOAP 실행을 프로파일 기반 결과로 대체
# ---------------------------------------------------------------------------

async def simulate_call(itf: Interface) -> tuple[int | None, dict | None, Exception | None, int]:
    """_exec_with_retry 와 같은 반환형 (http_status, response, exc, attempts)."""
    from app.scripts.seed_demo import _failure_response, _payloads_for

    prof = _profiles().get(itf.name, _DEFAULT_PROFILE)
    status, http, err, _et, _tr, latency, attempts = _outcome(itf, prof, now_kst())
    await asyncio.sleep(min(latency, 3000) / 1000)
    if status == CallStatus.TIMEOUT:
        return None, None, httpx.ReadTimeout(err or "timed out"), attempts
    if status == CallStatus.SUCCESS:
        _req, body = _payloads_for(itf.name)
        return 200, {"headers": {"content-type": "application/json"}, "body": body}, None, attempts
    return http, _failure_response(status), None, attempts


# ---------------------------------------------------------------------------
# 백그라운드 루프
# ---------------------------------------------------------------------------

def _resolve_stale_incidents(db: Session) -> None:
    """detector 가 연 호출성 incident 가 시연 환경에서 영원히 열려있지 않도록 정리."""
    from app.services.incident_helpers import mark_related_handled

    cutoff = now_kst() - timedelta(hours=STALE_INCIDENT_HOURS)
    active_ids = {o.incident_id for o in _active.values()}
    stale = db.scalars(
        select(Incident)
        .where(Incident.resolved_at.is_(None))
        .where(Incident.detected_at < cutoff)
        .where(Incident.type != IncidentType.SECRET_EXPIRY_WARNING)
    ).all()
    for inc in stale:
        if inc.id in active_ids:
            continue
        inc.resolved_at = now_kst()
        inc.resolution = inc.resolution or "이후 연속 정상 호출 확인 — 일시 장애로 판단해 자동 해결"
        mark_related_handled(db, inc, db.get(Interface, inc.interface_id))
    if stale:
        db.commit()


def _prune(db: Session) -> None:
    cutoff = now_kst() - timedelta(days=RETENTION_DAYS)
    db.execute(
        delete(CallLog)
        .where(CallLog.triggered_by == TRIGGER)
        .where(CallLog.called_at < cutoff)
    )
    db.commit()


def _backfill() -> datetime:
    db = SessionLocal()
    try:
        now = now_kst()
        last = db.scalar(
            select(func.max(CallLog.called_at)).where(CallLog.triggered_by == TRIGGER)
        )
        start = max(last, now - timedelta(days=WINDOW_DAYS)) if last else now
        n = len(simulate_range(db, start, now, include_cron=True))
        if n:
            log.info("demo traffic backfill: %d call(s) since %s", n, start.isoformat())
        _resolve_stale_incidents(db)
        _prune(db)
        return now
    finally:
        db.close()


def _tick(since: datetime) -> tuple[datetime, list[dict], list[dict]]:
    db = SessionLocal()
    try:
        now = now_kst()
        opened_before = {o.incident_id for o in _active.values()}
        rows = simulate_range(db, since, now)
        names = {i.id: i.name for i, _ in _targets(db)}
        calls = [
            {
                "id": r.id,
                "interface_id": r.interface_id,
                "interface_name": names.get(r.interface_id),
                "status": r.status.value,
                "http_status": r.http_status,
                "duration_ms": r.duration_ms,
                "called_at": r.called_at.isoformat(),
                "triggered_by": TRIGGER,
            }
            for r in sorted(rows, key=lambda r: r.called_at)
        ]
        incidents = []
        for o in _active.values():
            if o.incident_id in opened_before:
                continue
            inc = db.get(Incident, o.incident_id)
            if inc is None:
                continue
            incidents.append({
                "id": inc.id,
                "interface_id": inc.interface_id,
                "interface_name": names.get(inc.interface_id),
                "type": inc.type.value,
                "severity": inc.severity,
                "summary": inc.summary,
                "detected_at": inc.detected_at.isoformat(),
                "should_alert": True,
                "muted": False,
                "muted_until": None,
                "silenced": False,
                "silenced_reason": None,
                "effective_channels": ["in_app"],
            })
        _resolve_stale_incidents(db)
        return now, calls, incidents
    finally:
        db.close()


async def run_forever() -> None:
    try:
        since = await asyncio.to_thread(_backfill)
    except Exception:  # noqa: BLE001
        log.exception("demo traffic backfill failed")
        since = now_kst()
    ticks = 0
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            since, calls, incidents = await asyncio.to_thread(_tick, since)
            for payload in calls:
                await ws_manager.broadcast("call_log", payload)
            for payload in incidents:
                await ws_manager.broadcast("incident", payload)
            ticks += 1
            if ticks % 120 == 0:  # 1시간마다 보관기간 정리
                await asyncio.to_thread(_prune_once)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("demo traffic tick failed")


def _prune_once() -> None:
    db = SessionLocal()
    try:
        _prune(db)
    finally:
        db.close()
