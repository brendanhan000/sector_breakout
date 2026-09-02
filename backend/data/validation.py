"""Ingest validation and quarantine.

A symbol that fails ANY check is quarantined: it is not written to ``bars`` and
no signals are computed for it on that run. The alternative — letting bad data
through and hoping the engine copes — produces a dashboard that is confidently
wrong, which is the failure mode this whole system is built to avoid.

Quarantine is per symbol and per run. It does not poison already-stored good
history, and the next run re-checks from scratch.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .provider import BAR_COLUMNS


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    detail: str
    sample: tuple = ()


@dataclass(slots=True)
class ValidationResult:
    """Violations quarantine the symbol. Warnings are recorded but do not.

    The split exists because not every anomaly affects a signal. A bar whose
    OPEN sits outside its own high/low range is malformed, but no function in
    the engine reads the open — ATR uses high/low/previous close, the channel
    uses high/low/close. Quarantining a sector over a field nothing consumes
    would throw away real information (its whole breadth twin) to protect
    against nothing. Warnings keep it visible in the run log instead.
    """

    symbol: str
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "ok": self.ok,
            "violations": [
                {"code": v.code, "detail": v.detail, "sample": [str(s) for s in v.sample]}
                for v in self.violations
            ],
            "warnings": [
                {"code": v.code, "detail": v.detail, "sample": [str(s) for s in v.sample]}
                for v in self.warnings
            ],
        }


def validate_bars(
    symbol: str,
    frame: pd.DataFrame,
    *,
    max_gap_trading_days: int,
    allow_zero_volume: bool = True,
    min_rows: int = 2,
    envelope_rtol: float = 1e-9,
) -> ValidationResult:
    """Check one symbol's bars. Empty violation list means safe to ingest."""
    result = ValidationResult(symbol=symbol)

    if frame is None or frame.empty:
        result.violations.append(Violation("EMPTY", "provider returned no rows"))
        return result

    missing = [c for c in BAR_COLUMNS if c not in frame.columns]
    if missing:
        result.violations.append(Violation("MISSING_COLUMNS", f"missing {missing}"))
        return result

    if len(frame) < min_rows:
        result.violations.append(
            Violation("TOO_SHORT", f"only {len(frame)} rows, need at least {min_rows}")
        )

    # ---- NaNs -------------------------------------------------------------
    nan_counts = frame[list(BAR_COLUMNS)].isna().sum()
    nan_cols = {c: int(n) for c, n in nan_counts.items() if n > 0}
    if nan_cols:
        bad_index = frame.index[frame[list(BAR_COLUMNS)].isna().any(axis=1)]
        result.violations.append(
            Violation("NAN", f"NaN values present: {nan_cols}", tuple(bad_index[:5]))
        )

    # ---- duplicate dates --------------------------------------------------
    duplicated = frame.index[frame.index.duplicated(keep=False)]
    if len(duplicated) > 0:
        result.violations.append(
            Violation(
                "DUPLICATE_DATES",
                f"{len(duplicated)} rows share a date with another row",
                tuple(pd.unique(duplicated)[:5]),
            )
        )

    if not frame.index.is_monotonic_increasing:
        result.violations.append(Violation("UNSORTED", "index is not sorted ascending"))

    # ---- OHLC geometry ----------------------------------------------------
    finite = frame[list(BAR_COLUMNS)].notna().all(axis=1)
    checked = frame[finite]

    bad_hl = checked.index[checked["high"] < checked["low"]]
    if len(bad_hl) > 0:
        result.violations.append(
            Violation("HIGH_LT_LOW", f"{len(bad_hl)} bars with high < low", tuple(bad_hl[:5]))
        )

    # ---- OHLC envelope ----------------------------------------------------
    # Adjusted prices are floating-point products of an adjustment factor, so a
    # bar where close == low can land a few ULPs (~1e-16 relative) outside its
    # own range. A relative tolerance keeps that arithmetic noise from
    # quarantining an otherwise perfect five-year history.
    tolerance = checked["close"].abs() * envelope_rtol

    close_outside = checked.index[
        (checked["close"] > checked["high"] + tolerance)
        | (checked["close"] < checked["low"] - tolerance)
    ]
    if len(close_outside) > 0:
        # The close drives every signal in the system, so this is fatal.
        result.violations.append(
            Violation(
                "CLOSE_OUTSIDE_RANGE",
                f"{len(close_outside)} bars where the close is outside its own high/low",
                tuple(close_outside[:5]),
            )
        )

    open_outside = checked.index[
        (checked["open"] > checked["high"] + tolerance)
        | (checked["open"] < checked["low"] - tolerance)
    ]
    if len(open_outside) > 0:
        # Warning, not a violation. Vendors routinely report a stale open on an
        # in-progress session, and no engine function reads the open — ATR uses
        # high/low/previous close and the channel uses high/low/close. Dropping
        # a sector's equal-weight twin (and therefore its NARROW badge) over a
        # field nothing consumes trades real information for no protection.
        result.warnings.append(
            Violation(
                "OPEN_OUTSIDE_RANGE",
                f"{len(open_outside)} bars where the open is outside its own high/low",
                tuple(open_outside[:5]),
            )
        )

    bad_volume = checked.index[checked["volume"] < 0]
    if len(bad_volume) > 0:
        result.violations.append(
            Violation("NEGATIVE_VOLUME", f"{len(bad_volume)} bars with volume < 0",
                      tuple(bad_volume[:5]))
        )

    if not allow_zero_volume:
        zero_volume = checked.index[checked["volume"] == 0]
        if len(zero_volume) > 0:
            result.violations.append(
                Violation("ZERO_VOLUME", f"{len(zero_volume)} bars with zero volume",
                          tuple(zero_volume[:5]))
            )

    nonpositive = checked.index[(checked[["open", "high", "low", "close"]] <= 0).any(axis=1)]
    if len(nonpositive) > 0:
        result.violations.append(
            Violation("NONPOSITIVE_PRICE", f"{len(nonpositive)} bars with a price <= 0",
                      tuple(nonpositive[:5]))
        )

    # ---- gaps -------------------------------------------------------------
    gaps = find_gaps(frame.index, max_gap_trading_days)
    if gaps:
        result.violations.append(
            Violation(
                "GAP",
                f"{len(gaps)} gap(s) longer than {max_gap_trading_days} trading days",
                tuple(f"{a.date()}->{b.date()} ({n}d)" for a, b, n in gaps[:5]),
            )
        )

    return result


