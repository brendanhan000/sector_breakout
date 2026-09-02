"""SQLAlchemy 2.x ORM models.

Six tables:

    bars      raw adjusted OHLCV, the only table the providers write
    signals   engine output per (symbol, date, plane)
    states    state machine output per (symbol, date, plane)
    regime    one row per date, market-wide
    rrg       relative rotation coordinates per (symbol, date)
    run_log   one row per refresh, including what was quarantined and why

``rrg`` is a sixth table beyond the five named in the specification. RRG
coordinates are per (symbol, date) but are NOT plane-specific, so folding them
into ``signals`` would mean either duplicating each row across both planes or
leaving half of them NULL. A small dedicated table is the honest shape.

Every computed table carries ``run_id``, so a bad run can be identified and
rolled back rather than being silently mixed into good history.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .types import PortableJSON


class Base(DeclarativeBase):
    pass


class Plane(str, enum.Enum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"


class RunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class DataQuality(str, enum.Enum):
    OK = "OK"
    WARMING_UP = "WARMING_UP"      # not enough history for every statistic yet
    DEGRADED = "DEGRADED"          # at least one symbol quarantined
    STALE = "STALE"                # last successful run is older than expected


# --------------------------------------------------------------------------- #
# Raw market data
# --------------------------------------------------------------------------- #
class Bar(Base):
    """One split/dividend-adjusted daily bar.

    (symbol, date) is the primary key — a natural composite rather than a
    surrogate id. That is what makes ingest idempotent: re-running a day updates
    in place rather than duplicating, and there is no second key that could
    disagree with the first.
    """

    __tablename__ = "bars"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)

    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("high >= low", name="ck_bars_high_ge_low"),
        CheckConstraint("volume >= 0", name="ck_bars_volume_nonneg"),
        Index("ix_bars_symbol_date", "symbol", "date"),
    )


# --------------------------------------------------------------------------- #
# Engine output
# --------------------------------------------------------------------------- #
class Signal(Base):
    """Channel term structure and confirmation filters for one symbol/date/plane.

    The per-horizon values live in the ``term`` JSON column keyed by horizon:

        {"10": {"c":…, "signal":…, "max":…, "min":…, "z_up":…, "z_dn":…}, …}

    Storing them this way rather than as ``signal_10``/``signal_20``/… columns
    means changing ``engine.channel.horizons`` in config.yaml does not require a
    migration. It also makes it structurally impossible to add a "composite"
    column by accident, which the specification explicitly forbids.
    """

    __tablename__ = "signals"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    plane: Mapped[str] = mapped_column(String(16), primary_key=True)

    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("run_log.run_id"), nullable=False)

    term: Mapped[dict] = mapped_column(PortableJSON, nullable=False)

    rvol: Mapped[float | None] = mapped_column(Float)
    beta: Mapped[float | None] = mapped_column(Float)
    residual_close: Mapped[float | None] = mapped_column(Float)
    breadth_spread: Mapped[float | None] = mapped_column(Float)
    equal_weight_signal: Mapped[float | None] = mapped_column(Float)
    narrow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("plane in ('absolute','relative')", name="ck_signals_plane"),
        Index("ix_signals_symbol_plane_date", "symbol", "plane", "date"),
        Index("ix_signals_date", "date"),
    )


class SectorState(Base):
    """State machine output. ``entered`` marks a transition bar.

    FAILED_* rows are never deleted or overwritten with NEUTRAL — the history of
    failed breakouts is the highest-quality reversal signal the system produces,
    so it has to survive.
    """

    __tablename__ = "states"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    plane: Mapped[str] = mapped_column(String(16), primary_key=True)

    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("run_log.run_id"), nullable=False)

    state: Mapped[str] = mapped_column(String(24), nullable=False)
    bars_in_state: Mapped[int] = mapped_column(Integer, nullable=False)
    entered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    from_state: Mapped[str | None] = mapped_column(String(24))

    consec_above: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consec_below: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("plane in ('absolute','relative')", name="ck_states_plane"),
        CheckConstraint("bars_in_state >= 0", name="ck_states_age_nonneg"),
        Index("ix_states_symbol_plane_date", "symbol", "plane", "date"),
        Index("ix_states_entered", "entered", "date"),
    )


class RRGPoint(Base):
    """Relative rotation coordinates. Plane-independent by construction."""

    __tablename__ = "rrg"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)

    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("run_log.run_id"), nullable=False)

    rs: Mapped[float | None] = mapped_column(Float)
    rs_ratio: Mapped[float | None] = mapped_column(Float)
    rs_momentum: Mapped[float | None] = mapped_column(Float)
    quadrant: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        Index("ix_rrg_symbol_date", "symbol", "date"),
    )


class Regime(Base):
    """Market-wide regime. One row per date, computed once, not per sector."""

    __tablename__ = "regime"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)

    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("run_log.run_id"), nullable=False)

    dispersion_raw: Mapped[float | None] = mapped_column(Float)
    dispersion: Mapped[float | None] = mapped_column(Float)
    dispersion_percentile: Mapped[float | None] = mapped_column(Float)
    correlation: Mapped[float | None] = mapped_column(Float)
    low_dispersion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    benchmark_state: Mapped[str | None] = mapped_column(String(24))
    benchmark_bars_in_state: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (Index("ix_regime_date", "date"),)


# --------------------------------------------------------------------------- #
# Operational
# --------------------------------------------------------------------------- #
class RunLog(Base):
    """One row per refresh. The API's ``data_quality`` flag is derived from here.

    A run that quarantined symbols is PARTIAL, not SUCCESS. The dashboard must
    be able to say "this is degraded" rather than quietly serving a day computed
    from nine sectors instead of eleven.
    """

    __tablename__ = "run_log"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RunStatus.RUNNING.value)
    trigger: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")

    as_of: Mapped[dt.date | None] = mapped_column(Date)
    data_quality: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataQuality.OK.value
    )

    symbols_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbols_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bars_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    quarantined: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    repulled: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_run_log_started", "started_at"),)
