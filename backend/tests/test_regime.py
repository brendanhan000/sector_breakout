"""Regime: dispersion, its trailing percentile, and mean pairwise correlation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.params import CorrelationParams, DispersionParams, RegimeParams
from backend.engine.regime import (
    compute_regime,
    cross_sectional_dispersion,
    dispersion_percentile,
    mean_pairwise_correlation,
)


def _returns(n: int, k: int, seed: int, scale: float = 0.01) -> pd.DataFrame:
    g = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-02", periods=n)
    return pd.DataFrame(
        {f"S{i}": g.normal(0, scale, n) for i in range(k)}, index=idx
    )


@pytest.fixture
def small_regime_params() -> RegimeParams:
    return RegimeParams(
        dispersion=DispersionParams(smoothing_window=5, percentile_window=60, low_threshold=25.0),
        correlation=CorrelationParams(window=20, sparkline_bars=50),
    )


def test_dispersion_is_the_cross_sectional_stdev(small_regime_params):
    idx = pd.bdate_range("2020-01-01", periods=3)
    returns = pd.DataFrame(
        {"A": [0.01, 0.02, -0.01], "B": [0.03, 0.02, 0.01], "C": [0.02, 0.02, 0.00]}, index=idx
    )
    disp = cross_sectional_dispersion(returns, small_regime_params)
    assert disp["dispersion_raw"].iloc[0] == pytest.approx(np.std([0.01, 0.03, 0.02], ddof=1))
    # A day where every sector moved identically has zero dispersion.
    assert disp["dispersion_raw"].iloc[1] == pytest.approx(0.0)


def test_dispersion_requires_at_least_two_sectors(small_regime_params):
    idx = pd.bdate_range("2020-01-01", periods=3)
    returns = pd.DataFrame({"A": [0.01, np.nan, 0.02], "B": [np.nan, np.nan, 0.01]}, index=idx)
    disp = cross_sectional_dispersion(returns, small_regime_params)
    assert pd.isna(disp["dispersion_raw"].iloc[0])   # one sector
    assert pd.isna(disp["dispersion_raw"].iloc[1])   # none
    assert not pd.isna(disp["dispersion_raw"].iloc[2])


def test_percentile_requires_the_full_window(small_regime_params):
    """A '3-year percentile' computed from 100 bars is a different statistic.

    Requiring the full window means the low-dispersion gate fires on a real
    3-year comparison or not at all.
    """
    s = pd.Series(np.arange(100.0), index=pd.bdate_range("2020-01-01", periods=100))
    pct = dispersion_percentile(s, small_regime_params)
    assert pct.iloc[:59].isna().all()
    assert not pd.isna(pct.iloc[59])


def test_percentile_is_100_at_a_new_high_and_low_at_a_new_low(small_regime_params):
    rising = pd.Series(np.arange(200.0), index=pd.bdate_range("2020-01-01", periods=200))
    pct = dispersion_percentile(rising, small_regime_params).dropna()
    assert np.allclose(pct, 100.0), "a monotonically rising series is always at its own max"

    falling = pd.Series(np.arange(200.0)[::-1], index=pd.bdate_range("2020-01-01", periods=200))
    pct_f = dispersion_percentile(falling, small_regime_params).dropna()
    # The newest value is the smallest of its window: rank 1 of 60.
    assert np.allclose(pct_f, 100.0 / 60.0)


def test_percentile_is_backward_looking_only(small_regime_params):
    """A spike in the future must not change today's percentile."""
    g = np.random.default_rng(4)
    base = pd.Series(g.uniform(0, 1, 200), index=pd.bdate_range("2020-01-01", periods=200))
    spiked = base.copy()
    spiked.iloc[150:] = 99.0

    a = dispersion_percentile(base, small_regime_params).iloc[:150]
    b = dispersion_percentile(spiked, small_regime_params).iloc[:150]
    pd.testing.assert_series_equal(a, b)


