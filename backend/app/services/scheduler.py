"""APScheduler 통합 — 시작 시 + 인터페이스 변경 시 DB의 cron 잡을 재동기화.

CronTrigger 의 timezone 은 반드시 KST (UTC 면 운영자가 "매일 03시" 입력했는데
12시에 도는 사고 발생). 보관 처리(deleted_at) 또는 enabled=false 인 인터페이스는
sync_jobs 에서 자동 제외 → 사용자 액션 1번에 cron 자동 갱신.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.time import KST
from app.models import Interface
from app.models.interface import InterfaceDirection

log = logging.getLogger("noahub.scheduler")
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=KST)
    return _scheduler


async def _run_interface(interface_id: int) -> None:
    from app.services.executor import execute_interface  # avoid circular import

    db = SessionLocal()
    try:
        itf = db.get(Interface, interface_id)
        if not itf or not itf.enabled:
            return
        await execute_interface(itf, db, triggered_by="schedule")
    finally:
        db.close()


def _job_id(interface_id: int) -> str:
    return f"interface-{interface_id}"


def sync_jobs() -> None:
    """Reconcile scheduled jobs with the current DB state."""
    sched = get_scheduler()
    db = SessionLocal()
    try:
        rows = db.scalars(select(Interface)).all()
        # INBOUND 인터페이스는 우리가 능동 호출 안 하므로 cron 등록 X
        wanted: dict[str, tuple[int, str]] = {
            _job_id(itf.id): (itf.id, itf.schedule_cron)
            for itf in rows
            if itf.enabled and itf.schedule_cron
            and itf.direction != InterfaceDirection.INBOUND
        }
        for job in sched.get_jobs():
            if job.id not in wanted:
                sched.remove_job(job.id)
        for jid, (interface_id, cron) in wanted.items():
            try:
                trigger = CronTrigger.from_crontab(cron, timezone=KST)
            except ValueError:
                log.warning("invalid cron %r on interface %s", cron, interface_id)
                continue
            existing = sched.get_job(jid)
            if existing:
                existing.reschedule(trigger)
            else:
                sched.add_job(
                    _run_interface,
                    trigger=trigger,
                    id=jid,
                    args=[interface_id],
                    coalesce=True,
                    max_instances=1,
                )
    finally:
        db.close()


def start_scheduler() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
    sync_jobs()
    log.info("scheduler started with %d job(s)", len(sched.get_jobs()))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


async def schedule_resync() -> None:
    """Convenience for routes that mutate interfaces."""
    await asyncio.to_thread(sync_jobs)
