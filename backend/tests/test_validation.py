"""Ingest validation: malformed inputs are quarantined, not propagated.

The contract is blunt — a symbol whose data fails ANY check produces no signals
on that run. Silently computing a channel from a series with a NaN, a duplicated
date or a three-week hole yields a number that looks exactly like a real signal
on the dashboard, and there is no way for a user to tell the difference.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from backend.data.provider import BAR_COLUMNS, ProviderError, empty_bars, normalise_bars
from backend.data.validation import (
    detect_adjustment_change,
    find_gaps,
    validate_bars,
)

GAP_LIMIT = 5


def good_frame(n: int = 60, start: str = "2022-01-03") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    close = 100 + np.arange(n, dtype="float64") * 0.1
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1e6),
        },
        index=idx,
    )


def codes(result) -> set[str]:
    return {v.code for v in result.violations}


def test_clean_frame_passes():
    result = validate_bars("XLK", good_frame(), max_gap_trading_days=GAP_LIMIT)
    assert result.ok
    assert result.violations == []


def test_nan_is_quarantined():
    frame = good_frame()
    frame.iloc[10, frame.columns.get_loc("close")] = np.nan
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "NAN" in codes(result)


def test_high_below_low_is_quarantined():
    frame = good_frame()
    frame.iloc[5, frame.columns.get_loc("high")] = 1.0
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "HIGH_LT_LOW" in codes(result)


def test_bar_where_high_does_not_contain_close_is_quarantined():
    """high >= low can hold while the bar is still internally inconsistent."""
    frame = good_frame()
    row = 7
    frame.iloc[row, frame.columns.get_loc("high")] = frame.iloc[row]["close"] - 1.0
    frame.iloc[row, frame.columns.get_loc("low")] = frame.iloc[row]["close"] - 2.0
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "CLOSE_OUTSIDE_RANGE" in codes(result)


def test_negative_volume_is_quarantined():
    frame = good_frame()
    frame.iloc[3, frame.columns.get_loc("volume")] = -1.0
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "NEGATIVE_VOLUME" in codes(result)


def test_zero_volume_is_allowed_by_default_but_can_be_rejected():
    """Some ETFs legitimately print zero volume, so this is configurable."""
    frame = good_frame()
    frame.iloc[3, frame.columns.get_loc("volume")] = 0.0

    assert validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT,
                         allow_zero_volume=True).ok
    strict = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT,
                           allow_zero_volume=False)
    assert "ZERO_VOLUME" in codes(strict)


def test_duplicate_dates_are_quarantined():
    frame = good_frame()
    frame = pd.concat([frame, frame.iloc[[10]]]).sort_index()
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "DUPLICATE_DATES" in codes(result)


def test_nonpositive_price_is_quarantined():
    frame = good_frame()
    frame.iloc[8, frame.columns.get_loc("low")] = 0.0
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "NONPOSITIVE_PRICE" in codes(result)


def test_long_gap_is_quarantined():
    frame = good_frame(n=40)
    # Drop three weeks in the middle.
    frame = pd.concat([frame.iloc[:20], frame.iloc[35:]])
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "GAP" in codes(result)


def test_short_holiday_gap_is_tolerated():
    """A long weekend or a holiday week must not quarantine a healthy symbol."""
    frame = good_frame(n=40)
    frame = pd.concat([frame.iloc[:20], frame.iloc[23:]])   # 3 missing bdays
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert result.ok, f"a 3-day gap should be tolerated, got {codes(result)}"


def test_empty_frame_is_quarantined():
    result = validate_bars("XLK", empty_bars(), max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "EMPTY" in codes(result)


def test_missing_columns_are_quarantined():
    frame = good_frame().drop(columns=["volume"])
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "MISSING_COLUMNS" in codes(result)


def test_multiple_violations_are_all_reported():
    """The log should say everything that is wrong, not just the first thing."""
    frame = good_frame()
    frame.iloc[5, frame.columns.get_loc("high")] = 1.0
    frame.iloc[6, frame.columns.get_loc("volume")] = -5.0
    frame.iloc[7, frame.columns.get_loc("close")] = np.nan
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert {"HIGH_LT_LOW", "NEGATIVE_VOLUME", "NAN"} <= codes(result)


def test_violations_serialise_for_the_run_log():
    frame = good_frame()
    frame.iloc[5, frame.columns.get_loc("high")] = 1.0
    payload = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT).as_dict()
    assert payload["symbol"] == "XLK"
    assert payload["ok"] is False
    assert isinstance(payload["violations"][0]["detail"], str)
    import json

    json.dumps(payload)  # must be JSON-serialisable for the run_log column


# --------------------------------------------------------------------------- #
# Gap detection
# --------------------------------------------------------------------------- #
def test_find_gaps_counts_business_days():
    idx = pd.DatetimeIndex(["2022-01-03", "2022-01-04", "2022-01-20"])
    gaps = find_gaps(idx, max_gap_trading_days=5)
    assert len(gaps) == 1
    assert gaps[0][2] == 11


def test_weekend_is_not_a_gap():
    idx = pd.DatetimeIndex(["2022-01-07", "2022-01-10"])   # Friday -> Monday
    assert find_gaps(idx, max_gap_trading_days=5) == []


def test_single_bar_has_no_gaps():
    assert find_gaps(pd.DatetimeIndex(["2022-01-03"]), max_gap_trading_days=5) == []


# --------------------------------------------------------------------------- #
# Adjustment-factor detection
# --------------------------------------------------------------------------- #
def test_unchanged_history_is_not_an_adjustment_change():
    stored = good_frame()
    fetched = good_frame()
    assert not detect_adjustment_change(stored, fetched, rtol=0.001, check_bars=60)


def test_a_split_restates_history_and_is_detected():
    """A 2-for-1 split halves every historical close.

    Appending new bars onto an un-restated history would leave a series that is
    unadjusted before the split and adjusted after it — a 50% discontinuity the
    channel logic cannot distinguish from a crash.
    """
    stored = good_frame()
    fetched = good_frame()
    fetched[["open", "high", "low", "close"]] /= 2.0
    assert detect_adjustment_change(stored, fetched, rtol=0.001, check_bars=60)


def test_a_dividend_sized_adjustment_is_detected():
    stored = good_frame()
    fetched = good_frame()
    fetched[["open", "high", "low", "close"]] *= 0.995   # ~0.5%, above the 0.1% tolerance
    assert detect_adjustment_change(stored, fetched, rtol=0.001, check_bars=60)


def test_floating_point_noise_is_not_an_adjustment_change():
    stored = good_frame()
    fetched = good_frame()
    fetched["close"] += 1e-9
    assert not detect_adjustment_change(stored, fetched, rtol=0.001, check_bars=60)


def test_no_overlap_means_no_conclusion():
    stored = good_frame(n=20, start="2022-01-03")
    fetched = good_frame(n=20, start="2023-01-03")
    assert not detect_adjustment_change(stored, fetched, rtol=0.001, check_bars=60)


def test_empty_inputs_are_safe():
    assert not detect_adjustment_change(empty_bars(), good_frame(), rtol=0.001, check_bars=60)
    assert not detect_adjustment_change(good_frame(), empty_bars(), rtol=0.001, check_bars=60)


# --------------------------------------------------------------------------- #
# Provider frame normalisation
# --------------------------------------------------------------------------- #
def test_normalise_lowercases_and_orders_columns():
    raw = pd.DataFrame(
        {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10.0],
         "Dividends": [0.0]},
        index=pd.DatetimeIndex(["2022-01-03"]),
    )
    out = normalise_bars(raw)
    assert list(out.columns) == list(BAR_COLUMNS)
    assert out.index.name == "date"


def test_normalise_drops_duplicate_dates_keeping_the_latest():
    raw = pd.DataFrame(
        {"open": [1.0, 9.0], "high": [1.0, 9.0], "low": [1.0, 9.0],
         "close": [1.0, 9.0], "volume": [1.0, 2.0]},
        index=pd.DatetimeIndex(["2022-01-03", "2022-01-03"]),
    )
    out = normalise_bars(raw)
    assert len(out) == 1
    assert out["close"].iloc[0] == 9.0


def test_normalise_strips_timezone_and_time_of_day():
    raw = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=pd.DatetimeIndex(["2022-01-03 14:30:00-05:00"]),
    )
    out = normalise_bars(raw)
    assert out.index[0] == pd.Timestamp("2022-01-03")


def test_normalise_sorts_by_date():
    raw = pd.DataFrame(
        {"open": [2.0, 1.0], "high": [2.0, 1.0], "low": [2.0, 1.0],
         "close": [2.0, 1.0], "volume": [1.0, 1.0]},
        index=pd.DatetimeIndex(["2022-01-04", "2022-01-03"]),
    )
    out = normalise_bars(raw)
    assert out.index.is_monotonic_increasing
    assert out["close"].tolist() == [1.0, 2.0]


def test_normalise_rejects_a_frame_missing_required_columns():
    raw = pd.DataFrame({"open": [1.0]}, index=pd.DatetimeIndex(["2022-01-03"]))
    with pytest.raises(ProviderError, match="missing"):
        normalise_bars(raw)


def test_normalise_of_nothing_returns_the_canonical_empty_frame():
    out = normalise_bars(pd.DataFrame())
    assert out.empty
    assert list(out.columns) == list(BAR_COLUMNS)


# --------------------------------------------------------------------------- #
# The violation / warning split
# --------------------------------------------------------------------------- #
def test_float_noise_in_the_envelope_does_not_quarantine():
    """Adjusted prices are floating-point products of an adjustment factor.

    A bar where close == low can land a few ULPs outside its own range. This is
    arithmetic noise, and quarantining an otherwise perfect five-year history
    over 1e-16 would be absurd. Observed on real RSPM and RSPR data.
    """
    frame = good_frame()
    row = 5
    # Nudge low above close by ~1e-16 relative, exactly as float error does.
    close = frame.iloc[row]["close"]
    frame.iloc[row, frame.columns.get_loc("low")] = close * (1 + 1e-15)
    frame.iloc[row, frame.columns.get_loc("close")] = close

    result = validate_bars("RSPM", frame, max_gap_trading_days=GAP_LIMIT)
    assert result.ok, f"float noise triggered a quarantine: {codes(result)}"


def test_a_close_outside_its_own_range_is_fatal():
    """The close drives every signal, so this one really is a quarantine."""
    frame = good_frame()
    frame.iloc[5, frame.columns.get_loc("close")] = frame.iloc[5]["high"] * 1.05
    result = validate_bars("XLK", frame, max_gap_trading_days=GAP_LIMIT)
    assert not result.ok
    assert "CLOSE_OUTSIDE_RANGE" in codes(result)


def test_an_open_outside_its_own_range_warns_but_does_not_quarantine():
    """Observed on real Yahoo data for in-progress sessions.

    No engine function reads the open — ATR uses high/low/previous close, the
    channel uses high/low/close. Dropping a sector's equal-weight twin (and with
    it the NARROW badge) over a field nothing consumes would trade away real
    information for no protection at all.
    """
    frame = good_frame()
    frame.iloc[5, frame.columns.get_loc("open")] = frame.iloc[5]["high"] * 1.01

    result = validate_bars("RSPG", frame, max_gap_trading_days=GAP_LIMIT)
    assert result.ok, "a stale open must not quarantine the symbol"
    assert {w.code for w in result.warnings} == {"OPEN_OUTSIDE_RANGE"}


def test_warnings_are_serialised_into_the_run_log():
    import json

    frame = good_frame()
    frame.iloc[5, frame.columns.get_loc("open")] = frame.iloc[5]["high"] * 1.01
    payload = validate_bars("RSPG", frame, max_gap_trading_days=GAP_LIMIT).as_dict()

    assert payload["ok"] is True
    assert payload["warnings"][0]["code"] == "OPEN_OUTSIDE_RANGE"
    json.dumps(payload)


def test_engine_never_reads_the_open_column():
    """Pins the justification for treating an out-of-range open as a warning.

    If a future change makes a signal depend on the open, this fails and the
    warning must be promoted back to a violation.
    """
    import ast
    from pathlib import Path

    engine_dir = Path(__file__).resolve().parents[2] / "backend" / "engine"
    readers: list[str] = []

    for path in engine_dir.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            # Matches df["open"] anywhere in the engine.
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "open"
            ):
                readers.append(f"{path.name}:{node.lineno}")

    # beta.residual_ohlc scales the open through for charting; it feeds no
    # signal. Anything beyond that list is a real dependency.
    allowed_files = {"beta.py"}
    unexpected = [r for r in readers if r.split(":")[0] not in allowed_files]
    assert unexpected == [], (
        f"the engine now reads the open at {unexpected}; OPEN_OUTSIDE_RANGE must "
        "be promoted from a warning back to a violation"
    )
