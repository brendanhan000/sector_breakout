"""Pipeline orchestration: quarantine handling and the plane-disagreement flag."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.pipeline import ABSOLUTE, RELATIVE, compute_all, compute_sector
from backend.engine.state import State

from .synthetic import make_ohlcv, ramp_series, random_walk


def _bars(universe, seed_offset: int = 0, n: int = 500) -> dict[str, pd.DataFrame]:
    out = {}
    for i, symbol in enumerate(universe.all_symbols):
        close = random_walk(seed=seed_offset + i * 17, n=n, sigma=0.011, drift=0.0002)
        volume = np.random.default_rng(90_000 + i).lognormal(14.5, 0.35, n)
        out[symbol] = make_ohlcv(close, volume=volume, wiggle=0.005)
    return out


def test_pipeline_computes_both_planes_for_every_sector(universe, engine_params):
    result = compute_all(_bars(universe), universe, engine_params)
    assert set(result.sectors) == set(universe.sector_symbols)
    for sector in result.sectors.values():
        assert sector.absolute.plane == ABSOLUTE
        assert sector.relative.plane == RELATIVE
        assert not sector.absolute.frame.empty
        assert not sector.relative.frame.empty


def test_quarantined_sector_is_excluded_entirely(universe, engine_params):
    """A symbol whose data failed validation must produce no signals at all —
    and must not silently contribute to the cross-sectional regime either."""
    bars = _bars(universe)
    result = compute_all(bars, universe, engine_params, quarantined=("XLU",))

    assert "XLU" not in result.sectors
    assert result.quarantined == ("XLU",)
    assert len(result.sectors) == len(universe.sectors) - 1


def test_quarantined_equal_weight_twin_disables_only_its_breadth(universe, engine_params):
    """Losing the twin must not lose the sector — only the NARROW badge."""
    bars = _bars(universe)
    result = compute_all(bars, universe, engine_params, quarantined=("RSPU",))

    assert "XLU" in result.sectors
    xlu = result.sectors["XLU"]
    assert xlu.breadth["equal_signal"].isna().all()
    assert not xlu.absolute.frame["narrow"].any()
    # An untouched sector keeps its breadth.
    assert result.sectors["XLK"].breadth["equal_signal"].notna().any()


def test_quarantined_benchmark_refuses_to_compute(universe, engine_params):
    """Without SPY there is no relative plane and no regime. Failing loudly is
    the only honest option."""
    bars = _bars(universe)
    with pytest.raises(ValueError, match="benchmark"):
        compute_all(bars, universe, engine_params, quarantined=("SPY",))


def test_missing_benchmark_bars_refuse_to_compute(universe, engine_params):
    bars = _bars(universe)
    del bars["SPY"]
    with pytest.raises(ValueError, match="benchmark"):
        compute_all(bars, universe, engine_params)


def test_all_sectors_quarantined_refuses_to_compute(universe, engine_params):
    bars = _bars(universe)
    with pytest.raises(ValueError, match="no sectors survived"):
        compute_all(bars, universe, engine_params, quarantined=universe.sector_symbols)


def test_sector_with_no_bars_is_skipped(universe, engine_params):
    bars = _bars(universe)
    bars["XLE"] = bars["XLE"].iloc[0:0]
    result = compute_all(bars, universe, engine_params)
    assert "XLE" not in result.sectors


def test_as_of_is_the_last_commonly_available_bar(universe, engine_params):
    bars = _bars(universe)
    bars["XLU"] = bars["XLU"].iloc[:-3]   # one sector lags
    result = compute_all(bars, universe, engine_params)
    assert result.as_of == bars["XLU"].index[-1]


def test_disagreement_flag_is_true_only_for_opposing_planes(
    universe, engine_params, monkeypatch
):
    """The flag that drives the emphasised rows in the grid."""
    bars = _bars(universe)
    result = compute_all(bars, universe, engine_params)
    sector = result.sectors["XLK"]

    def force(absolute: State, relative: State) -> bool:
        sector.absolute.frame.iloc[-1, sector.absolute.frame.columns.get_loc("state")] = absolute
        sector.relative.frame.iloc[-1, sector.relative.frame.columns.get_loc("state")] = relative
        return sector.disagreement

    assert force(State.CONFIRMED_UP, State.CONFIRMED_DOWN) is True
    assert force(State.CONFIRMED_DOWN, State.PENDING_UP) is True
    assert force(State.CONFIRMED_UP, State.CONFIRMED_UP) is False
    assert force(State.NEUTRAL, State.CONFIRMED_UP) is False
    assert force(State.NEUTRAL, State.NEUTRAL) is False
    # A FAILED upside breakout is a downside signal, so it opposes an upside one.
    assert force(State.CONFIRMED_UP, State.FAILED_UP) is True


def test_relative_plane_uses_real_volume_not_synthetic(universe, engine_params):
    """Volume is plane-independent; the confirmation gate must see the real tape."""
    bars = _bars(universe)
    result = compute_all(bars, universe, engine_params)
    sector = result.sectors["XLK"]

    pd.testing.assert_series_equal(
        sector.absolute.frame["rvol"], sector.relative.frame["rvol"], check_names=False
    )


def test_extension_is_defined_on_both_planes(universe, engine_params):
    """REGRESSION: z_up must exist on the relative plane, which begins with a
    full beta warm-up of NaNs. Without it the leaderboard sorts on nothing."""
    result = compute_all(_bars(universe), universe, engine_params)
    h = engine_params.channel.state_horizon

    for sector in result.sectors.values():
        for plane in (sector.absolute, sector.relative):
            assert plane.frame[f"z_up_{h}"].notna().any(), (
                f"{sector.symbol} {plane.plane}: extension undefined across the series"
            )
            assert not pd.isna(plane.frame[f"z_up_{h}"].iloc[-1])


def test_latest_returns_the_final_row(universe, engine_params):
    result = compute_all(_bars(universe), universe, engine_params)
    sector = result.sectors["XLK"]
    latest = sector.absolute.latest
    assert latest.name == sector.absolute.frame.index[-1]


def test_regime_is_computed_once_over_the_surviving_cross_section(universe, engine_params):
    result = compute_all(_bars(universe), universe, engine_params, quarantined=("XLU",))
    assert set(result.regime.columns) == {
        "dispersion_raw", "dispersion", "dispersion_percentile", "correlation", "low_dispersion",
    }
    assert len(result.regime) == len(result.benchmark_state)
