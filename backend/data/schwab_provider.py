"""Schwab price history via the central schwab_hub.

Uses GET /marketdata/v1/pricehistory. The hub owns the Schwab credentials and
token refresh; this repo holds none. Start it with ../schwab_hub/run.sh.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

import httpx
import pandas as pd

from .provider import ProviderError, empty_bars, normalise_bars

log = logging.getLogger(__name__)

#: Schwab returns epoch milliseconds; a trading day's bar is stamped at the open.
_MS = 1000


class SchwabProvider:
    """Daily adjusted bars from the Schwab Trader API."""

    name = "schwab"

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30.0)
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        params = {
            "symbol": symbol,
            "periodType": "year",
            "frequencyType": "daily",
            "frequency": 1,
            "startDate": int(
                dt.datetime.combine(start, dt.time.min, dt.timezone.utc).timestamp() * _MS
            ),
            "endDate": int(
                dt.datetime.combine(end, dt.time.max, dt.timezone.utc).timestamp() * _MS
            ),
            # Schwab's adjusted series. Unadjusted history would show every
            # dividend ex-date as a gap the channel logic reads as a breakdown.
            "needExtendedHoursData": "false",
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.get(
                    f"{self._base_url}/marketdata/v1/pricehistory",
                    params=params,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * attempt)
                    continue
                raise ProviderError(f"Schwab request failed for {symbol}: {exc}") from exc

            if response.status_code == 429 and attempt < self.max_retries:
                time.sleep(self.backoff_seconds * attempt * 2)
                continue
            if response.status_code != 200:
                raise ProviderError(
                    f"Schwab pricehistory returned HTTP {response.status_code} for {symbol}"
                )

            return self._parse(response.json(), symbol)

        raise ProviderError(f"Schwab request failed for {symbol}: {last_error}")

    @staticmethod
    def _parse(payload: dict, symbol: str) -> pd.DataFrame:
        if payload.get("empty") or not payload.get("candles"):
            log.warning("Schwab returned no candles for %s", symbol)
            return empty_bars()

        frame = pd.DataFrame(payload["candles"])
        expected = {"datetime", "open", "high", "low", "close", "volume"}
        if not expected.issubset(frame.columns):
            raise ProviderError(
                f"Schwab candle payload for {symbol} is missing {expected - set(frame.columns)}"
            )

        frame.index = pd.to_datetime(frame["datetime"], unit="ms", utc=True).dt.tz_localize(None)
        return normalise_bars(frame.drop(columns=["datetime"]))


def build_schwab_provider(settings) -> SchwabProvider:
    """Construct a SchwabProvider pointed at the schwab_hub."""
    return SchwabProvider(base_url=settings.schwab_hub_url)
