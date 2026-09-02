"""Wilder's Average True Range.

    TR_t  = max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|)
    ATR_t = (ATR_{t-1} * (N - 1) + TR_t) / N

Seeded with the simple mean of the first N true ranges, which is Wilder's own
convention and matches ``adjust=False`` recursive semantics (no re-weighting of
history as new bars arrive). Everything before the seed bar is NaN rather than
a partially-formed value, so no downstream consumer can accidentally use an
ATR computed from fewer than N bars.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["true_range", "wilder_atr"]


def true_range(df: pd.DataFrame) -> pd.Series:
    """True range series for an OHLC frame.

    The first bar has no previous close, so its true range degenerates to the
    high-low span rather than being NaN — otherwise the seed mean would be
    undefined and ATR would start one bar later than Wilder intended.
    """
    high = df["high"].astype("float64")
    low = df["low"].astype("float64")
    prev_close = df["close"].astype("float64").shift(1)

    hl = high - low
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()

    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1, skipna=True)
    tr.name = "true_range"
    return tr


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder ATR over ``period`` bars.

    Seeds from the first window of ``period`` CONSECUTIVE valid true ranges,
    which is not necessarily the start of the series. This matters: the
    residual (Plane B) frame is NaN for the whole beta warm-up window, so a
    version that only seeded from bar 0 returned an all-NaN ATR for the entire
    relative plane — which silently made z_up/z_dn undefined everywhere and left
    the rotation leaderboard sorting on nothing at all.

    Still strictly causal: the seed depends only on bars at or before the seed
    bar, and appending future bars cannot move it. If the chain is later broken
    by an interior NaN, the series waits for a fresh complete window and
    re-seeds rather than propagating NaN forever.
    """
    if period < 1:
        raise ValueError(f"ATR period must be >= 1, got {period}")

    tr = true_range(df).to_numpy(dtype="float64", copy=True)
    n = tr.shape[0]
    out = np.full(n, np.nan, dtype="float64")

    if n < period:
        return pd.Series(out, index=df.index, name=f"atr_{period}")

    seeded = False
    for i in range(period - 1, n):
        if not seeded:
            window = tr[i - period + 1 : i + 1]
            if not np.isnan(window).any():
                out[i] = window.mean()
                seeded = True
            continue

        if np.isnan(tr[i]):
            # Lost the chain. Wait for a fresh complete window rather than
            # poisoning every subsequent bar.
            seeded = False
            continue

        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period

    return pd.Series(out, index=df.index, name=f"atr_{period}")