def test_mean_correlation_of_identical_series_is_one(small_regime_params):
    n = 100
    g = np.random.default_rng(9)
    r = g.normal(0, 0.01, n)
    idx = pd.bdate_range("2020-01-01", periods=n)
    returns = pd.DataFrame({f"S{i}": r for i in range(4)}, index=idx)
    corr = mean_pairwise_correlation(returns, small_regime_params).dropna()
    assert np.allclose(corr, 1.0)


def test_mean_correlation_of_independent_series_is_near_zero(small_regime_params):
    returns = _returns(2000, 8, seed=21)
    params = RegimeParams(
        dispersion=small_regime_params.dispersion,
        correlation=CorrelationParams(window=250, sparkline_bars=50),
    )
    corr = mean_pairwise_correlation(returns, params).dropna()
    assert abs(corr.mean()) < 0.05


def test_mean_correlation_matches_the_offdiagonal_mean_directly(small_regime_params):
    """Cross-check the pairwise shortcut against an explicit matrix computation."""
    returns = _returns(120, 5, seed=3)
    corr = mean_pairwise_correlation(returns, small_regime_params)

    w = small_regime_params.correlation.window
    window = returns.iloc[-w:]
    matrix = window.corr().to_numpy()
    off_diag = matrix[~np.eye(matrix.shape[0], dtype=bool)]
    assert corr.iloc[-1] == pytest.approx(off_diag.mean(), abs=1e-10)


def test_single_series_has_no_correlation(small_regime_params):
    returns = _returns(100, 1, seed=1)
    corr = mean_pairwise_correlation(returns, small_regime_params)
    assert corr.isna().all()


def test_low_dispersion_flag_fires_in_the_bottom_quartile(small_regime_params):
    """The gate that grays out the entire relative plane.

    In a low-dispersion regime every Plane B signal is noise, and a dashboard
    that keeps rendering confident rotation calls is actively harmful — so this
    flag has to be right.
    """
    n = 400
    idx = pd.bdate_range("2018-01-01", periods=n)
    g = np.random.default_rng(6)

    # High dispersion for most of the sample, then a hard collapse near the end.
    # The collapse must be RECENT relative to the percentile window: if the
    # whole trailing window were already collapsed, today's value would rank in
    # the middle of its own quiet regime and the gate correctly would not fire.
    collapse_at = n - 20
    wide = g.normal(0, 0.02, (n, 6))
    narrow = g.normal(0, 0.0005, (n, 6))
    data = np.vstack([wide[:collapse_at], narrow[collapse_at:]])
    returns = pd.DataFrame(data, index=idx, columns=[f"S{i}" for i in range(6)])

    regime = compute_regime(returns, small_regime_params)
    tail = regime.dropna(subset=["dispersion_percentile"])

    assert not tail["low_dispersion"].iloc[0], "wide regime should not be flagged"

    # The flag must hold across the collapsed stretch. Asserting on a single
    # final bar would be brittle: the percentile is a rank, so one bar can sit
    # just over the threshold while the regime is plainly quiet.
    collapsed = regime["low_dispersion"].iloc[collapse_at:]
    assert collapsed.mean() > 0.8, "collapsed dispersion was not flagged"
    assert regime["dispersion_percentile"].iloc[collapse_at:].median() < (
        small_regime_params.dispersion.low_threshold
    )


def test_low_dispersion_is_false_not_null_when_percentile_is_unknown(small_regime_params):
    """Missing history must not gray out the panel by accident.

    Insufficient history is a data-quality condition the API reports separately;
    it is not evidence of low dispersion.
    """
    returns = _returns(40, 4, seed=2)   # shorter than the percentile window
    regime = compute_regime(returns, small_regime_params)
    assert regime["dispersion_percentile"].isna().all()
    assert (~regime["low_dispersion"]).all()
    assert regime["low_dispersion"].dtype == bool


def test_compute_regime_emits_every_expected_column(small_regime_params):
    returns = _returns(300, 6, seed=8)
    regime = compute_regime(returns, small_regime_params)
    assert set(regime.columns) == {
        "dispersion_raw", "dispersion", "dispersion_percentile", "correlation", "low_dispersion",
    }
