"""Schwab Trader API provider.

Uses GET /marketdata/v1/pricehistory with an OAuth2 refresh-token flow.

CREDENTIALS COME FROM THE ENVIRONMENT AND ARE NEVER WRITTEN TO config.yaml OR
COMMITTED. See the README for the variables and how to obtain a refresh token.

The token handshake sits behind :class:`TokenProvider` so the HTTP call can be
substituted in tests without a live Schwab account or network access.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Protocol

import httpx
import pandas as pd

from .provider import ProviderError, empty_bars, normalise_bars

log = logging.getLogger(__name__)

#: Schwab returns epoch milliseconds; a trading day's bar is stamped at the open.
_MS = 1000


class TokenProvider(Protocol):
    """Supplies a currently-valid access token."""

    def access_token(self) -> str:
        ...


class RefreshTokenAuth:
    """OAuth2 refresh-token grant with in-memory caching.

    Schwab access tokens are short lived (30 minutes) while the refresh token
    lasts 7 days. This exchanges the refresh token for an access token on first
    use and re-exchanges shortly before expiry. Nothing is persisted to disk —
    a token on disk is a credential on disk.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        token_url: str,
        *,
        leeway_seconds: int = 60,
        client: httpx.Client | None = None,
    ) -> None:
        if not client_id or not client_secret or not refresh_token:
            raise ProviderError(
                "Schwab credentials are incomplete. Set SECTOR_SCHWAB_CLIENT_ID, "
                "SECTOR_SCHWAB_CLIENT_SECRET and SECTOR_SCHWAB_REFRESH_TOKEN."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._token_url = token_url
        self._leeway = leeway_seconds
        self._client = client or httpx.Client(timeout=30.0)

        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0

    def access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - self._leeway:
                return self._token

            response = self._client.post(
                self._token_url,
                data={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
                auth=(self._client_id, self._client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code != 200:
                # Deliberately does not echo the response body: it can contain
                # the credential that was just rejected.
                raise ProviderError(
                    f"Schwab token refresh failed with HTTP {response.status_code}. "
                    "The refresh token expires every 7 days and must be renewed."
                )

            payload = response.json()
            token = payload.get("access_token")
            if not token:
                raise ProviderError("Schwab token response contained no access_token")

            self._token = token
            self._expires_at = time.time() + float(payload.get("expires_in", 1800))
            return token


class SchwabProvider:
    """Daily adjusted bars from the Schwab Trader API."""

    name = "schwab"

    def __init__(
        self,
        auth: TokenProvider,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.5,
    ) -> None:
        self._auth = auth
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
                    headers={"Authorization": f"Bearer {self._auth.access_token()}"},
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
    """Construct a SchwabProvider from application settings."""
    auth = RefreshTokenAuth(
        client_id=settings.schwab_client_id or "",
        client_secret=settings.schwab_client_secret or "",
        refresh_token=settings.schwab_refresh_token or "",
        token_url=settings.schwab_token_url,
    )
    return SchwabProvider(auth=auth, base_url=settings.schwab_base_url)
