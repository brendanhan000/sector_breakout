"""The price provider interface.

Everything downstream depends on this Protocol, never on yfinance or Schwab
directly, so swapping the source is a config change rather than a refactor.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable

import pandas as pd

#: The canonical bar frame: DatetimeIndex named "date", these columns, float64.
BAR_COLUMNS = ("open", "high", "low", "close", "volume")


@runtime_checkable
class PriceProvider(Protocol):
    """Daily split- and dividend-adjusted bars for one symbol.

    Implementations MUST return a frame with a sorted, unique DatetimeIndex and
    exactly ``BAR_COLUMNS``, and MUST return adjusted closes. Mixing adjusted
    and unadjusted history inside one series produces phantom gaps that the
    channel logic reads as real breakouts.

    An empty frame (correct columns, no rows) means "no data for this range" and
    is not an error. Raise :class:`ProviderError` for genuine failures so the
    caller can distinguish an empty window from a broken feed.
    """

    name: str

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        ...


class ProviderError(RuntimeError):
    """The provider failed to answer. Distinct from 'answered, with no rows'."""


def empty_bars() -> pd.DataFrame:
    frame = pd.DataFrame({col: pd.Series(dtype="float64") for col in BAR_COLUMNS})
    frame.index = pd.DatetimeIndex([], name="date")
    return frame


def normalise_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Coerce a provider's raw output into the canonical shape.

    Lower-cases column names, keeps only BAR_COLUMNS, sorts by date, drops
    duplicate dates (keeping the last, which is the provider's latest word on
    that bar) and casts to float64.
    """
    if frame is None or len(frame) == 0:
        return empty_bars()

    out = frame.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]

    missing = [c for c in BAR_COLUMNS if c not in out.columns]
    if missing:
        raise ProviderError(f"provider returned bars missing columns {missing}")

    out = out[list(BAR_COLUMNS)].astype("float64")

    index = pd.DatetimeIndex(pd.to_datetime(out.index)).tz_localize(None).normalize()
    out.index = index
    out.index.name = "date"

    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out
