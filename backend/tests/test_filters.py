"""Relative volume and the breadth / NARROW filter."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.filters import breadth_spread, narrow_flag, relative_volume


def _s(values):
    return pd.Series(
        np.asarray(values, dtype="float64"),
        index=pd.bdate_range("2020-01-01", periods=len(values)),
    )


def test_relative_volume_is_one_on_constant_volume(rvol_params):
    rvol = relative_volume(_s(np.full(50, 1e6)), rvol_params)
    assert np.allclose(rvol.dropna(), 1.0)


def test_relative_volume_baseline_excludes_today(rvol_params):
    """The .shift(1) matters here for the same reason it matters in the channel.

    Without it a huge volume day partially normalises away its own surge,
    because the baseline it is compared against already contains it.
    """
    vol = np.full(60, 1e6)
    vol[50] = 5e6
    rvol = relative_volume(_s(vol), rvol_params)

    # Baseline going into bar 50 is still 1e6, so RVOL is the full 5.0.
    assert rvol.iloc[50] == pytest.approx(5.0, rel=1e-9)

    unshifted = pd.Series(vol) / pd.Series(vol).ewm(span=rvol_params.span, adjust=False).mean()
    assert unshifted.iloc[50] < 5.0, "control: the unshifted version dilutes the surge"


def test_relative_volume_first_bar_is_undefined(rvol_params):
    rvol = relative_volume(_s([1e6, 2e6, 3e6]), rvol_params)
    assert pd.isna(rvol.iloc[0])


def test_relative_volume_handles_zero_baseline(rvol_params):
    """A genuinely untraded stretch makes the ratio undefined, not infinite."""
    vol = np.concatenate([np.zeros(30), np.full(10, 1e6)])
    rvol = relative_volume(_s(vol), rvol_params)
    assert np.isfinite(rvol.dropna()).all()
    assert pd.isna(rvol.iloc[5])


def test_breadth_spread_is_cap_minus_equal():
    cap = _s([0.9, 0.8, 0.5])
    equal = _s([0.2, 0.8, 0.6])
    spread = breadth_spread(cap, equal)
    assert spread.tolist() == pytest.approx([0.7, 0.0, -0.1])


def test_narrow_fires_when_cap_breaks_out_on_a_non_participating_base(breadth_params):
    """Four mega-caps moving is not a sector moving.

    This is the highest value-per-line-of-code measurement in the system, and it
    gets its own badge in the UI rather than a tooltip.
    """
    cap = _s([1.0, 1.0, 1.0])
    equal = _s([0.10, 0.10, 0.10])         # twin is not participating
    breaking = pd.Series([True, True, False], index=cap.index)

    flag = narrow_flag(cap, equal, breaking, breadth_params)
    assert flag.tolist() == [True, True, False]


def test_narrow_does_not_fire_when_the_twin_participates(breadth_params):
    cap = _s([1.0, 1.0])
    equal = _s([0.85, 0.90])
    breaking = pd.Series([True, True], index=cap.index)
    assert not narrow_flag(cap, equal, breaking, breadth_params).any()


def test_narrow_does_not_fire_without_a_breakout(breadth_params):
    """A quiet sector with a weak equal-weight twin is just quiet."""
    cap = _s([0.0, 0.1])
    equal = _s([0.0, 0.05])
    breaking = pd.Series([False, False], index=cap.index)
    assert not narrow_flag(cap, equal, breaking, breadth_params).any()


def test_narrow_is_false_when_the_twin_has_no_data(breadth_params):
    """No equal-weight history means the claim cannot be made either way.

    Asserting NARROW on missing data would put a scary badge on every sector
    during the warm-up window.
    """
    cap = _s([1.0, 1.0])
    equal = pd.Series([np.nan, np.nan], index=cap.index)
    breaking = pd.Series([True, True], index=cap.index)

    flag = narrow_flag(cap, equal, breaking, breadth_params)
    assert flag.dtype == bool
    assert not flag.any()


def test_narrow_threshold_boundary_is_exclusive(breadth_params):
    cap = _s([1.0, 1.0])
    equal = _s([breadth_params.narrow_threshold, breadth_params.narrow_threshold - 1e-9])
    breaking = pd.Series([True, True], index=cap.index)
    flag = narrow_flag(cap, equal, breaking, breadth_params)
    assert flag.tolist() == [False, True]
