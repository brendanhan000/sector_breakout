"""Ingest: fetch, validate, quarantine, upsert.

Idempotent by construction — the upsert is keyed on (symbol, date), so running
the same day twice updates in place rather than duplicating. That matters more
than it sounds: the daily job WILL be re-run by hand, and a system that
double-counts bars on a retry is a system nobody can safely retry.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.db.models import Bar
from backend.settings import AppConfig

from .provider import BAR_COLUMNS, PriceProvider, ProviderError, empty_bars
from .validation import ValidationResult, detect_adjustment_change, validate_bars

log = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestReport:
    requested: list[str] = field(default_factory=list)
    ingested: list[str] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    repulled: list[str] = field(default_factory=list)
    bars_upserted: int = 0
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def quarantined_symbols(self) -> tuple[str, ...]:
        return tuple(q["symbol"] for q in self.quarantined)


def load_bars(session: Session, symbol: str) -> pd.DataFrame:
    """Read a symbol's stored bars back into the canonical frame shape."""
    rows = session.execute(
        select(Bar.date, Bar.open, Bar.high, Bar.low, Bar.close, Bar.volume)
        .where(Bar.symbol == symbol)
        .order_by(Bar.date)
    ).all()

    if not rows:
        return empty_bars()

    frame = pd.DataFrame(rows, columns=["date", *BAR_COLUMNS])
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("date")), name="date")
    return frame.astype("float64")


def load_many(session: Session, symbols: list[str]) -> dict[str, pd.DataFrame]:
    return {symbol: load_bars(session, symbol) for symbol in symbols}


def latest_stored_date(session: Session, symbol: str) -> dt.date | None:
    return session.execute(
        select(Bar.date).where(Bar.symbol == symbol).order_by(Bar.date.desc()).limit(1)
    ).scalar_one_or_none()


def upsert_bars(session: Session, symbol: str, frame: pd.DataFrame, provider_name: str) -> int:
    """Insert-or-update on (symbol, date). Returns the number of rows written.

    Uses the dialect's native upsert so a re-run is a single statement rather
    than a read-modify-write race.
    """
    if frame.empty:
        return 0

    payload = [
        {
            "symbol": symbol,
            "date": index.date() if hasattr(index, "date") else index,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
            "provider": provider_name,
        }
        for index, row in frame.iterrows()
    ]

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(Bar).values(payload)
    elif dialect == "sqlite":
        stmt = sqlite_insert(Bar).values(payload)
    else:  # pragma: no cover — no other dialect is supported
        raise RuntimeError(f"upsert is not implemented for dialect {dialect!r}")

    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "date"],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
            "provider": stmt.excluded.provider,
        },
    )
    session.execute(stmt)
    return len(payload)


def delete_symbol(session: Session, symbol: str) -> None:
    session.execute(delete(Bar).where(Bar.symbol == symbol))


def ingest_symbol(
    session: Session,
    provider: PriceProvider,
    symbol: str,
    config: AppConfig,
    *,
    today: dt.date,
    report: IngestReport,
) -> None:
    """Fetch, validate and upsert one symbol, recording the outcome in ``report``."""
    validation_cfg = config.data.validation
    stored = load_bars(session, symbol)
    last = latest_stored_date(session, symbol)

    backfill_start = today - dt.timedelta(days=365 * config.data.backfill_years + 7)

    if last is None:
        start = backfill_start
        mode = "backfill"
    else:
        # Overlap by the adjustment-check window so a restated history is
        # visible. Fetching only strictly-new bars would make a split invisible.
        start = last - dt.timedelta(days=validation_cfg.adjustment_check_bars * 2)
        mode = "incremental"

    try:
        fetched = provider.get_daily_bars(symbol, start, today)
    except ProviderError as exc:
        log.error("provider failed for %s: %s", symbol, exc)
        report.errors[symbol] = str(exc)
        report.quarantined.append(
            {"symbol": symbol, "ok": False,
             "violations": [{"code": "PROVIDER_ERROR", "detail": str(exc), "sample": []}]}
        )
        return

    # ---- adjustment-factor change -> full re-pull --------------------------
    if mode == "incremental" and detect_adjustment_change(
        stored, fetched, rtol=validation_cfg.adjustment_rtol,
        check_bars=validation_cfg.adjustment_check_bars,
    ):
        log.warning(
            "%s: historical closes were restated (split/dividend adjustment). "
            "Re-pulling the full history rather than mixing adjustment bases.", symbol,
        )
        try:
            fetched = provider.get_daily_bars(symbol, backfill_start, today)
        except ProviderError as exc:
            report.errors[symbol] = str(exc)
            report.quarantined.append(
                {"symbol": symbol, "ok": False,
                 "violations": [{"code": "REPULL_FAILED", "detail": str(exc), "sample": []}]}
            )
            return
        report.repulled.append(symbol)
        delete_symbol(session, symbol)
        stored = empty_bars()

    # ---- validate the merged view -----------------------------------------
    merged = _merge(stored, fetched)
    result = validate_bars(
        symbol,
        merged,
        max_gap_trading_days=validation_cfg.max_gap_trading_days,
        allow_zero_volume=validation_cfg.allow_zero_volume,
        envelope_rtol=validation_cfg.envelope_rtol,
    )

    if result.warnings:
        log.warning(
            "%s ingested with warnings: %s", symbol,
            "; ".join(f"{w.code}: {w.detail}" for w in result.warnings),
        )
        report.warnings.append(result.as_dict())

    if not result.ok:
        log.error(
            "%s QUARANTINED: %s", symbol,
            "; ".join(f"{v.code}: {v.detail}" for v in result.violations),
        )
        report.quarantined.append(result.as_dict())
        return

    report.bars_upserted += upsert_bars(session, symbol, fetched, provider.name)
    report.ingested.append(symbol)


def _merge(stored: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    """The post-ingest view: stored history with the fetched window applied.

    Validation runs on this rather than on the fetched slice alone, so a gap
    that only appears at the seam between old and new data is still caught.
    """
    if stored.empty:
        return fetched
    if fetched.empty:
        return stored
    combined = pd.concat([stored, fetched])
    return combined[~combined.index.duplicated(keep="last")].sort_index()


def ingest_universe(
    session: Session,
    provider: PriceProvider,
    symbols: list[str],
    config: AppConfig,
    *,
    today: dt.date,
) -> IngestReport:
    """Ingest every symbol, isolating failures so one bad feed cannot stop the run."""
    report = IngestReport(requested=list(symbols))
    for symbol in symbols:
        try:
            ingest_symbol(session, provider, symbol, config, today=today, report=report)
        except Exception as exc:  # noqa: BLE001 — one symbol must not kill the run
            log.exception("unexpected failure ingesting %s", symbol)
            report.errors[symbol] = repr(exc)
            report.quarantined.append(
                {"symbol": symbol, "ok": False,
                 "violations": [{"code": "UNEXPECTED", "detail": repr(exc), "sample": []}]}
            )
    return report


def build_provider(config: AppConfig, settings) -> PriceProvider:
    """Instantiate the provider named in config.yaml."""
    name = config.data.provider.lower()
    if name == "yfinance":
        from .yfinance_provider import YFinanceProvider

        return YFinanceProvider()
    if name == "schwab":
        from .schwab_provider import build_schwab_provider

        return build_schwab_provider(settings)
    raise ValueError(f"unknown data provider {config.data.provider!r}")
