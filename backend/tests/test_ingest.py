"""Ingest against a real (SQLite) database with a stub provider.

Covers the properties that matter operationally: idempotence, quarantine
isolation, and the split/adjustment re-pull.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.data.ingest import (
    IngestReport,
    ingest_symbol,
    ingest_universe,
    load_bars,
    upsert_bars,
)
from backend.data.provider import ProviderError, empty_bars
from backend.db.models import Bar, Base
from backend.settings import AppConfig, load_config, REPO_ROOT

TODAY = dt.date(2024, 6, 28)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


@pytest.fixture
def config() -> AppConfig:
    return load_config(REPO_ROOT / "config.yaml")


def bars_frame(n: int = 120, end: dt.date = TODAY, base: float = 100.0) -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    close = base + np.arange(n, dtype="float64") * 0.05
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.full(n, 1e6),
        },
        index=idx,
    )


class StubProvider:
    """Serves a preset frame, records calls, and can be made to fail."""

    name = "stub"

    def __init__(self, frames: dict[str, pd.DataFrame] | None = None, fail: set[str] | None = None):
        self.frames = frames or {}
        self.fail = fail or set()
        self.calls: list[tuple[str, dt.date, dt.date]] = []

    def get_daily_bars(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if symbol in self.fail:
            raise ProviderError(f"synthetic failure for {symbol}")
        frame = self.frames.get(symbol)
        if frame is None:
            return empty_bars()
        return frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]


def count_bars(session, symbol: str) -> int:
    return session.execute(
        select(func.count()).select_from(Bar).where(Bar.symbol == symbol)
    ).scalar_one()


# --------------------------------------------------------------------------- #
def test_backfill_writes_every_bar(session, config):
    provider = StubProvider({"XLK": bars_frame(120)})
    report = IngestReport()
    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=report)
    session.commit()

    assert report.ingested == ["XLK"]
    assert report.quarantined == []
    assert count_bars(session, "XLK") == 120


def test_ingest_is_idempotent(session, config):
    """Running the same day twice must not duplicate rows.

    The daily job WILL be re-run by hand. A system that double-counts on a retry
    is a system nobody can safely retry.
    """
    provider = StubProvider({"XLK": bars_frame(120)})
    for _ in range(3):
        report = IngestReport()
        ingest_symbol(session, provider, "XLK", config, today=TODAY, report=report)
        session.commit()

    assert count_bars(session, "XLK") == 120


def test_upsert_updates_in_place(session, config):
    frame = bars_frame(10)
    upsert_bars(session, "XLK", frame, "stub")
    session.commit()

    revised = frame.copy()
    revised["close"] = 999.0
    upsert_bars(session, "XLK", revised, "stub")
    session.commit()

    assert count_bars(session, "XLK") == 10
    assert load_bars(session, "XLK")["close"].iloc[-1] == 999.0


def test_incremental_update_appends_only_new_bars(session, config):
    full = bars_frame(120)
    provider = StubProvider({"XLK": full.iloc[:100]})

    report = IngestReport()
    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=report)
    session.commit()
    assert count_bars(session, "XLK") == 100

    provider.frames["XLK"] = full
    report2 = IngestReport()
    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=report2)
    session.commit()

    assert count_bars(session, "XLK") == 120
    # The second call must not have re-requested the whole backfill window.
    _, second_start, _ = provider.calls[-1]
    assert second_start > provider.calls[0][1]


def test_round_trip_preserves_values(session, config):
    frame = bars_frame(30)
    upsert_bars(session, "XLK", frame, "stub")
    session.commit()

    restored = load_bars(session, "XLK")
    assert list(restored.columns) == ["open", "high", "low", "close", "volume"]
    assert np.allclose(restored["close"].to_numpy(), frame["close"].to_numpy())
    assert restored.index.equals(pd.DatetimeIndex(frame.index))


# --------------------------------------------------------------------------- #
# Quarantine
# --------------------------------------------------------------------------- #
def test_malformed_symbol_is_quarantined_and_not_written(session, config):
    """The headline requirement: bad data never reaches the bars table."""
    bad = bars_frame(120)
    bad.iloc[50, bad.columns.get_loc("high")] = 0.01   # high < low

    provider = StubProvider({"XLK": bad})
    report = IngestReport()
    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=report)
    session.commit()

    assert report.ingested == []
    assert report.quarantined_symbols == ("XLK",)
    assert count_bars(session, "XLK") == 0


def test_quarantine_does_not_affect_other_symbols(session, config):
    bad = bars_frame(120)
    bad.iloc[10, bad.columns.get_loc("close")] = np.nan

    provider = StubProvider({"XLK": bad, "XLU": bars_frame(120), "SPY": bars_frame(120)})
    report = ingest_universe(session, provider, ["XLK", "XLU", "SPY"], config, today=TODAY)
    session.commit()

    assert set(report.ingested) == {"XLU", "SPY"}
    assert report.quarantined_symbols == ("XLK",)
    assert count_bars(session, "XLU") == 120
    assert count_bars(session, "XLK") == 0


def test_provider_failure_is_quarantined_not_raised(session, config):
    """One broken feed must not abort the whole run."""
    provider = StubProvider({"XLU": bars_frame(120)}, fail={"XLK"})
    report = ingest_universe(session, provider, ["XLK", "XLU"], config, today=TODAY)
    session.commit()

    assert report.ingested == ["XLU"]
    assert report.quarantined_symbols == ("XLK",)
    assert "XLK" in report.errors


def test_symbol_with_no_data_is_quarantined(session, config):
    provider = StubProvider({})
    report = ingest_universe(session, provider, ["NOPE"], config, today=TODAY)
    assert report.quarantined_symbols == ("NOPE",)
    assert report.ingested == []


def test_quarantine_preserves_previously_good_history(session, config):
    """A bad feed today must not destroy yesterday's valid bars."""
    provider = StubProvider({"XLK": bars_frame(120)})
    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=IngestReport())
    session.commit()
    assert count_bars(session, "XLK") == 120

    broken = bars_frame(120)
    broken.iloc[-1, broken.columns.get_loc("volume")] = -1.0
    provider.frames["XLK"] = broken

    report = IngestReport()
    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=report)
    session.commit()

    assert report.quarantined_symbols == ("XLK",)
    assert count_bars(session, "XLK") == 120   # untouched


