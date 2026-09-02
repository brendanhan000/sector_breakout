"""Channel position, clipping, degenerate windows, extension, and term structure."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.channel import channel_frame, channel_position, donchian_levels, extension
from backend.engine.params import ChannelParams

from .synthetic import make_ohlcv, ramp_series, random_walk


def test_donchian_levels_use_only_prior_bars():
    """max_N at t is the max of the N bars ending at t-1, inclusive."""
    close = np.array([10, 12, 11, 15, 13, 9, 14, 16, 12, 11, 18, 17], dtype="float64")
    df = make_ohlcv(close, wiggle=0.0)
    levels = donchian_levels(df, 3)

    # t=3: prior 3 bars are t=0,1,2 -> highs 10,12,11 -> max 12, lows -> min 10
    assert levels["max_n"].iloc[3] == 12.0
    assert levels["min_n"].iloc[3] == 10.0
    # t=4: prior 3 bars are t=1,2,3 -> 12,11,15 -> max 15, min 11
    assert levels["max_n"].iloc[4] == 15.0
    assert levels["min_n"].iloc[4] == 11.0
    # First N bars have no complete prior window.
    assert levels["max_n"].iloc[:3].isna().all()


def test_mid_and_width_derive_from_the_shifted_levels():
    close = np.array([10, 20, 30, 25, 15], dtype="float64")
    df = make_ohlcv(close, wiggle=0.0)
    levels = donchian_levels(df, 3)
    # t=3: prior window 10,20,30 -> max 30 min 10 -> mid 20 width 20
    assert levels["mid"].iloc[3] == 20.0
    assert levels["width"].iloc[3] == 20.0


def test_channel_position_is_plus_one_at_the_upper_edge(channel_params):
    """A close exactly at the prior N-bar high scores +1 before smoothing."""
    close = np.array([10, 20, 30, 30.0, 30.0], dtype="float64")
    df = make_ohlcv(close, wiggle=0.0)
    pos = channel_position(df, 3, channel_params)
    # t=3: mid 20, half-width 10, close 30 -> (30-20)/10 = +1
    assert pos["c"].iloc[3] == pytest.approx(1.0)


def test_channel_position_is_minus_one_at_the_lower_edge(channel_params):
    close = np.array([10, 20, 30, 10.0], dtype="float64")
    df = make_ohlcv(close, wiggle=0.0)
    pos = channel_position(df, 3, channel_params)
    assert pos["c"].iloc[3] == pytest.approx(-1.0)


def test_channel_position_is_zero_at_the_midpoint(channel_params):
    close = np.array([10, 20, 30, 20.0], dtype="float64")
    df = make_ohlcv(close, wiggle=0.0)
    pos = channel_position(df, 3, channel_params)
    assert pos["c"].iloc[3] == pytest.approx(0.0)


def test_position_is_clipped_at_the_configured_bound(channel_params):
    """A violent gap must saturate, not run away to an arbitrary magnitude."""
    close = np.array([100, 101, 102, 1000.0], dtype="float64")
    df = make_ohlcv(close, wiggle=0.0)
    pos = channel_position(df, 3, channel_params)
    assert pos["c"].iloc[3] == pytest.approx(channel_params.clip)

    close_dn = np.array([100, 101, 102, 1.0], dtype="float64")
    pos_dn = channel_position(make_ohlcv(close_dn, wiggle=0.0), 3, channel_params)
    assert pos_dn["c"].iloc[3] == pytest.approx(-channel_params.clip)


def test_degenerate_flat_window_returns_zero_not_nan_or_inf(channel_params):
    """width == 0 must yield 0.0.

    NaN would poison the EWM for every subsequent bar (the signal would never
    recover), and inf would blow up every downstream comparison in the state
    machine. 0.0 is also the correct reading: the close sits, trivially, at the
    middle of a zero-width range.
    """
    close = np.full(30, 50.0)
    df = make_ohlcv(close, wiggle=0.0)
    pos = channel_position(df, 5, channel_params)

    settled = pos.iloc[5:]
    assert not settled["c"].isna().any()
    assert np.isfinite(settled["c"]).all()
    assert (settled["c"] == 0.0).all()
    assert np.isfinite(settled["signal"]).all()


def test_flat_window_does_not_poison_later_bars(channel_params):
    """After a dead-flat stretch the signal must respond again immediately."""
    close = np.concatenate([np.full(30, 50.0), np.linspace(50.0, 80.0, 30)])
    df = make_ohlcv(close, wiggle=0.0)
    pos = channel_position(df, 10, channel_params)
    assert np.isfinite(pos["signal"].iloc[-1])
    assert pos["signal"].iloc[-1] > 0.5


def test_ewm_span_follows_the_configured_formula(channel_params):
    assert channel_params.ewm_span(10) == 2      # max(2, 10 // 4 = 2)
    assert channel_params.ewm_span(20) == 5      # max(2, 20 // 4 = 5)
    assert channel_params.ewm_span(55) == 13     # max(2, 55 // 4 = 13)
    # The floor binds for very short horizons.
    assert channel_params.ewm_span(4) == 2
    assert channel_params.ewm_span(1) == 2


def test_smoothed_signal_is_less_volatile_than_raw_position(channel_params):
    df = make_ohlcv(random_walk(seed=17, n=600), wiggle=0.004)
    pos = channel_position(df, 20, channel_params).dropna()
    assert pos["signal"].diff().std() < pos["c"].diff().std()


def test_extension_is_positive_only_beyond_the_upper_edge(channel_params):
    df = make_ohlcv(ramp_series(120, slope=1.0), wiggle=0.0)
    ext = extension(df, 20).dropna()
    # On a strict ramp every close is a new high, so z_up > 0 throughout.
    assert (ext["z_up"] > 0).all()
    assert (ext["z_dn"] < 0).all()


def test_extension_is_atr_normalised_and_therefore_comparable(channel_params):
    """Two series with identical shape but different price levels and
    volatilities must produce the same extension. This is what makes z_up a
    valid cross-sectional sort key across XLU and XLK."""
    base = ramp_series(150, start_level=100.0, slope=0.5)
    quiet = make_ohlcv(base, wiggle=0.0)

    # Same shape, 10x the price level and 10x the absolute moves.
    loud = make_ohlcv(base * 10.0, wiggle=0.0)

    z_quiet = extension(quiet, 20)["z_up"].dropna()
    z_loud = extension(loud, 20)["z_up"].dropna()

    assert np.allclose(z_quiet.to_numpy(), z_loud.to_numpy(), rtol=1e-9)


def test_channel_frame_emits_every_horizon_and_averages_none(channel_params):
    """The term structure must be stored side by side.

    Averaging the three horizons into a composite collapses four distinct
    readings (established trend / early reversal / pullback-in-uptrend /
    extended) into one number and destroys the exact information the panel
    exists to show. This test asserts the columns are present and distinct.
    """
    df = make_ohlcv(random_walk(seed=23, n=400), wiggle=0.005)
    frame = channel_frame(df, channel_params)

    for h in channel_params.horizons:
        for prefix in ("c", "signal", "max", "min", "z_up", "z_dn"):
            assert f"{prefix}_{h}" in frame.columns

    assert "signal" not in frame.columns, "a composite column has appeared"
    assert "composite" not in frame.columns

    tail = frame.dropna()
    s10, s20, s55 = tail["signal_10"], tail["signal_20"], tail["signal_55"]
    assert not np.allclose(s10, s20)
    assert not np.allclose(s20, s55)
    # And no column is the mean of the others.
    mean_of_three = (s10 + s20 + s55) / 3.0
    for col in (s10, s20, s55):
        assert not np.allclose(col, mean_of_three)


def test_pullback_inside_uptrend_shows_the_expected_term_structure(channel_params):
    """Short horizon negative, long horizon positive — the good entry.

    A long steady advance followed by a brief sharp dip should leave the 55-day
    channel firmly positive while the 10-day has already turned negative.
    """
    up = ramp_series(200, start_level=100.0, slope=0.6)
    dip = up[-1] - np.linspace(0.0, 14.0, 12)
    close = np.concatenate([up, dip])
    df = make_ohlcv(close, wiggle=0.0)

    frame = channel_frame(df, channel_params)
    last = frame.iloc[-1]

    assert last["signal_10"] < 0, "short horizon should have rolled over"
    assert last["signal_55"] > 0, "long horizon should still be in the uptrend"


def test_rejects_nonpositive_horizon():
    df = make_ohlcv(random_walk(seed=1, n=50))
    with pytest.raises(ValueError):
        donchian_levels(df, 0)


def test_extension_is_defined_on_a_series_with_a_warmup_of_nans(channel_params):
    """REGRESSION: z_up/z_dn must exist on the relative plane.

    The residual series is NaN through the beta warm-up. If ATR cannot seed
    after that, extension is NaN everywhere and the leaderboard has nothing to
    sort by — silently.
    """
    warmup = 60
    n = 300
    close = np.concatenate([np.full(warmup, np.nan), 100 + np.arange(n - warmup) * 0.4])
    df = make_ohlcv(np.nan_to_num(close, nan=1.0), wiggle=0.005)
    df.loc[df.index[:warmup], ["open", "high", "low", "close"]] = np.nan

    ext = extension(df, 20)
    assert ext["z_up"].notna().sum() > 0, "extension is undefined across the whole series"
    assert ext["z_dn"].notna().sum() > 0
