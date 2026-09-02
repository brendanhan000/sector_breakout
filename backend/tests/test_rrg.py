"""RRG coordinates and quadrant placement."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.rrg import (
    Quadrant,
    classify_quadrant,
    normalise,
    relative_strength,
    rrg_coordinates,
    rrg_tail,
)

from .synthetic import ramp_series, random_walk


def test_relative_strength_is_scaled_ratio():
    idx = pd.bdate_range("2020-01-01", periods=3)
    sector = pd.Series([110.0, 120.0, 90.0], index=idx)
    bench = pd.Series([100.0, 100.0, 100.0], index=idx)
    rs = relative_strength(sector, bench)
    assert rs.tolist() == pytest.approx([110.0, 120.0, 90.0])


def test_quadrant_classification_at_the_corners(rrg_params):
    assert classify_quadrant(105.0, 105.0, rrg_params) == Quadrant.LEADING
    assert classify_quadrant(105.0, 95.0, rrg_params) == Quadrant.WEAKENING
    assert classify_quadrant(95.0, 95.0, rrg_params) == Quadrant.LAGGING
    assert classify_quadrant(95.0, 105.0, rrg_params) == Quadrant.IMPROVING
    assert classify_quadrant(np.nan, 105.0, rrg_params) is None
    assert classify_quadrant(105.0, np.nan, rrg_params) is None


def test_quadrant_boundary_is_the_origin_not_the_offset(rrg_params):
    """Exactly on the origin counts as the lower/left side (x > 100 is strict)."""
    assert classify_quadrant(100.0, 100.0, rrg_params) == Quadrant.LAGGING
    assert classify_quadrant(100.0, 100.001, rrg_params) == Quadrant.IMPROVING


def test_normalise_is_a_zscore_plus_offset(rrg_params):
    n = 200
    g = np.random.default_rng(12)
    s = pd.Series(g.normal(50, 5, n), index=pd.bdate_range("2020-01-01", periods=n))
    out = normalise(s, rrg_params)

    w = rrg_params.window
    window = s.iloc[-w:]
    expected = rrg_params.offset + (s.iloc[-1] - window.mean()) / window.std(ddof=1)
    assert out.iloc[-1] == pytest.approx(expected, rel=1e-12)


def test_normalise_requires_the_full_window(rrg_params):
    s = pd.Series(np.arange(100.0), index=pd.bdate_range("2020-01-01", periods=100))
    out = normalise(s, rrg_params)
    assert out.iloc[: rrg_params.window - 1].isna().all()
    assert not pd.isna(out.iloc[rrg_params.window - 1])


def test_flat_window_normalises_to_the_offset_not_infinity(rrg_params):
    s = pd.Series(np.full(120, 42.0), index=pd.bdate_range("2020-01-01", periods=120))
    out = normalise(s, rrg_params).dropna()
    assert np.isfinite(out).all()
    assert np.allclose(out, rrg_params.offset)


def test_sector_identical_to_benchmark_sits_at_the_offset(rrg_params):
    """A sector that IS the benchmark has constant RS.

    Note the consequence of the specified constants: the formula's neutral value
    is the offset (101), while the quadrant origin is 100. So a sector sitting
    exactly at its own trailing mean RS reads as marginally Leading rather than
    dead centre. That is what the specified normalisation produces; both numbers
    are in config.yaml (rrg.offset / rrg.origin) if you want them to coincide.
    """
    n = 150
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = pd.Series(random_walk(seed=5, n=n), index=idx)
    coords = rrg_coordinates(close, close, rrg_params).dropna()

    assert np.allclose(coords["rs_ratio"], rrg_params.offset)
    assert np.allclose(coords["rs_momentum"], rrg_params.offset)


def test_persistent_outperformance_lands_in_leading(rrg_params):
    """A sector steadily beating a flat benchmark must be Leading."""
    n = 300
    idx = pd.bdate_range("2020-01-01", periods=n)
    sector = pd.Series(ramp_series(n, start_level=100.0, slope=0.4), index=idx)
    bench = pd.Series(np.full(n, 100.0), index=idx)

    coords = rrg_coordinates(sector, bench, rrg_params).dropna()
    assert coords["rs_ratio"].iloc[-1] > rrg_params.origin
    assert coords["quadrant"].iloc[-1] == Quadrant.LEADING


def test_rotation_traces_the_canonical_counterclockwise_loop(rrg_params):
    """A sector that outperforms and then rolls over must rotate
    Leading -> Weakening -> Lagging -> Improving, in that order.

    This is the reading the whole panel exists to support: position tells you
    where a sector is, the tail tells you which way it is travelling, and the
    direction is the trade. If the rotation ran clockwise, the momentum axis
    would be inverted and every tail on the dashboard would point the wrong way.
    """
    n = 400
    peak = 200
    idx = pd.bdate_range("2020-01-01", periods=n)
    t = np.arange(n)
    noise = np.random.default_rng(77).normal(0, 0.2, n)
    path = np.where(t < peak, 100 + 0.25 * t, 100 + 0.25 * peak - 0.25 * (t - peak))

    sector = pd.Series(path + noise, index=idx)
    bench = pd.Series(np.full(n, 100.0), index=idx)

    coords = rrg_coordinates(sector, bench, rrg_params).dropna()
    sequence = list(pd.unique(coords["quadrant"]))

    assert sequence == [
        Quadrant.LEADING,
        Quadrant.WEAKENING,
        Quadrant.LAGGING,
        Quadrant.IMPROVING,
    ], f"rotation did not run counterclockwise: {sequence}"


def test_sustained_outperformance_then_collapse_reaches_lagging(rrg_params):
    n = 400
    peak = 200
    idx = pd.bdate_range("2020-01-01", periods=n)
    t = np.arange(n)
    noise = np.random.default_rng(77).normal(0, 0.2, n)
    path = np.where(t < peak, 100 + 0.25 * t, 100 + 0.25 * peak - 0.25 * (t - peak))

    coords = rrg_coordinates(
        pd.Series(path + noise, index=idx), pd.Series(np.full(n, 100.0), index=idx), rrg_params
    ).dropna()

    assert coords["rs_ratio"].iloc[-1] < rrg_params.origin
    assert coords["rs_momentum"].iloc[-1] < rrg_params.origin
    assert coords["quadrant"].iloc[-1] == Quadrant.LAGGING


def test_offset_sits_one_sigma_above_the_quadrant_origin(rrg_params):
    """DOCUMENTED CONSEQUENCE of the specified constants, not an accident.

    The normalisation is ``offset + z`` with offset = 101, while the quadrant
    origin is 100. The two differ by exactly one standard-deviation unit, so a
    sector sitting precisely at its own trailing mean scores 101 and reads as
    marginally Leading rather than dead centre. In practice a sector must be
    more than 1 sigma below its trailing mean before it registers as Lagging on
    an axis.

    This is what the specified formula produces. Both numbers live in
    config.yaml (``rrg.offset`` and ``rrg.origin``); setting offset to 100.0
    makes neutral and origin coincide. The test pins the current behaviour so a
    change to either constant is a deliberate, visible decision.
    """
    assert rrg_params.offset - rrg_params.origin == pytest.approx(1.0)

    n = 300
    idx = pd.bdate_range("2020-01-01", periods=n)
    # A series with no trend in relative strength: z hovers around 0.
    g = np.random.default_rng(101)
    sector = pd.Series(100 + g.normal(0, 1.0, n), index=idx)
    bench = pd.Series(np.full(n, 100.0), index=idx)

    coords = rrg_coordinates(sector, bench, rrg_params).dropna()
    # Centred on the offset, not on the origin.
    assert coords["rs_ratio"].mean() == pytest.approx(rrg_params.offset, abs=0.3)
    assert coords["rs_ratio"].mean() > rrg_params.origin


def test_tail_returns_the_configured_number_of_defined_points(rrg_params):
    n = 300
    idx = pd.bdate_range("2020-01-01", periods=n)
    sector = pd.Series(random_walk(seed=31, n=n), index=idx)
    bench = pd.Series(random_walk(seed=32, n=n), index=idx)

    coords = rrg_coordinates(sector, bench, rrg_params)
    tail = rrg_tail(coords, rrg_params)

    assert len(tail) == rrg_params.tail_length
    assert tail.index.is_monotonic_increasing, "tails must be oldest-first to draw direction"
    assert not tail[["rs_ratio", "rs_momentum"]].isna().any().any()
    assert tail.index[-1] == coords.dropna(subset=["rs_ratio"]).index[-1]


def test_rrg_needs_two_full_windows_of_warmup(rrg_params):
    """RS_Momentum normalises RS_Ratio, so the warm-up is 2*window - 1 bars.

    With the shipped 60-bar window that is 119 bars before the first plotted
    point exists. Anything shorter yields an empty RRG panel, which is why the
    API refuses to serve a partially warmed-up day rather than drawing a chart
    from two sectors.
    """
    w = rrg_params.window
    idx = pd.bdate_range("2020-01-01", periods=2 * w)
    sector = pd.Series(random_walk(seed=41, n=2 * w), index=idx)
    bench = pd.Series(random_walk(seed=42, n=2 * w), index=idx)
    coords = rrg_coordinates(sector, bench, rrg_params)

    assert coords["rs_momentum"].iloc[: 2 * w - 2].isna().all()
    assert not pd.isna(coords["rs_momentum"].iloc[2 * w - 2])


def test_tail_is_short_when_history_barely_covers_the_warmup(rrg_params):
    n = 2 * rrg_params.window + 3   # only a few defined momentum points
    idx = pd.bdate_range("2020-01-01", periods=n)
    sector = pd.Series(random_walk(seed=41, n=n), index=idx)
    bench = pd.Series(random_walk(seed=42, n=n), index=idx)
    tail = rrg_tail(rrg_coordinates(sector, bench, rrg_params), rrg_params)
    assert 0 < len(tail) < rrg_params.tail_length
