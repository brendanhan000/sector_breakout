"""Synthetic series builders shared across the test suite.

Kept out of conftest.py so test modules can import them directly (conftest is
not an importable package module under pytest's default layout).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_START = "2015-01-02"


def make_ohlcv(
    close: np.ndarray,
    *,
    volume: np.ndarray | None = None,
    wiggle: float = 0.0,
    start: str = TRADING_START,
) -> pd.DataFrame:
    """Build an OHLCV frame from a close path.

    ``wiggle`` is the fractional half-range of the intrabar high/low around the
    close. wiggle=0 gives high == low == close, which makes analytically-derived
    channel assertions exact.
    """
    close = np.asarray(close, dtype="float64")
    n = close.shape[0]
    idx = pd.bdate_range(start, periods=n)
    high = close * (1.0 + wiggle)
    low = close * (1.0 - wiggle)
    if volume is None:
        volume = np.full(n, 1_000_000.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.asarray(volume, dtype="float64"),
        },
        index=idx,
    )


def random_walk(seed: int, n: int = 1000, sigma: float = 0.01, drift: float = 0.0) -> np.ndarray:
    """Geometric random walk. drift=0 gives a driftless (null-hypothesis) path."""
    g = np.random.default_rng(seed)
    return 100.0 * np.exp(np.cumsum(g.normal(drift, sigma, n)))


def step_series(n: int, jump_at: int, low: float = 100.0, high: float = 130.0) -> np.ndarray:
    """Flat, one clean jump, flat again. The unambiguous breakout."""
    out = np.full(n, low, dtype="float64")
    out[jump_at:] = high
    return out


def ramp_series(n: int, start_level: float = 100.0, slope: float = 0.25) -> np.ndarray:
    """Linear ramp. Every bar is a new high — a permanent, uninterrupted trend."""
    return start_level + slope * np.arange(n, dtype="float64")


def sine_series(n: int, period: int = 120, level: float = 100.0, amp: float = 15.0) -> np.ndarray:
    """Pure cycle. Alternates between up and down extremes, never trends."""
    t = np.arange(n, dtype="float64")
    return level + amp * np.sin(2.0 * np.pi * t / period)
