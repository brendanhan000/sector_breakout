"""Wilder ATR against hand-computed values on a 20-bar fixture.

The fixture uses integer OHLC so every true range is an exact integer and every
ATR value has a short exact decimal expansion. That means the expectations below
are literal hand-derived numbers, not the output of the code under test pasted
back in — which is the only version of this test that can actually catch a
regression.

Bar-by-bar derivation, TR_t = max(H-L, |H - C_{t-1}|, |L - C_{t-1}|):

     t   H    L    C     H-L  |H-Cp|  |L-Cp|   TR
     0  105   95  100     10     --      --    10   (no prior close -> H-L)
     1  108  100  106      8      8       0     8
     2  110  104  105      6      4       2     6
     3  112  106  111      6      7       1     7
     4  115  109  110      6      4       2     6
     5  120  112  118      8     10       2    10
     6  119  111  112      8      1       7     8
     7  114  106  108      8      2       6     8
     8  116  108  115      8      8       0     8
     9  118  113  114      5      3       2     5
    10  120  112  119      8      6       2     8
    11  122  116  117      6      3       3     6
    12  121  113  120      8      4       4     8
    13  125  118  124      7      5       2     7
    14  127  121  122      6      3       3     6
    15  124  116  118      8      2       6     8
    16  126  117  125      9      8       1     9
    17  128  122  123      6      3       3     6
    18  130  121  129      9      7       2     9
    19  133  126  127      7      4       3     7

    sum(TR) = 150  ->  ATR(20) seed at t=19 = 150 / 20 = 7.5 exactly
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.atr import true_range, wilder_atr

BARS = [
    (105, 95, 100), (108, 100, 106), (110, 104, 105), (112, 106, 111),
    (115, 109, 110), (120, 112, 118), (119, 111, 112), (114, 106, 108),
    (116, 108, 115), (118, 113, 114), (120, 112, 119), (122, 116, 117),
    (121, 113, 120), (125, 118, 124), (127, 121, 122), (124, 116, 118),
    (126, 117, 125), (128, 122, 123), (130, 121, 129), (133, 126, 127),
]

EXPECTED_TR = [10, 8, 6, 7, 6, 10, 8, 8, 8, 5, 8, 6, 8, 7, 6, 8, 9, 6, 9, 7]


@pytest.fixture
def fixture_bars() -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=len(BARS))
    return pd.DataFrame(
        {
            "open": [c for _, _, c in BARS],
            "high": [h for h, _, _ in BARS],
            "low": [l for _, l, _ in BARS],
            "close": [c for _, _, c in BARS],
            "volume": [1_000_000] * len(BARS),
        },
        index=idx,
    ).astype("float64")


def test_true_range_matches_hand_computation(fixture_bars):
    tr = true_range(fixture_bars)
    assert tr.tolist() == [float(x) for x in EXPECTED_TR]


def test_first_bar_true_range_is_high_low(fixture_bars):
    """No prior close exists, so TR degenerates to the high-low span."""
    tr = true_range(fixture_bars)
    assert tr.iloc[0] == 105.0 - 95.0


def test_atr_seed_is_simple_mean_of_first_n_true_ranges(fixture_bars):
    """ATR(20) at t=19 is exactly sum(TR)/20 = 150/20 = 7.5."""
    atr = wilder_atr(fixture_bars, 20)
    assert atr.iloc[:19].isna().all(), "ATR must be NaN before the seed bar"
    assert atr.iloc[19] == pytest.approx(7.5, abs=1e-12)


def test_atr_recursion_matches_hand_computation(fixture_bars):
    """ATR(5), derived longhand.

        seed t=4 : (10 + 8 + 6 + 7 + 6) / 5            = 37 / 5      = 7.4
        t=5      : (7.4      * 4 + 10) / 5 = 39.6 / 5              = 7.92
        t=6      : (7.92     * 4 +  8) / 5 = 39.68 / 5             = 7.936
        t=7      : (7.936    * 4 +  8) / 5 = 39.744 / 5            = 7.9488
        t=8      : (7.9488   * 4 +  8) / 5 = 39.7952 / 5           = 7.95904
        t=9      : (7.95904  * 4 +  5) / 5 = 36.83616 / 5          = 7.367232
    """
    atr = wilder_atr(fixture_bars, 5)

    assert atr.iloc[:4].isna().all()
    assert atr.iloc[4] == pytest.approx(7.4, abs=1e-12)
    assert atr.iloc[5] == pytest.approx(7.92, abs=1e-12)
    assert atr.iloc[6] == pytest.approx(7.936, abs=1e-12)
    assert atr.iloc[7] == pytest.approx(7.9488, abs=1e-12)
    assert atr.iloc[8] == pytest.approx(7.95904, abs=1e-12)
    assert atr.iloc[9] == pytest.approx(7.367232, abs=1e-12)


def test_atr_is_not_a_simple_moving_average(fixture_bars):
    """Wilder smoothing must differ from a rolling mean of true range.

    A common silent bug is reaching for ``.rolling(N).mean()`` because it is one
    line shorter. Wilder's recursion has a much longer effective memory (it is
    an EMA with alpha = 1/N, not 2/(N+1)), so the two series diverge.
    """
    atr = wilder_atr(fixture_bars, 5)
    sma = true_range(fixture_bars).rolling(5, min_periods=5).mean()
    tail = slice(6, None)
    assert not np.allclose(
        atr.iloc[tail].to_numpy(), sma.iloc[tail].to_numpy()
    ), "ATR is behaving like a simple moving average of true range"


def test_atr_period_longer_than_series_returns_all_nan(fixture_bars):
    atr = wilder_atr(fixture_bars, 50)
    assert atr.isna().all()


def test_atr_rejects_nonpositive_period(fixture_bars):
    with pytest.raises(ValueError):
        wilder_atr(fixture_bars, 0)


def test_atr_is_nonnegative_on_real_shaped_data():
    g = np.random.default_rng(3)
    n = 300
    close = 100 * np.exp(np.cumsum(g.normal(0, 0.01, n)))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1e6),
        },
        index=pd.bdate_range("2020-01-01", periods=n),
    )
    atr = wilder_atr(df, 20).dropna()
    assert (atr > 0).all()


# --------------------------------------------------------------------------- #
# Seeding after a warm-up period of NaNs (the relative plane)
# --------------------------------------------------------------------------- #
def test_atr_seeds_after_a_leading_block_of_nans():
    """REGRESSION: the residual plane starts with a full beta warm-up of NaNs.

    An implementation that only seeds from bar 0 returns an all-NaN ATR for the
    entire relative plane. That silently makes z_up/z_dn undefined for every
    sector on Plane B, which in turn leaves the rotation leaderboard — whose
    whole job is to sort by extension — sorting on nothing at all. Nothing about
    the dashboard looks broken when this happens, which is what makes it
    dangerous.
    """
    warmup = 60
    n = 200
    close = np.concatenate([np.full(warmup, np.nan), 100 + np.arange(n - warmup) * 0.5])
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1e6),
        },
        index=pd.bdate_range("2020-01-01", periods=n),
    )

    atr = wilder_atr(df, 20)

    assert atr.iloc[:warmup].isna().all(), "no ATR can exist during the warm-up"
    assert atr.notna().sum() > 0, "ATR never seeded after the warm-up ended"
    # First valid TR is at `warmup`; the seed needs 20 of them.
    assert not pd.isna(atr.iloc[warmup + 19])
    assert (atr.iloc[warmup + 19 :] > 0).all()


def test_atr_reseeds_after_an_interior_gap_of_nans():
    """An interior NaN must not poison every subsequent bar."""
    n = 200
    close = 100 + np.arange(n, dtype="float64") * 0.5
    close[80:85] = np.nan
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1e6),
        },
        index=pd.bdate_range("2020-01-01", periods=n),
    )

    atr = wilder_atr(df, 20)
    assert not pd.isna(atr.iloc[50]), "ATR should be defined before the gap"
    assert not pd.isna(atr.iloc[-1]), "ATR should recover after the gap"


def test_atr_seeding_is_unaffected_by_future_bars():
    """The seed position must be causal — appending bars cannot move it."""
    warmup = 30
    n = 300
    close = np.concatenate([np.full(warmup, np.nan), 100 + np.arange(n - warmup) * 0.4])
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1e6),
        },
        index=pd.bdate_range("2020-01-01", periods=n),
    )

    full = wilder_atr(frame, 20)
    for k in (1, 5, 20, 60):
        truncated = wilder_atr(frame.iloc[:-k], 20)
        a = full.iloc[: n - k].to_numpy()
        b = truncated.to_numpy()
        both_nan = np.isnan(a) & np.isnan(b)
        assert (both_nan | (a == b)).all(), f"ATR seeding shifted under truncation at k={k}"
