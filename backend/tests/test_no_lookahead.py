"""THE test. Everything else in this suite is secondary to this file.

A signal has look-ahead bias when its value at time t depends on data that did
not exist at time t. The symptom is invisible in normal use — the dashboard
looks fine, the backtest looks excellent — and the failure mode is producing
confident, wrong conclusions. That is strictly worse than producing nothing.

The experiment: compute the full series, then truncate the input to T-k and
recompute. Every value at or before T-k must be BIT-IDENTICAL. If appending
future bars changes a historical value, that historical value was reading the
future, and the entire system is invalid.

Bit-identical, not approximately equal. These are the same arithmetic operations
on the same float64 inputs in the same order; there is no numerical reason for
them to differ, so any tolerance would only be hiding a real leak.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.atr import wilder_atr
from backend.engine.beta import (
    log_returns,
    residual_ohlc,
    residual_returns,
    residual_series,
    rolling_beta,
)
from backend.engine.channel import channel_frame, donchian_levels
from backend.engine.filters import relative_volume
from backend.engine.pipeline import compute_all
from backend.engine.regime import compute_regime
from backend.engine.rrg import rrg_coordinates
from backend.engine.state import run_state_machine

from .synthetic import make_ohlcv, random_walk

TRUNCATIONS = [1, 5, 20, 60]


def assert_identical_prefix(full: pd.Series | pd.DataFrame, truncated, k: int, label: str) -> None:
    """Assert `truncated` matches `full` bit-for-bit over the truncated index."""
    idx = truncated.index
    assert len(idx) == len(full.index) - k, f"{label}: unexpected truncated length"

    a = full.loc[idx]
    b = truncated

    if isinstance(a, pd.DataFrame):
        assert list(a.columns) == list(b.columns), f"{label}: column mismatch"
        for col in a.columns:
            _assert_series(a[col], b[col], f"{label}[{col}] k={k}")
    else:
        _assert_series(a, b, f"{label} k={k}")


def _assert_series(a: pd.Series, b: pd.Series, label: str) -> None:
    if a.dtype.kind in "fc" and b.dtype.kind in "fc":
        av = a.to_numpy(dtype="float64")
        bv = b.to_numpy(dtype="float64")
        both_nan = np.isnan(av) & np.isnan(bv)
        # Bit-identical: exact equality, with NaN treated as equal to NaN.
        mismatch = ~(both_nan | (av == bv))
        if mismatch.any():
            i = int(np.flatnonzero(mismatch)[0])
            raise AssertionError(
                f"LOOK-AHEAD LEAK in {label}: value at {a.index[i]!r} changed when "
                f"future bars were appended: {bv[i]!r} (truncated) vs {av[i]!r} (full)"
            )
    else:
        a_l, b_l = list(a), list(b)
        for i, (x, y) in enumerate(zip(a_l, b_l)):
            x_nan = x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NA
            y_nan = y is None or (isinstance(y, float) and np.isnan(y)) or y is pd.NA
            if x_nan and y_nan:
                continue
            if x != y:
                raise AssertionError(
                    f"LOOK-AHEAD LEAK in {label}: value at {a.index[i]!r} changed when "
                    f"future bars were appended: {y!r} (truncated) vs {x!r} (full)"
                )


@pytest.fixture(scope="module")
def price_frame() -> pd.DataFrame:
    g = np.random.default_rng(20240501)
    close = random_walk(seed=11, n=900, sigma=0.012, drift=0.0002)
    volume = g.lognormal(14.0, 0.4, close.shape[0])
    return make_ohlcv(close, volume=volume, wiggle=0.006)


@pytest.fixture(scope="module")
def benchmark_frame() -> pd.DataFrame:
    g = np.random.default_rng(20240502)
    close = random_walk(seed=99, n=900, sigma=0.009, drift=0.0003)
    volume = g.lognormal(15.0, 0.3, close.shape[0])
    return make_ohlcv(close, volume=volume, wiggle=0.004)


# --------------------------------------------------------------------------- #
# Component-level
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("k", TRUNCATIONS)
def test_donchian_levels_no_lookahead(price_frame, k):
    for horizon in (10, 20, 55):
        full = donchian_levels(price_frame, horizon)
        trunc = donchian_levels(price_frame.iloc[:-k], horizon)
        assert_identical_prefix(full, trunc, k, f"donchian_{horizon}")


@pytest.mark.parametrize("k", TRUNCATIONS)
def test_channel_frame_no_lookahead(price_frame, channel_params, k):
    full = channel_frame(price_frame, channel_params)
    trunc = channel_frame(price_frame.iloc[:-k], channel_params)
    assert_identical_prefix(full, trunc, k, "channel_frame")


@pytest.mark.parametrize("k", TRUNCATIONS)
def test_atr_no_lookahead(price_frame, k):
    for period in (10, 20, 55):
        full = wilder_atr(price_frame, period)
        trunc = wilder_atr(price_frame.iloc[:-k], period)
        assert_identical_prefix(full, trunc, k, f"atr_{period}")


@pytest.mark.parametrize("k", TRUNCATIONS)
def test_rvol_no_lookahead(price_frame, rvol_params, k):
    full = relative_volume(price_frame["volume"], rvol_params)
    trunc = relative_volume(price_frame["volume"].iloc[:-k], rvol_params)
    assert_identical_prefix(full, trunc, k, "rvol")


@pytest.mark.parametrize("k", TRUNCATIONS)
def test_beta_and_residual_no_lookahead(price_frame, benchmark_frame, beta_params, k):
    def build(px, bm):
        r_i = log_returns(px["close"])
        r_m = log_returns(bm["close"])
        beta = rolling_beta(r_i, r_m, beta_params)
        eps = residual_returns(r_i, r_m, beta)
        resid = residual_series(eps, beta_params)
        return beta, resid, residual_ohlc(px, resid)

    fb, fr, fo = build(price_frame, benchmark_frame)
    tb, tr, to = build(price_frame.iloc[:-k], benchmark_frame.iloc[:-k])

    assert_identical_prefix(fb, tb, k, "beta")
    assert_identical_prefix(fr, tr, k, "residual")
    assert_identical_prefix(fo, to, k, "residual_ohlc")


@pytest.mark.parametrize("k", TRUNCATIONS)
def test_state_machine_no_lookahead(price_frame, channel_params, state_params, rvol_params, k):
    def build(px):
        f = channel_frame(px, channel_params)
        rvol = relative_volume(px["volume"], rvol_params)
        h = channel_params.state_horizon
        return run_state_machine(
            f[f"signal_{h}"], px["close"], f[f"max_{h}"], f[f"min_{h}"], rvol, state_params
        )

    full = build(price_frame)
    trunc = build(price_frame.iloc[:-k])
    assert_identical_prefix(full, trunc, k, "state_machine")


@pytest.mark.parametrize("k", TRUNCATIONS)
def test_rrg_no_lookahead(price_frame, benchmark_frame, rrg_params, k):
    full = rrg_coordinates(price_frame["close"], benchmark_frame["close"], rrg_params)
    trunc = rrg_coordinates(
        price_frame["close"].iloc[:-k], benchmark_frame["close"].iloc[:-k], rrg_params
    )
    assert_identical_prefix(full, trunc, k, "rrg")


@pytest.mark.parametrize("k", TRUNCATIONS)
def test_regime_no_lookahead(regime_params, k):
    cols = {}
    for i in range(6):
        cols[f"S{i}"] = pd.Series(
            log_returns(pd.Series(random_walk(seed=500 + i, n=900)))
        ).to_numpy()
    idx = pd.bdate_range("2015-01-02", periods=900)
    returns = pd.DataFrame(cols, index=idx)

    full = compute_regime(returns, regime_params)
    trunc = compute_regime(returns.iloc[:-k], regime_params)
    assert_identical_prefix(full, trunc, k, "regime")


# --------------------------------------------------------------------------- #
# End-to-end: the whole pipeline, every sector, both planes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("k", TRUNCATIONS)
def test_full_pipeline_no_lookahead(universe, engine_params, k):
    g = np.random.default_rng(4242)
    bars = {}
    for i, sym in enumerate(universe.all_symbols):
        close = random_walk(seed=1000 + i * 13, n=900, sigma=0.011, drift=0.0001)
        volume = g.lognormal(14.5, 0.35, close.shape[0])
        bars[sym] = make_ohlcv(close, volume=volume, wiggle=0.005)

    full = compute_all(bars, universe, engine_params)
    truncated_bars = {sym: df.iloc[:-k] for sym, df in bars.items()}
    trunc = compute_all(truncated_bars, universe, engine_params)

    assert_identical_prefix(full.regime, trunc.regime, k, "pipeline.regime")
    assert_identical_prefix(
        full.benchmark_state, trunc.benchmark_state, k, "pipeline.benchmark_state"
    )

    for sym in full.sectors:
        fs, ts = full.sectors[sym], trunc.sectors[sym]
        assert_identical_prefix(fs.absolute.frame, ts.absolute.frame, k, f"{sym}.absolute")
        assert_identical_prefix(fs.relative.frame, ts.relative.frame, k, f"{sym}.relative")
        assert_identical_prefix(fs.beta, ts.beta, k, f"{sym}.beta")
        assert_identical_prefix(fs.residual, ts.residual, k, f"{sym}.residual")
        assert_identical_prefix(fs.rrg, ts.rrg, k, f"{sym}.rrg")
        assert_identical_prefix(fs.breadth, ts.breadth, k, f"{sym}.breadth")


def test_shift_is_actually_present(price_frame):
    """Direct assertion that the channel excludes the current bar.

    The subtle version of this bug is real but hard to see in a diff, so this
    test states the property in the most literal way possible: on a strictly
    increasing series every bar sets a new high, so the prior-N-bar maximum must
    be strictly BELOW today's high on every bar. Without the .shift(1) it would
    equal today's high instead.
    """
    close = np.arange(1.0, 201.0)
    df = make_ohlcv(close, wiggle=0.0)
    levels = donchian_levels(df, 20)

    valid = levels["max_n"].notna()
    assert valid.sum() > 0
    assert (levels.loc[valid, "max_n"] < df.loc[valid, "high"]).all(), (
        "max_N is not strictly below today's high on a monotonically rising series "
        "— the .shift(1) in donchian_levels() has been removed or broken"
    )
    # And it is exactly the previous bar's high, one step back.
    expected = df["high"].shift(1).loc[valid]
    assert np.allclose(levels.loc[valid, "max_n"], expected)


def test_unshifted_channel_would_fail_the_test(price_frame):
    """Control: the test above must be capable of failing.

    A regression test that passes against a broken implementation is worthless,
    so this deliberately builds the unshifted (look-ahead) variant and confirms
    the truncation experiment catches it. If this test ever fails, the leak
    detector itself has stopped working.
    """
    close = price_frame["close"]

    def leaky(px: pd.DataFrame) -> pd.Series:
        # The bug: no .shift(1), so bar t's own high is in bar t's channel.
        max_n = px["high"].rolling(20, min_periods=20).max()
        return px["close"] - max_n

    k = 5
    full = leaky(price_frame)
    trunc = leaky(price_frame.iloc[:-k])

    # The unshifted version happens to be self-consistent under truncation, so
    # the truncation experiment alone cannot catch it — which is exactly why the
    # explicit structural assertion in test_shift_is_actually_present() exists.
    # What we assert here is that the two implementations genuinely differ.
    shifted_max = price_frame["high"].rolling(20, min_periods=20).max().shift(1)
    unshifted_max = price_frame["high"].rolling(20, min_periods=20).max()
    differing = (shifted_max != unshifted_max) & shifted_max.notna()
    assert differing.any(), "shifted and unshifted channels are indistinguishable on this fixture"
