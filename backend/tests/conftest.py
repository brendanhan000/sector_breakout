"""Shared fixtures.

Engine tests build their own parameter objects rather than reading config.yaml,
so that a change to production thresholds cannot silently invalidate an
analytically-derived assertion. The one exception is
``test_config.py::test_config_yaml_matches_engine_params``, which exists
specifically to check that the shipped config still binds.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.params import (
    ATRParams,
    BetaParams,
    BreadthParams,
    ChannelParams,
    CorrelationParams,
    DispersionParams,
    EngineParams,
    RegimeParams,
    RRGParams,
    RVolParams,
    SectorSpec,
    StateParams,
    UniverseSpec,
)

from .synthetic import make_ohlcv, random_walk, step_series, ramp_series, sine_series  # noqa: F401


@pytest.fixture
def channel_params() -> ChannelParams:
    return ChannelParams(
        horizons=(10, 20, 55),
        state_horizon=20,
        clip=1.5,
        ewm_span_floor=2,
        ewm_span_divisor=4,
    )


@pytest.fixture
def state_params() -> StateParams:
    return StateParams(
        pending_entry=0.90,
        pending_exit=0.50,
        confirmed_exit=0.30,
        confirm_consecutive_closes=2,
        confirm_rvol=1.3,
        fail_window_bars=5,
        failed_cooldown_bars=10,
    )


@pytest.fixture
def beta_params() -> BetaParams:
    return BetaParams(window=60, residual_base=100.0)


@pytest.fixture
def rvol_params() -> RVolParams:
    return RVolParams(span=20)


@pytest.fixture
def breadth_params() -> BreadthParams:
    return BreadthParams(horizon=20, narrow_threshold=0.30)


@pytest.fixture
def regime_params() -> RegimeParams:
    return RegimeParams(
        dispersion=DispersionParams(
            smoothing_window=20, percentile_window=252, low_threshold=25.0
        ),
        correlation=CorrelationParams(window=60, sparkline_bars=120),
    )


@pytest.fixture
def rrg_params() -> RRGParams:
    return RRGParams(window=60, origin=100.0, offset=101.0, tail_length=10)


@pytest.fixture
def engine_params(
    channel_params, state_params, beta_params, rvol_params, breadth_params,
    regime_params, rrg_params,
) -> EngineParams:
    return EngineParams(
        atr=ATRParams(period=20),
        beta=beta_params,
        channel=channel_params,
        rvol=rvol_params,
        breadth=breadth_params,
        state=state_params,
        regime=regime_params,
        rrg=rrg_params,
    )


@pytest.fixture
def universe() -> UniverseSpec:
    return UniverseSpec(
        benchmark="SPY",
        sectors=(
            SectorSpec("XLK", "Technology", "RSPT"),
            SectorSpec("XLU", "Utilities", "RSPU"),
            SectorSpec("XLE", "Energy", "RSPG"),
        ),
    )


@pytest.fixture
def ohlcv_factory():
    return make_ohlcv
