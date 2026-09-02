"""yfinance provider — the default, used for development and backfill."""

from __future__ import annotations

import datetime as dt
import logging
import time

import pandas as pd

from .provider import ProviderError, empty_bars, normalise_bars

log = logging.getLogger(__name__)


class YFinanceProvider:
    """Daily adjusted bars from Yahoo Finance.

    ``auto_adjust=True`` returns OHLC adjusted for splits AND dividends, which
    is what the engine needs: an unadjusted series shows a dividend ex-date as a
    real gap down, and the channel logic cannot tell that apart from a breakdown.
    """

    name = "yfinance"

    def __init__(self, max_retries: int = 3, backoff_seconds: float = 1.5) -> None:
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        import yfinance as yf

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                raw = yf.Ticker(symbol).history(
                    start=start.isoformat(),
                    # yfinance treats `end` as exclusive.
                    end=(end + dt.timedelta(days=1)).isoformat(),
                    interval="1d",
                    auto_adjust=True,
                    actions=False,
                    raise_errors=True,
                )
            except Exception as exc:  # noqa: BLE001 — provider errors are opaque
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * attempt)
                    continue
                raise ProviderError(f"yfinance failed for {symbol}: {exc}") from exc

            if raw is None or raw.empty:
                log.warning("yfinance returned no rows for %s (%s..%s)", symbol, start, end)
                return empty_bars()

            return normalise_bars(raw)

        raise ProviderError(f"yfinance failed for {symbol}: {last_error}")
