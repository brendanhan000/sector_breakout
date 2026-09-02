"""Orchestration for the signal engine.

Still pure: dict of OHLCV frames in, computed frames out. No database, no HTTP,
no clock, no filesystem. Everything here is a deterministic function of its
arguments, which is what makes the no-look-ahead test meaningful — if this layer
could reach outside itself, truncating the input would not be a real experiment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .beta import log_returns, residual_ohlc, residual_returns, residual_series, rolling_beta
from .channel import channel_frame
from .filters import breadth_spread, narrow_flag, relative_volume
from .params import EngineParams, SectorSpec, UniverseSpec
from .rrg import rrg_coordinates
from .regime import compute_regime
from .state import State, run_state_machine

__all__ = ["PlaneResult", "SectorResult", "PipelineResult", "compute_plane", "compute_sector", "compute_all"]

ABSOLUTE = "absolute"
RELATIVE = "relative"


@dataclass(slots=True)
class PlaneResult:
    """One sector on one plane: channel term structure + state machine output."""

    plane: str
    frame: pd.DataFrame          # channel term structure, rvol, state, bars_in_state
    ohlcv: pd.DataFrame          # the OHLCV this plane was computed on

    @property
    def latest(self) -> pd.Series:
        return self.frame.iloc[-1]


@dataclass(slots=True)
class SectorResult:
    spec: SectorSpec
    absolute: PlaneResult
    relative: PlaneResult
    beta: pd.Series
    residual: pd.Series
    rrg: pd.DataFrame
    breadth: pd.DataFrame

    @property
    def symbol(self) -> str:
        return self.spec.symbol

    @property
    def disagreement(self) -> bool:
        """True when the two planes point in opposite directions.

        These are the actionable rows and the entire reason the dashboard has
        two planes at all. XLU breaking out absolute while breaking DOWN
        relative is defensive drift inside a rally; XLE breaking out relative
        while breaking down absolute is genuine rotation into energy during a
        selloff. Eleven green lights on an up day is one bit of information
        displayed eleven times — the disagreements are the signal.
        """
        a = self.absolute.frame["state"].iloc[-1]
        r = self.relative.frame["state"].iloc[-1]
        if a is None or r is None:
            return False
        return State(a).bias * State(r).bias < 0


@dataclass(slots=True)
class PipelineResult:
    as_of: pd.Timestamp
    universe: UniverseSpec
    sectors: dict[str, SectorResult]
    regime: pd.DataFrame
    benchmark_state: pd.DataFrame
    quarantined: tuple[str, ...] = field(default=())


def compute_plane(
    plane: str, ohlcv: pd.DataFrame, real_volume: pd.Series, params: EngineParams
) -> PlaneResult:
    """Channel term structure + RVOL + state machine for one plane.

    ``real_volume`` is always the sector's ACTUAL traded volume, even on the
    relative plane where prices are synthetic. Volume is a plane-independent
    fact about the tape and the confirmation gate must see the real number.
    """
    frame = channel_frame(ohlcv, params.channel)
    frame["rvol"] = relative_volume(real_volume.reindex(ohlcv.index), params.rvol)

    h = params.channel.state_horizon
    machine = run_state_machine(
        signal=frame[f"signal_{h}"],
        close=ohlcv["close"],
        max_n=frame[f"max_{h}"],
        min_n=frame[f"min_{h}"],
        rvol=frame["rvol"],
        params=params.state,
    )
    frame["state"] = machine["state"]
    frame["bars_in_state"] = machine["bars_in_state"]
    frame["consec_above"] = machine["consec_above"]
    frame["consec_below"] = machine["consec_below"]
    frame["entered"] = machine["entered"]

    return PlaneResult(plane=plane, frame=frame, ohlcv=ohlcv)


def compute_sector(
    spec: SectorSpec,
    sector_ohlcv: pd.DataFrame,
    benchmark_ohlcv: pd.DataFrame,
    equal_weight_ohlcv: pd.DataFrame | None,
    params: EngineParams,
) -> SectorResult:
    """Everything for one sector, on both planes."""
    idx = sector_ohlcv.index
    bench = benchmark_ohlcv.reindex(idx)

    r_i = log_returns(sector_ohlcv["close"])
    r_m = log_returns(bench["close"])
    beta = rolling_beta(r_i, r_m, params.beta)
    eps = residual_returns(r_i, r_m, beta)
    residual = residual_series(eps, params.beta)
    rel_ohlcv = residual_ohlc(sector_ohlcv, residual)

    volume = sector_ohlcv["volume"]
    absolute = compute_plane(ABSOLUTE, sector_ohlcv, volume, params)
    relative = compute_plane(RELATIVE, rel_ohlcv, volume, params)

    # ---- breadth -----------------------------------------------------------
    # Breadth is measured on the ABSOLUTE plane for both planes' badges: the
    # question "is the whole sector moving, or four mega-caps?" is a question
    # about the sector's constituents, not about the residual construction.
    bh = params.breadth.horizon
    cap_signal = absolute.frame[f"signal_{bh}"]

    if equal_weight_ohlcv is not None and not equal_weight_ohlcv.empty:
        ew_frame = channel_frame(equal_weight_ohlcv.reindex(idx).dropna(how="all"), params.channel)
        ew_signal = ew_frame[f"signal_{bh}"].reindex(idx)
    else:
        ew_signal = pd.Series(np.nan, index=idx)

    breadth = pd.DataFrame({"cap_signal": cap_signal, "equal_signal": ew_signal})
    breadth["breadth_spread"] = breadth_spread(cap_signal, ew_signal)

    for plane_name, plane in ((ABSOLUTE, absolute), (RELATIVE, relative)):
        breaking = plane.frame["state"].map(
            lambda s: State(s).is_breaking_out if s is not None else False
        )
        flag = narrow_flag(cap_signal, ew_signal, breaking.astype(bool), params.breadth)
        breadth[f"narrow_{plane_name}"] = flag
        plane.frame["narrow"] = flag
        plane.frame["breadth_spread"] = breadth["breadth_spread"]

    rrg = rrg_coordinates(sector_ohlcv["close"], bench["close"], params.rrg)

    return SectorResult(
        spec=spec,
        absolute=absolute,
        relative=relative,
        beta=beta,
        residual=residual,
        rrg=rrg,
        breadth=breadth,
    )


def compute_all(
    bars: dict[str, pd.DataFrame],
    universe: UniverseSpec,
    params: EngineParams,
    quarantined: tuple[str, ...] = (),
) -> PipelineResult:
    """Run the whole engine over a set of OHLCV frames.

    ``quarantined`` symbols are excluded from computation entirely — a symbol
    whose data failed ingest validation must not produce signals, and must not
    silently contribute to the cross-sectional regime statistics either.
    """
    benchmark = universe.benchmark
    if benchmark not in bars or bars[benchmark].empty:
        raise ValueError(f"benchmark {benchmark} has no bars; cannot compute")
    if benchmark in quarantined:
        raise ValueError(f"benchmark {benchmark} is quarantined; refusing to compute")

    bench_ohlcv = bars[benchmark]

    sectors: dict[str, SectorResult] = {}
    for spec in universe.sectors:
        if spec.symbol in quarantined:
            continue
        sector_df = bars.get(spec.symbol)
        if sector_df is None or sector_df.empty:
            continue
        ew_df = None if spec.equal_weight in quarantined else bars.get(spec.equal_weight)
        sectors[spec.symbol] = compute_sector(spec, sector_df, bench_ohlcv, ew_df, params)

    if not sectors:
        raise ValueError("no sectors survived quarantine; refusing to compute a regime")

    # ---- regime, computed once over the surviving cross-section ------------
    returns = pd.DataFrame(
        {sym: log_returns(res.absolute.ohlcv["close"]) for sym, res in sectors.items()}
    )
    regime = compute_regime(returns, params.regime)

    benchmark_state = compute_plane(
        ABSOLUTE, bench_ohlcv, bench_ohlcv["volume"], params
    ).frame

    as_of = min(res.absolute.frame.index[-1] for res in sectors.values())
    as_of = min(as_of, benchmark_state.index[-1])

    return PipelineResult(
        as_of=pd.Timestamp(as_of),
        universe=universe,
        sectors=sectors,
        regime=regime,
        benchmark_state=benchmark_state,
        quarantined=tuple(quarantined),
    )
