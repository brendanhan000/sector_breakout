"""Confirmation filters: relative volume and breadth.

Both answer the same question from different angles — "is this breakout real, or
is it one line on a chart?"
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .params import BreadthParams, RVolParams

__all__ = ["relative_volume", "breadth_spread", "narrow_flag"]


def relative_volume(volume: pd.Series, params: RVolParams) -> pd.Series:
    """RVOL_t = Volume_t / EWM(Volume, span).shift(1)

    The ``.shift(1)`` matters for the same reason it matters in the channel: the
    baseline must not contain today's volume, or a huge volume day partially
    normalises away its own surge.
    """
    vol = volume.astype("float64")
    baseline = vol.ewm(span=params.span, adjust=False).mean().shift(1)

    # A zero baseline (a genuinely untraded stretch) makes the ratio undefined.
    rvol = vol / baseline.where(baseline > 0)
    rvol.name = "rvol"
    return rvol


def breadth_spread(cap_signal: pd.Series, equal_signal: pd.Series) -> pd.Series:
    """signal(cap-weight, N=20) - signal(equal-weight, N=20).

    Pound for pound the highest value-per-line-of-code measurement in the
    system. A positive spread means the cap-weight ETF is breaking out while the
    equal-weight twin is not, i.e. a handful of mega-caps are carrying the whole
    move and the "sector" is not participating.
    """
    cap, equal = cap_signal.align(equal_signal, join="left")
    spread = cap - equal
    spread.name = "breadth_spread"
    return spread


def narrow_flag(
    cap_signal: pd.Series,
    equal_signal: pd.Series,
    breaking_out: pd.Series,
    params: BreadthParams,
) -> pd.Series:
    """True where the cap-weight ETF is breaking out on a non-participating base.

    ``breaking_out`` is a boolean series (typically "state is PENDING_UP or
    CONFIRMED_UP"). When it is true and the equal-weight twin's signal is below
    the narrow threshold, this is four mega-caps moving, not a sector — and the
    UI surfaces it as its own badge rather than burying it in a tooltip.
    """
    _cap, equal = cap_signal.align(equal_signal, join="left")
    breaking, equal = breaking_out.align(equal, join="left")

    flag = breaking.fillna(False).astype(bool) & (equal < params.narrow_threshold)
    # Where the equal-weight twin has no data yet we cannot make the claim.
    flag = flag.where(equal.notna(), other=False)
    flag.name = "narrow"
    return flag.astype(bool)