def find_gaps(index: pd.DatetimeIndex, max_gap_trading_days: int) -> list[tuple]:
    """Consecutive bars separated by more than ``max_gap_trading_days``.

    Business days stand in for trading days. Exchange holidays therefore count
    as missing days, which is why the threshold is 5 rather than 1: a holiday
    week costs at most one or two, well inside the tolerance, so this flags
    genuine feed outages without false-positiving on Thanksgiving.
    """
    if len(index) < 2:
        return []

    unique = pd.DatetimeIndex(pd.unique(index)).sort_values()
    gaps: list[tuple] = []
    for prev, cur in zip(unique[:-1], unique[1:]):
        # Business days strictly between the two bars.
        missing = int(np.busday_count(prev.date(), cur.date())) - 1
        if missing > max_gap_trading_days:
            gaps.append((prev, cur, missing))
    return gaps


def detect_adjustment_change(
    stored: pd.DataFrame, fetched: pd.DataFrame, *, rtol: float, check_bars: int
) -> bool:
    """True when historical closes have moved, implying a new adjustment factor.

    A split or dividend restates the ENTIRE history. If we only appended new
    bars we would end up with a series that is adjusted before some date and
    unadjusted after it — a discontinuity the channel logic reads as a genuine
    breakout. Detecting this and re-pulling the whole symbol is the only safe
    response; patching the tail is not.
    """
    if stored.empty or fetched.empty:
        return False

    overlap = stored.index.intersection(fetched.index)
    if len(overlap) == 0:
        return False

    overlap = overlap.sort_values()[-check_bars:]
    a = stored.loc[overlap, "close"].to_numpy(dtype="float64")
    b = fetched.loc[overlap, "close"].to_numpy(dtype="float64")

    both_valid = np.isfinite(a) & np.isfinite(b) & (a != 0)
    if not both_valid.any():
        return False

    relative = np.abs(b[both_valid] - a[both_valid]) / np.abs(a[both_valid])
    return bool(np.nanmax(relative) > rtol)
