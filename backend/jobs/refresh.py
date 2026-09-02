"""The daily refresh: ingest -> compute -> persist, recorded in run_log.

One job, run once a day, over 23 symbols. There is no queue, no broker and no
stream, because the workload does not have any of the problems those solve.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.data.ingest import build_provider, ingest_universe, load_many
from backend.db.models import DataQuality, RunLog, RunStatus
from backend.db.persist import persist_pipeline, purge_run
from backend.engine.pipeline import compute_all
from backend.settings import Settings, get_settings

log = logging.getLogger(__name__)


def _warming_up(settings: Settings, bars: dict) -> bool:
    """True when the history is too short for every statistic to be defined.

    The binding constraint is the RRG: RS_Momentum normalises RS_Ratio, so it
    needs 2*window - 1 bars before its first point exists. The dispersion
    percentile needs its full 3-year window on top of the smoothing window.
    """
    config = settings.config
    needed = max(
        2 * config.rrg.window - 1,
        config.regime.dispersion.percentile_window + config.regime.dispersion.smoothing_window,
        max(config.engine.channel.horizons) + config.engine.beta.window,
    )
    longest = max((len(frame) for frame in bars.values()), default=0)
    return longest < needed


def run_refresh(
    settings: Settings | None = None,
    session: Session | None = None,
    *,
    today: dt.date | None = None,
    trigger: str = "manual",
    session_factory=None,
    run_id: str | None = None,
) -> str:
    """Execute one refresh. Returns the run_id.

    The run_log row is written BEFORE any work starts, so a crash mid-run leaves
    a RUNNING row rather than no evidence at all.

    ``run_id`` may be supplied by a caller that has already registered the row —
    the API does exactly that, so a client can poll GET /api/runs/{run_id}
    immediately instead of racing the background task into existence.
    """
    settings = settings or get_settings()
    today = today or dt.date.today()
    run_id = run_id or str(uuid.uuid4())

    if session is not None:
        return _execute(settings, session, today, trigger, run_id)

    if session_factory is None:
        from backend.db.session import get_sessionmaker

        session_factory = get_sessionmaker(settings.database_url)

    with session_factory() as owned:
        try:
            result = _execute(settings, owned, today, trigger, run_id)
            owned.commit()
            return result
        except Exception:
            owned.rollback()
            raise


def register_run(session: Session, trigger: str) -> str:
    """Insert a RUNNING row and return its id, so callers can poll immediately."""
    run_id = str(uuid.uuid4())
    session.add(RunLog(run_id=run_id, status=RunStatus.RUNNING.value, trigger=trigger))
    session.flush()
    return run_id


def _execute(
    settings: Settings, session: Session, today: dt.date, trigger: str, run_id: str
) -> str:
    config = settings.config
    universe = settings.universe
    provider = build_provider(config, settings)

    # The caller may already have registered this run so that it is pollable.
    entry = session.get(RunLog, run_id)
    if entry is None:
        entry = RunLog(run_id=run_id)
        session.add(entry)
    entry.status = RunStatus.RUNNING.value
    entry.trigger = trigger
    entry.provider = provider.name
    entry.symbols_requested = len(universe.all_symbols)
    session.flush()

    try:
        report = ingest_universe(
            session, provider, list(universe.all_symbols), config, today=today
        )
        session.flush()

        entry.symbols_ingested = len(report.ingested)
        entry.bars_upserted = report.bars_upserted
        entry.quarantined = report.quarantined
        entry.warnings = report.warnings
        entry.repulled = report.repulled

        bars = load_many(session, list(universe.all_symbols))
        bars = {symbol: frame for symbol, frame in bars.items() if not frame.empty}

        result = compute_all(
            bars, universe, settings.engine_params, quarantined=report.quarantined_symbols
        )
        persist_pipeline(session, result, run_id)

        entry.as_of = result.as_of.date()
        entry.data_quality = _quality(report, settings, bars).value
        entry.status = (
            RunStatus.PARTIAL.value if report.quarantined else RunStatus.SUCCESS.value
        )
        entry.finished_at = dt.datetime.now(dt.timezone.utc)
        session.flush()
        log.info(
            "run %s finished: status=%s as_of=%s quality=%s",
            run_id, entry.status, entry.as_of, entry.data_quality,
        )
        return run_id

    except Exception as exc:
        log.exception("run %s failed", run_id)
        # Remove any partial output. A half-written day must never be served.
        purge_run(session, run_id)
        entry.status = RunStatus.FAILED.value
        entry.error = repr(exc)[:4000]
        entry.finished_at = dt.datetime.now(dt.timezone.utc)
        session.flush()
        raise


def _quality(report, settings: Settings, bars: dict) -> DataQuality:
    if report.quarantined:
        return DataQuality.DEGRADED
    if _warming_up(settings, bars):
        return DataQuality.WARMING_UP
    return DataQuality.OK


def latest_run(session: Session) -> RunLog | None:
    return session.execute(
        select(RunLog)
        .where(RunLog.status.in_([RunStatus.SUCCESS.value, RunStatus.PARTIAL.value]))
        .order_by(RunLog.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()
