"""APScheduler integration — re-syncs cron jobs from the DB on startup and on demand."""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.time import KST
from app.models import Interface

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
        wanted: dict[str, tuple[int, str]] = {
            _job_id(itf.id): (itf.id, itf.schedule_cron)
            for itf in rows
            if itf.enabled and itf.schedule_cron
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
