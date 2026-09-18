"""Typed parameter objects for the signal engine.

The engine is a pure computation layer: it never reads ``config.yaml``, never
touches the database and never imports the API. Instead every tunable arrives
through one of the frozen dataclasses below, which the application layer builds
from ``config.yaml`` (see ``backend.settings.Settings.engine_params``).

None of these fields carry defaults. That is deliberate — a default here would
be a magic number living outside ``config.yaml``, and the two copies would drift.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ATRParams:
    period: int


@dataclass(frozen=True, slots=True)
class BetaParams:
    window: int
    residual_base: float


@dataclass(frozen=True, slots=True)
class ChannelParams:
    horizons: tuple[int, ...]
    state_horizon: int
    clip: float
    ewm_span_floor: int
    ewm_span_divisor: int

    def ewm_span(self, horizon: int) -> int:
        """Smoothing span for a given channel horizon: max(floor, N // divisor)."""
        return max(self.ewm_span_floor, horizon // self.ewm_span_divisor)


@dataclass(frozen=True, slots=True)
class RVolParams:
    span: int


@dataclass(frozen=True, slots=True)
class BreadthParams:
    horizon: int
    narrow_threshold: float


@dataclass(frozen=True, slots=True)
class StateParams:
    pending_entry: float
    pending_exit: float
    confirmed_exit: float
    confirm_consecutive_closes: int
    confirm_rvol: float
    fail_window_bars: int
    failed_cooldown_bars: int


@dataclass(frozen=True, slots=True)
class DispersionParams:
    smoothing_window: int
    percentile_window: int
    low_threshold: float


@dataclass(frozen=True, slots=True)
class CorrelationParams:
    window: int
    sparkline_bars: int


@dataclass(frozen=True, slots=True)
class RegimeParams:
    dispersion: DispersionParams
    correlation: CorrelationParams


@dataclass(frozen=True, slots=True)
class RRGParams:
    window: int
    origin: float
    offset: float
    tail_length: int


@dataclass(frozen=True, slots=True)
class EngineParams:
    """Everything the engine needs, in one object."""

    atr: ATRParams
    beta: BetaParams
    channel: ChannelParams
    rvol: RVolParams
    breadth: BreadthParams
    state: StateParams
    regime: RegimeParams
    rrg: RRGParams


@dataclass(frozen=True, slots=True)
class SectorSpec:
    """One sector: its cap-weight ETF, display name, and equal-weight twin."""

    symbol: str
    name: str
    equal_weight: str | None = None


@dataclass(frozen=True, slots=True)
class UniverseSpec:
    benchmark: str
    sectors: tuple[SectorSpec, ...]

    @property
    def sector_symbols(self) -> tuple[str, ...]:
        return tuple(s.symbol for s in self.sectors)

    @property
    def equal_weight_symbols(self) -> tuple[str, ...]:
        return tuple(s.equal_weight for s in self.sectors if s.equal_weight)

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return (self.benchmark, *self.sector_symbols, *self.equal_weight_symbols)

    def by_symbol(self, symbol: str) -> SectorSpec:
        for s in self.sectors:
            if s.symbol == symbol:
                return s
        raise KeyError(f"{symbol} is not a sector in this universe")
