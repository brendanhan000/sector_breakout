"""Rolling beta recovery and residual construction (Plane B).

The contract: feed a series that is BY CONSTRUCTION 1.5x the market plus
independent noise, and the engine must recover beta ~= 1.5 and produce a
residual that carries none of the market's return.

If beta estimation is wrong, the whole relative plane is wrong in a way that is
invisible on the dashboard — high-beta sectors would look permanently strong in
up markets and permanently weak in down markets, and the "rotation" the panel
claims to show would just be a beta ranking.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.beta import (
    log_returns,
    residual_ohlc,
    residual_returns,
    residual_series,
    rolling_beta,
)

from .synthetic import make_ohlcv

TRUE_BETA = 1.5


@pytest.fixture
def market_and_asset() -> tuple[pd.Series, pd.Series, pd.Series]:
    """r_i = 1.5 * r_m + noise, with noise independent of r_m."""
    g = np.random.default_rng(20240607)
    n = 2000
    r_m = g.normal(0.0003, 0.010, n)
    noise = g.normal(0.0, 0.004, n)
    r_i = TRUE_BETA * r_m + noise

    idx = pd.bdate_range("2015-01-02", periods=n)
    market_close = pd.Series(100 * np.exp(np.cumsum(r_m)), index=idx)
    asset_close = pd.Series(100 * np.exp(np.cumsum(r_i)), index=idx)
    return market_close, asset_close, pd.Series(r_m, index=idx)


def test_rolling_beta_recovers_the_true_slope(market_and_asset, beta_params):
    market_close, asset_close, _ = market_and_asset
    r_i = log_returns(asset_close)
    r_m = log_returns(market_close)

    beta = rolling_beta(r_i, r_m, beta_params).dropna()

    assert len(beta) > 1500
    # Each 60-observation estimate has standard error
    # sigma_noise / (sigma_m * sqrt(60)) ~= 0.004 / (0.010 * 7.75) ~= 0.052,
    # so the SAMPLE MEAN of ~1900 overlapping estimates must be very close.
    assert beta.mean() == pytest.approx(TRUE_BETA, abs=0.02)
    assert beta.median() == pytest.approx(TRUE_BETA, abs=0.03)
    # And no individual window should be wildly off: 5 standard errors.
    assert beta.std() < 0.10
    assert (beta - TRUE_BETA).abs().max() < 0.35


def test_residual_has_no_significant_correlation_with_the_market(
    market_and_asset, beta_params
):
    """The whole point of the residual: the market component is gone.

    The raw asset returns are ~0.97 correlated with the market. After removing
    beta * r_m the correlation must collapse to approximately zero — that
    reduction IS the relative plane.
    """
    market_close, asset_close, _ = market_and_asset
    r_i = log_returns(asset_close)
    r_m = log_returns(market_close)
    beta = rolling_beta(r_i, r_m, beta_params)
    eps = residual_returns(r_i, r_m, beta)

    both = pd.concat([eps, r_m], axis=1).dropna()
    resid_corr = both.iloc[:, 0].corr(both.iloc[:, 1])
    raw_corr = pd.concat([r_i, r_m], axis=1).dropna().corr().iloc[0, 1]

    assert raw_corr > 0.9, "fixture is not actually market-driven"

    # Two-sided significance at ~3 sigma for n observations: 3 / sqrt(n).
    n = len(both)
    threshold = 3.0 / np.sqrt(n)
    assert abs(resid_corr) < threshold, (
        f"residual still correlates with the market: r={resid_corr:.4f} "
        f"exceeds the 3-sigma bound {threshold:.4f}"
    )
    assert abs(resid_corr) < raw_corr / 20.0


def test_raw_ratio_would_not_pass_the_orthogonality_test(market_and_asset, beta_params):
    """Control: the naive XLK/SPY ratio keeps the beta drift the residual removes.

    This is why the spec makes the residual construction mandatory rather than
    optional. The returns of a raw price ratio still correlate strongly with the
    market whenever beta != 1.
    """
    market_close, asset_close, _ = market_and_asset
    ratio_returns = log_returns(asset_close / market_close)
    r_m = log_returns(market_close)

    both = pd.concat([ratio_returns, r_m], axis=1).dropna()
    ratio_corr = both.iloc[:, 0].corr(both.iloc[:, 1])

    # beta = 1.5 leaves 0.5 * r_m inside the ratio.
    assert abs(ratio_corr) > 0.5, (
        "the naive ratio should retain substantial market exposure at beta=1.5"
    )


def test_residual_series_is_price_like_and_starts_at_base(market_and_asset, beta_params):
    market_close, asset_close, _ = market_and_asset
    r_i = log_returns(asset_close)
    r_m = log_returns(market_close)
    beta = rolling_beta(r_i, r_m, beta_params)
    eps = residual_returns(r_i, r_m, beta)
    resid = residual_series(eps, beta_params)

    valid = resid.dropna()
    assert len(valid) > 0
    assert (valid > 0).all(), "a price-like series must stay strictly positive"

    # Leading bars (before beta exists) stay NaN rather than being back-filled
    # with the base level, which would fake a flat history the channel would
    # read as a real trading range.
    first_valid = resid.first_valid_index()
    assert resid.loc[:first_valid].iloc[:-1].isna().all()

    # The first defined value is base * exp(eps_first).
    first_eps = eps.loc[first_valid]
    assert valid.iloc[0] == pytest.approx(
        beta_params.residual_base * np.exp(first_eps), rel=1e-12
    )


def test_zero_beta_residual_equals_the_asset_itself(beta_params):
    """Sanity: with an uncorrelated market, beta ~= 0 and the residual tracks
    the asset's own returns rather than a market-adjusted version."""
    g = np.random.default_rng(5)
    n = 800
    r_m = g.normal(0, 0.01, n)
    r_i = g.normal(0, 0.01, n)  # independent
    idx = pd.bdate_range("2016-01-01", periods=n)

    market = pd.Series(100 * np.exp(np.cumsum(r_m)), index=idx)
    asset = pd.Series(100 * np.exp(np.cumsum(r_i)), index=idx)

    beta = rolling_beta(log_returns(asset), log_returns(market), beta_params)
    assert abs(beta.dropna().mean()) < 0.10


