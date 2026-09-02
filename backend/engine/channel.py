"""Continuous Donchian channel position, and the ATR-normalised extension.

    max_N  = High.rolling(N).max().shift(1)     <-- the .shift(1) is mandatory
    min_N  = Low.rolling(N).min().shift(1)
    mid    = (max_N + min_N) / 2
    width  = max_N - min_N
    c_t    = (Close_t - mid) / (0.5 * width)
    c_t    = clip(c_t, -clip, +clip)
    signal = c.ewm(span=max(2, N // 4), adjust=False).mean()

THE .shift(1) IS THE SINGLE MOST IMPORTANT LINE IN THIS CODEBASE.

``High.rolling(N).max()`` at bar t includes bar t's own high. Comparing bar t's
close against a channel that already contains bar t is circular: the bar can
never close meaningfully above its own high, and any breakout you "detect" was
constructed from information that did not exist when the bar opened. The shift
makes the channel a statement about the N bars *before* t, which is the only
version of the question a trader can actually act on.

There is a dedicated regression test for this (tests/test_no_lookahead.py).
Never remove the shift to "make the signal more responsive".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .atr import wilder_atr
from .params import ChannelParams

__all__ = ["donchian_levels", "channel_position", "extension", "channel_frame"]


def donchian_levels(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Prior-N-bar Donchian high/low/mid/width, excluding the current bar."""
    if horizon < 1:
        raise ValueError(f"channel horizon must be >= 1, got {horizon}")

    high = df["high"].astype("float64")
    low = df["low"].astype("float64")

    # .shift(1): the channel describes the N bars ENDING AT t-1.
    max_n = high.rolling(horizon, min_periods=horizon).max().shift(1)
    min_n = low.rolling(horizon, min_periods=horizon).min().shift(1)

    return pd.DataFrame(
        {
            "max_n": max_n,
            "min_n": min_n,
            "mid": (max_n + min_n) / 2.0,
            "width": max_n - min_n,
        }
    )


def channel_position(
    df: pd.DataFrame, horizon: int, params: ChannelParams
) -> pd.DataFrame:
    """Raw and EWM-smoothed channel position for one horizon.

    Returns columns ``c`` (clipped raw position) and ``signal`` (smoothed).
    """
    levels = donchian_levels(df, horizon)
    close = df["close"].astype("float64")

    half_width = 0.5 * levels["width"]

    # A degenerate flat window (width == 0) carries no positional information.
    # 0.0 is the correct reading — the close is, trivially, at the middle of a
    # zero-width range. Returning NaN would poison the EWM for every later bar
    # and inf would blow up every downstream comparison.
    raw = pd.Series(np.nan, index=df.index, dtype="float64")
    usable = levels["width"].notna() & (levels["width"] > 0)
    raw.loc[usable] = (close[usable] - levels["mid"][usable]) / half_width[usable]

    flat = levels["width"].notna() & (levels["width"] <= 0)
    raw.loc[flat] = 0.0

    c = raw.clip(lower=-params.clip, upper=params.clip)

    # adjust=False -> a true recursive EWM. Each value depends only on itself
    # and the running state, never on how many bars happen to follow it, which
    # is what keeps the series stable under truncation.
    signal = c.ewm(span=params.ewm_span(horizon), adjust=False).mean()

    out = pd.DataFrame({"c": c, "signal": signal})
    out.index = df.index
    return out


def extension(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """ATR-normalised distance beyond the channel edge.

        z_up = (Close_t - max_N) / ATR_N
        z_dn = (min_N - Close_t) / ATR_N

    Channel position saturates at the clip bound and cannot tell "just poked
    through" from "20% beyond the channel". Extension can, and because it is
    measured in ATR units it is directly comparable across XLU and XLK — which
    is what makes it a valid cross-sectional sort key for the leaderboard.
    """
    levels = donchian_levels(df, horizon)
    close = df["close"].astype("float64")
    atr = wilder_atr(df, horizon)

    # Zero ATR means a completely motionless window; the normalisation is
    # undefined rather than infinite.
    denom = atr.where(atr > 0)

    return pd.DataFrame(
        {
            "z_up": (close - levels["max_n"]) / denom,
            "z_dn": (levels["min_n"] - close) / denom,
        }
    )


def channel_frame(df: pd.DataFrame, params: ChannelParams) -> pd.DataFrame:
    """Full term structure across every configured horizon.

    Columns are suffixed by horizon (``signal_10``, ``z_up_55``, ...). The three
    horizons are stored side by side and are NEVER averaged into a composite:
    the *shape* of the term structure is the diagnostic.

        all three positive, rising      -> established trend
        short positive, long negative   -> early reversal or bounce
        short negative, long positive   -> pullback inside an uptrend
        all three pinned near the clip  -> extended, expect mean reversion

    Averaging collapses all four of those into one number and destroys exactly
    the information the panel exists to show.
    """
    out = pd.DataFrame(index=df.index)
    for horizon in params.horizons:
        pos = channel_position(df, horizon, params)
        lev = donchian_levels(df, horizon)
        ext = extension(df, horizon)
        out[f"c_{horizon}"] = pos["c"]
        out[f"signal_{horizon}"] = pos["signal"]
        out[f"max_{horizon}"] = lev["max_n"]
        out[f"min_{horizon}"] = lev["min_n"]
        out[f"z_up_{horizon}"] = ext["z_up"]
        out[f"z_dn_{horizon}"] = ext["z_dn"]
    return out
