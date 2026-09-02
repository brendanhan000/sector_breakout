"""APScheduler wiring: one daily job.

Deliberately the entire scheduling layer. The workload is 23 symbols once a day;
a message bus or a streaming framework here would be architecture for its own
sake, and it would add failure modes that a single cron-shaped job does not have.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.jobs.refresh import run_refresh
from backend.settings import Settings

log = logging.getLogger(__name__)


def build_scheduler(settings: Settings) -> BackgroundScheduler | None:
    """Return a started scheduler, or None when scheduling is disabled."""
    config = settings.config.scheduler
    if not config.enabled:
        log.info("scheduler disabled in config.yaml")
        return None

    scheduler = BackgroundScheduler(timezone=config.timezone)
    scheduler.add_job(
        _scheduled_refresh,
        trigger=CronTrigger(
            day_of_week="mon-fri", hour=config.hour, minute=config.minute,
            timezone=config.timezone,
        ),
        args=[settings],
        id="daily_refresh",
        name="Daily sector refresh",
        # If the process was down at the scheduled time, run once on startup
        # rather than skipping the day entirely.
        misfire_grace_time=60 * 60 * 6,
        coalesce=True,        # one catch-up run, not one per missed interval
        max_instances=1,      # never overlap two refreshes
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "scheduler started: weekdays at %02d:%02d %s",
        config.hour, config.minute, config.timezone,
    )
    return scheduler


def _scheduled_refresh(settings: Settings) -> None:
    try:
        run_id = run_refresh(settings, trigger="scheduled")
        log.info("scheduled refresh completed: %s", run_id)
    except Exception:
        # Never let an exception escape into APScheduler's thread: it would kill
        # the job and the dashboard would silently stop updating.
        log.exception("scheduled refresh failed")
