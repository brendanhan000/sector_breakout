"""Read helpers between the ORM and the response models.

Kept apart from the route handlers so the shaping logic — in particular
"what counts as the latest complete day" — is testable without HTTP.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Bar, DataQuality, RRGPoint, Regime, RunLog, RunStatus, SectorState, Signal
from backend.engine.state import State
from backend.settings import Settings

COMPLETED = (RunStatus.SUCCESS.value, RunStatus.PARTIAL.value)

#: A dashboard showing data older than this is misleading even if the data is
#: internally consistent, so it is reported as STALE.
STALE_AFTER_DAYS = 5


def latest_completed_run(session: Session) -> RunLog | None:
    """The most recent run that finished. A FAILED or RUNNING run is invisible.

    This is what guarantees a partially computed day is never served: ``as_of``
    is only written once compute and persistence have both succeeded.
    """
    return session.execute(
        select(RunLog)
        .where(RunLog.status.in_(COMPLETED), RunLog.as_of.is_not(None))
        .order_by(RunLog.as_of.desc(), RunLog.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def resolve_envelope(session: Session, today: dt.date | None = None) -> tuple[dt.date | None, str]:
    """(as_of, data_quality) for every response."""
    run = latest_completed_run(session)
    if run is None or run.as_of is None:
        return None, DataQuality.WARMING_UP.value

    quality = run.data_quality
    today = today or dt.date.today()
    import numpy as np

    if int(np.busday_count(run.as_of, today)) > STALE_AFTER_DAYS:
        quality = DataQuality.STALE.value
    return run.as_of, quality


def signals_on(session: Session, as_of: dt.date) -> dict[tuple[str, str], Signal]:
    rows = session.execute(select(Signal).where(Signal.date == as_of)).scalars().all()
    return {(r.symbol, r.plane): r for r in rows}


def states_on(session: Session, as_of: dt.date) -> dict[tuple[str, str], SectorState]:
    rows = session.execute(select(SectorState).where(SectorState.date == as_of)).scalars().all()
    return {(r.symbol, r.plane): r for r in rows}


def closes_on(session: Session, as_of: dt.date) -> dict[str, float]:
    rows = session.execute(
        select(Bar.symbol, Bar.close).where(Bar.date == as_of)
    ).all()
    return {symbol: close for symbol, close in rows}


def regime_on(session: Session, as_of: dt.date) -> Regime | None:
    return session.execute(select(Regime).where(Regime.date == as_of)).scalar_one_or_none()


def correlation_series(session: Session, as_of: dt.date, bars: int) -> list[Regime]:
    rows = (
        session.execute(
            select(Regime)
            .where(Regime.date <= as_of)
            .order_by(Regime.date.desc())
            .limit(bars)
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def rrg_tail(session: Session, symbol: str, as_of: dt.date, length: int) -> list[RRGPoint]:
    rows = (
        session.execute(
            select(RRGPoint)
            .where(
                RRGPoint.symbol == symbol,
                RRGPoint.date <= as_of,
                RRGPoint.rs_ratio.is_not(None),
                RRGPoint.rs_momentum.is_not(None),
            )
            .order_by(RRGPoint.date.desc())
            .limit(length)
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def history(
    session: Session, symbol: str, plane: str, as_of: dt.date, days: int
) -> tuple[list[Signal], list[SectorState], dict[dt.date, Bar]]:
    signals = list(
        reversed(
            session.execute(
                select(Signal)
                .where(Signal.symbol == symbol, Signal.plane == plane, Signal.date <= as_of)
                .order_by(Signal.date.desc())
                .limit(days)
            )
            .scalars()
            .all()
        )
    )
    if not signals:
        return [], [], {}

    start = signals[0].date
    states = list(
        session.execute(
            select(SectorState)
            .where(
                SectorState.symbol == symbol,
                SectorState.plane == plane,
                SectorState.date >= start,
                SectorState.date <= as_of,
            )
            .order_by(SectorState.date)
        )
        .scalars()
        .all()
    )
    bars = {
        bar.date: bar
        for bar in session.execute(
            select(Bar).where(Bar.symbol == symbol, Bar.date >= start, Bar.date <= as_of)
        )
        .scalars()
        .all()
    }
    return signals, states, bars


def transition_history(
    session: Session, symbol: str, plane: str, as_of: dt.date, limit: int = 50
) -> list[SectorState]:
    """Past state changes, newest last. FAILED_* events are retained forever."""
    rows = (
        session.execute(
            select(SectorState)
            .where(
                SectorState.symbol == symbol,
                SectorState.plane == plane,
                SectorState.date <= as_of,
                SectorState.entered.is_(True),
            )
            .order_by(SectorState.date.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def planes_disagree(absolute: SectorState | None, relative: SectorState | None) -> bool:
    """Opposite directions on the two planes.

    XLU up on the absolute plane but down on the relative plane is defensive
    drift inside a rally. XLE down absolute but up relative is genuine rotation
    into energy during a selloff. Both are hidden entirely by a single-plane view.

    Compared by directional BIAS, not by breakout family: FAILED_UP began as an
    upside breakout but reads bearish, so a sector that is CONFIRMED_UP absolute
    and FAILED_UP relative genuinely does disagree.
    """
    if absolute is None or relative is None:
        return False
    return State(absolute.state).bias * State(relative.state).bias < 0


def quarantined_symbols(session: Session) -> set[str]:
    run = latest_completed_run(session)
    if run is None:
        return set()
    return {entry.get("symbol") for entry in (run.quarantined or []) if entry.get("symbol")}
