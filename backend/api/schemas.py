"""Pydantic v2 response models.

Two rules hold across every payload:

  * EVERY response carries ``as_of`` and ``data_quality``. A number on a trading
    dashboard without a date is not information, and a consumer must always be
    able to tell a fully-computed day from a degraded one.
  * A partially computed day is never served. ``as_of`` comes from the last run
    that actually completed, so a crashed or half-finished refresh leaves the
    dashboard showing yesterday rather than an inconsistent today.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

Plane = Literal["absolute", "relative"]
DataQualityLiteral = Literal["OK", "WARMING_UP", "DEGRADED", "STALE"]


class Envelope(BaseModel):
    """Fields every payload carries."""

    as_of: dt.date | None = Field(description="Latest fully-computed trading day")
    data_quality: DataQualityLiteral = Field(description="OK | WARMING_UP | DEGRADED | STALE")


# --------------------------------------------------------------------------- #
class HealthResponse(Envelope):
    status: Literal["ok", "degraded", "empty"]
    database: bool
    latest_run_id: str | None = None
    last_run_at: dt.datetime | None = None


class BenchmarkView(BaseModel):
    symbol: str
    state: str | None
    bars_in_state: int | None


class SparkPoint(BaseModel):
    date: dt.date
    value: float | None


class RegimeResponse(Envelope):
    dispersion: float | None
    dispersion_raw: float | None
    dispersion_percentile: float | None
    low_dispersion: bool
    low_dispersion_threshold: float
    correlation: float | None
    correlation_sparkline: list[SparkPoint]
    benchmark: BenchmarkView
    message: str | None = Field(
        default=None,
        description=(
            "Set when the relative plane should be visually suppressed. In a "
            "low-dispersion regime every Plane B signal is noise."
        ),
    )


# --------------------------------------------------------------------------- #
class HorizonPoint(BaseModel):
    """One horizon of the term structure. Never averaged into a composite —
    the SHAPE across horizons is the diagnostic."""

    horizon: int
    signal: float | None
    c: float | None
    z_up: float | None
    z_dn: float | None
    channel_max: float | None
    channel_min: float | None


class PlaneView(BaseModel):
    plane: Plane
    state: str | None
    bars_in_state: int | None
    term: list[HorizonPoint]
    rvol: float | None
    narrow: bool
    breadth_spread: float | None
    equal_weight_signal: float | None
    z_up: float | None = Field(description="Extension at the state horizon, in ATR units")
    z_dn: float | None
    close: float | None


class SectorRow(BaseModel):
    symbol: str
    name: str
    equal_weight: str
    quarantined: bool = False
    absolute: PlaneView | None
    relative: PlaneView | None
    beta: float | None
    disagreement: bool = Field(
        description=(
            "True when the two planes point in opposite directions. These are "
            "the actionable rows and the reason the dashboard has two planes."
        )
    )
    quadrant: str | None


class SectorsResponse(Envelope):
    state_horizon: int
    horizons: list[int]
    low_dispersion: bool
    sectors: list[SectorRow]


# --------------------------------------------------------------------------- #
class HistoryPoint(BaseModel):
    date: dt.date
    close: float | None
    high: float | None
    low: float | None
    residual: float | None
    beta: float | None
    rvol: float | None
    state: str | None
    bars_in_state: int | None
    entered: bool = False
    from_state: str | None = None
    term: dict[str, dict[str, float | None]]


class HistoryResponse(Envelope):
    symbol: str
    name: str
    plane: Plane
    days: int
    series: list[HistoryPoint]
    transitions: list[HistoryPoint] = Field(
        default_factory=list,
        description="State changes only, including past FAILED_* events.",
    )


# --------------------------------------------------------------------------- #
class RRGTailPoint(BaseModel):
    date: dt.date
    rs_ratio: float
    rs_momentum: float
    quadrant: str | None


class RRGSeries(BaseModel):
    symbol: str
    name: str
    rs_ratio: float | None
    rs_momentum: float | None
    quadrant: str | None
    tail: list[RRGTailPoint] = Field(
        description="Oldest-first. Direction of travel is the trade; a static point is not."
    )


class RRGResponse(Envelope):
    origin: float
    tail_length: int
    series: list[RRGSeries]


# --------------------------------------------------------------------------- #
class LeaderboardEntry(BaseModel):
    rank: int
    symbol: str
    name: str
    plane: Plane
    z_up: float | None
    z_dn: float | None
    signal: float | None
    state: str | None
    bars_in_state: int | None
    narrow: bool
    disagreement: bool


class LeaderboardResponse(Envelope):
    plane: Plane
    horizon: int
    low_dispersion: bool
    entries: list[LeaderboardEntry]


# --------------------------------------------------------------------------- #
class RefreshResponse(BaseModel):
    run_id: str
    status: str
    accepted_at: dt.datetime


class RunResponse(BaseModel):
    run_id: str
    status: str
    trigger: str
    provider: str
    started_at: dt.datetime
    finished_at: dt.datetime | None
    as_of: dt.date | None
    data_quality: str
    symbols_requested: int
    symbols_ingested: int
    bars_upserted: int
    quarantined: list[dict]
    warnings: list[dict]
    repulled: list[str]
    error: str | None