# --------------------------------------------------------------------------- #
# Adjustment factor changes
# --------------------------------------------------------------------------- #
def test_split_triggers_a_full_repull(session, config):
    """A restated history must be replaced wholesale, never patched at the tail.

    Patching would leave the series unadjusted before the split and adjusted
    after it — a discontinuity that reads as a genuine breakout.
    """
    original = bars_frame(200)
    provider = StubProvider({"XLK": original})

    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=IngestReport())
    session.commit()
    assert load_bars(session, "XLK")["close"].iloc[0] == pytest.approx(original["close"].iloc[0])

    # 2-for-1 split: the provider now reports every historical close halved.
    split = original.copy()
    split[["open", "high", "low", "close"]] /= 2.0
    provider.frames["XLK"] = split

    report = IngestReport()
    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=report)
    session.commit()

    assert report.repulled == ["XLK"]
    stored = load_bars(session, "XLK")
    assert len(stored) == 200
    # Every bar is on the new adjustment basis — no mixed history.
    assert np.allclose(stored["close"].to_numpy(), split["close"].to_numpy())


def test_no_repull_when_history_is_unchanged(session, config):
    provider = StubProvider({"XLK": bars_frame(200)})
    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=IngestReport())
    session.commit()

    report = IngestReport()
    ingest_symbol(session, provider, "XLK", config, today=TODAY, report=report)
    session.commit()
    assert report.repulled == []


def test_report_counts_are_consistent(session, config):
    provider = StubProvider({"XLK": bars_frame(50), "XLU": bars_frame(50)}, fail={"XLE"})
    report = ingest_universe(session, provider, ["XLK", "XLU", "XLE"], config, today=TODAY)
    assert report.requested == ["XLK", "XLU", "XLE"]
    assert report.bars_upserted == 100
    assert len(report.ingested) + len(report.quarantined) == 3
