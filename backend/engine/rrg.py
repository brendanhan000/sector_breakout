"""Relative Rotation Graph coordinates.

    RS          = 100 * (Close_sector / Close_SPY)
    RS_Ratio    = 101 + (RS - SMA(RS, 60)) / StDev(RS, 60)
    RS_Momentum = 101 + (RS_Ratio - SMA(RS_Ratio, 60)) / StDev(RS_Ratio, 60)

NOTE ON PROVENANCE: this is a documented reconstruction of the standard RRG
normalisation from its published description — a z-score of relative strength
against its own trailing window, offset so that the neutral point sits at 100,
then the same transform applied a second time to produce the momentum axis. It
is NOT the proprietary commercial RRG implementation (RRG(R) is a registered
trademark of RRG Research), and the two will not agree digit for digit. The
quadrant geometry and the rotational reading are the same; the exact smoothing
constants used commercially are not public.

Quadrants, with the origin at (100, 100):

    x > 100, y > 100   Leading     green
    x > 100, y < 100   Weakening   yellow
    x < 100, y < 100   Lagging     red
    x < 100, y > 100   Improving   blue

NOTE ON THE OFFSET (101) VS THE ORIGIN (100): these differ by exactly one
standard-deviation unit, so the formula's neutral value does not coincide with
the quadrant origin. A sector sitting precisely at its own trailing mean relative
strength scores 101 and therefore reads as marginally Leading rather than dead
centre; in practice a sector must fall more than 1 sigma below its trailing mean
before an axis registers it as Lagging. This is what the specified normalisation
produces, and it biases every point up and to the right. Both constants are in
config.yaml (rrg.offset, rrg.origin) — setting offset to 100.0 makes neutral and
origin coincide, at the cost of departing from the specified formula.

Healthy rotation traces a counterclockwise loop:
Improving -> Leading -> Weakening -> Lagging -> Improving. Which is why the
tails matter: a static scatter tells you where a sector sits, the tail tells you
which way it is travelling, and the direction is the trade.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

from .params import RRGParams

__all__ = [
    "Quadrant",
    "relative_strength",
    "normalise",
    "rrg_coordinates",
    "classify_quadrant",
    "rrg_tail",
]


class Quadrant(StrEnum):
    LEADING = "LEADING"
    WEAKENING = "WEAKENING"
    LAGGING = "LAGGING"
    IMPROVING = "IMPROVING"


def relative_strength(sector_close: pd.Series, benchmark_close: pd.Series) -> pd.Series:
    """RS = 100 * sector / benchmark, aligned on the sector's index."""
    sector = sector_close.astype("float64")
    bench = benchmark_close.reindex(sector.index).astype("float64")
    rs = 100.0 * (sector / bench.where(bench > 0))
    rs.name = "rs"
    return rs


def normalise(series: pd.Series, params: RRGParams) -> pd.Series:
    """offset + (x - SMA(x, w)) / StDev(x, w).

    A flat window has zero stdev and therefore no defined z-score; the result is
    the neutral offset itself rather than an infinity.
    """
    w = params.window
    mean = series.rolling(w, min_periods=w).mean()
    std = series.rolling(w, min_periods=w).std(ddof=1)

    out = params.offset + (series - mean) / std.where(std > 0)
    # Zero-variance window: the series sits exactly at its own mean, so the
    # normalised value is the neutral offset rather than an infinity.
    flat = std.notna() & (std <= 0)
    out[flat] = params.offset
    return out


def rrg_coordinates(
    sector_close: pd.Series, benchmark_close: pd.Series, params: RRGParams
) -> pd.DataFrame:
    """RS_Ratio / RS_Momentum time series plus the quadrant on each bar."""
    rs = relative_strength(sector_close, benchmark_close)
    ratio = normalise(rs, params)
    momentum = normalise(ratio, params)

    out = pd.DataFrame({"rs": rs, "rs_ratio": ratio, "rs_momentum": momentum})
    out["quadrant"] = [
        classify_quadrant(x, y, params) for x, y in zip(out["rs_ratio"], out["rs_momentum"])
    ]
    return out


def classify_quadrant(x: float, y: float, params: RRGParams) -> str | None:
    """Quadrant for one (RS_Ratio, RS_Momentum) point, or None if undefined."""
    if x is None or y is None or np.isnan(x) or np.isnan(y):
        return None
    o = params.origin
    if x > o:
        return Quadrant.LEADING if y > o else Quadrant.WEAKENING
    return Quadrant.IMPROVING if y > o else Quadrant.LAGGING


def rrg_tail(coords: pd.DataFrame, params: RRGParams) -> pd.DataFrame:
    """The trailing ``tail_length`` defined points, oldest first."""
    defined = coords.dropna(subset=["rs_ratio", "rs_momentum"])
    return defined.tail(params.tail_length)