def test_residual_ohlc_close_equals_residual_exactly(market_and_asset, beta_params):
    """The synthetic close must BE the residual series, so channel logic run on
    the relative plane is measuring exactly the quantity we defined."""
    market_close, asset_close, _ = market_and_asset
    idx = asset_close.index
    ohlcv = make_ohlcv(asset_close.to_numpy(), wiggle=0.005)
    ohlcv.index = idx

    r_i = log_returns(ohlcv["close"])
    r_m = log_returns(market_close)
    beta = rolling_beta(r_i, r_m, beta_params)
    resid = residual_series(residual_returns(r_i, r_m, beta), beta_params)

    rel = residual_ohlc(ohlcv, resid)

    both = pd.concat([rel["close"], resid], axis=1).dropna()
    assert np.allclose(both.iloc[:, 0], both.iloc[:, 1], rtol=1e-12)


def test_residual_ohlc_preserves_bar_geometry(market_and_asset, beta_params):
    market_close, asset_close, _ = market_and_asset
    ohlcv = make_ohlcv(asset_close.to_numpy(), wiggle=0.01)
    ohlcv.index = asset_close.index

    r_i = log_returns(ohlcv["close"])
    r_m = log_returns(market_close)
    beta = rolling_beta(r_i, r_m, beta_params)
    resid = residual_series(residual_returns(r_i, r_m, beta), beta_params)
    rel = residual_ohlc(ohlcv, resid).dropna()

    assert (rel["high"] >= rel["low"]).all()
    assert (rel["high"] >= rel["close"]).all()
    assert (rel["low"] <= rel["close"]).all()
    # Volume is a real, plane-independent quantity and must pass through unscaled.
    assert np.allclose(rel["volume"], ohlcv["volume"].reindex(rel.index))


def test_zero_variance_market_gives_undefined_beta_not_zero(beta_params):
    """A motionless market says nothing about beta. NaN is honest; 0 is a lie
    that would claim the sector is market-neutral."""
    n = 200
    idx = pd.bdate_range("2018-01-01", periods=n)
    flat_market = pd.Series(np.full(n, 100.0), index=idx)
    asset = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, n))), index=idx)

    beta = rolling_beta(log_returns(asset), log_returns(flat_market), beta_params)
    assert beta.isna().all()
