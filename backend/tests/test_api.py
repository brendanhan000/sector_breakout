"""API contract tests against a real (SQLite) database seeded by a real run.

These go through the actual refresh job rather than hand-inserting rows, so the
payloads are exercised against data the engine really produced.
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.jobs.refresh as refresh_module
from backend.api.app import create_app
from backend.db.models import Base
from backend.settings import REPO_ROOT, Settings

#: Anchored to the most recent business day rather than a fixed historical date.
#: resolve_envelope() marks anything older than a working week as STALE — which
#: is correct behaviour — so a fixture pinned to 2024 would test the staleness
#: path instead of the happy path.
TODAY = (pd.Timestamp.today().normalize() - pd.offsets.BDay(0)).date()
N_BARS = 1400


def _frame(seed: int) -> pd.DataFrame:
    g = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(TODAY), periods=N_BARS)
    close = 100 * np.exp(np.cumsum(g.normal(0.0003, 0.011, N_BARS)))
    high = close * (1 + np.abs(g.normal(0, 0.004, N_BARS)))
    low = close * (1 - np.abs(g.normal(0, 0.004, N_BARS)))
    volume = np.random.default_rng(seed + 50_000).lognormal(15, 0.4, N_BARS)
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    )


class StubProvider:
    name = "stub"

    def __init__(self, symbols):
        self.frames = {s: _frame(i * 13 + 3) for i, s in enumerate(symbols)}

    def get_daily_bars(self, symbol, start, end):
        f = self.frames[symbol]
        return f.loc[(f.index >= pd.Timestamp(start)) & (f.index <= pd.Timestamp(end))]


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    settings = Settings(config_path=REPO_ROOT / "config.yaml")
    db_path = tmp_path_factory.mktemp("api") / "api.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    symbols = list(settings.universe.all_symbols)
    original = refresh_module.build_provider
    refresh_module.build_provider = lambda cfg, st: StubProvider(symbols)
    try:
        with factory() as session:
            refresh_module.run_refresh(settings, session, today=TODAY, trigger="test")
            session.commit()
    finally:
        refresh_module.build_provider = original

    return settings, factory


@asynccontextmanager
async def _no_lifespan(app):
    """Tests exercise the routes, not the scheduler."""
    yield


@pytest.fixture(scope="module")
def client(seeded):
    settings, factory = seeded
    app = create_app(settings=settings, session_factory=factory)
    # Do not start the scheduler for tests.
    app.router.lifespan_context = _no_lifespan
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def empty_client():
    settings = Settings(config_path=REPO_ROOT / "config.yaml")
    # StaticPool keeps every session on the SAME in-memory database. Without it
    # each connection gets its own blank one and the schema vanishes.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings=settings, session_factory=factory)
    app.router.lifespan_context = _no_lifespan
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["as_of"] == TODAY.isoformat()
    assert body["data_quality"] == "OK"


def test_health_on_an_empty_database(empty_client):
    """An empty database is a legitimate state, not a 500."""
    response = empty_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "empty"


def test_endpoints_503_before_the_first_run(empty_client):
    """Better to say 'no data' than to serve a partially computed day."""
    for path in ("/api/regime", "/api/sectors", "/api/rrg", "/api/leaderboard"):
        assert empty_client.get(path).status_code == 503


@pytest.mark.parametrize(
    "path", ["/api/regime", "/api/sectors", "/api/rrg", "/api/leaderboard"]
)
def test_every_payload_carries_as_of_and_data_quality(client, path):
    body = client.get(path).json()
    assert body["as_of"] == TODAY.isoformat()
    assert body["data_quality"] in {"OK", "WARMING_UP", "DEGRADED", "STALE"}


# --------------------------------------------------------------------------- #
def test_regime_payload(client):
    body = client.get("/api/regime").json()
    assert body["dispersion"] is not None
    assert 0.0 <= body["dispersion_percentile"] <= 100.0
    assert -1.0 <= body["correlation"] <= 1.0
    assert body["benchmark"]["symbol"] == "SPY"
    assert len(body["correlation_sparkline"]) > 0
    assert body["low_dispersion_threshold"] == 25.0
    # The message is present exactly when the relative plane should be grayed.
    assert (body["message"] is not None) == body["low_dispersion"]


def test_regime_message_matches_the_specified_copy(client):
    body = client.get("/api/regime").json()
    if body["low_dispersion"]:
        assert body["message"] == "Low dispersion — rotation signals unreliable."


# --------------------------------------------------------------------------- #
def test_sectors_returns_all_twelve_on_both_planes(client):
    body = client.get("/api/sectors").json()
    assert len(body["sectors"]) == 12
    for row in body["sectors"]:
        assert row["absolute"] is not None
        assert row["relative"] is not None
        assert isinstance(row["disagreement"], bool)


def test_sectors_expose_the_full_term_structure_not_a_composite(client):
    body = client.get("/api/sectors").json()
    assert body["horizons"] == [10, 20, 55]
    for row in body["sectors"]:
        for plane in ("absolute", "relative"):
            term = row[plane]["term"]
            assert [h["horizon"] for h in term] == [10, 20, 55]
            assert "composite" not in row[plane]


def test_sector_rows_carry_state_age_and_confirmation_context(client):
    body = client.get("/api/sectors").json()
    for row in body["sectors"]:
        view = row["absolute"]
        assert view["state"] in {
            "NEUTRAL", "PENDING_UP", "CONFIRMED_UP", "FAILED_UP",
            "PENDING_DOWN", "CONFIRMED_DOWN", "FAILED_DOWN",
        }
        assert view["bars_in_state"] >= 0
        assert isinstance(view["narrow"], bool)


def test_disagreement_flag_matches_the_underlying_states(client):
    """Compared by directional bias, not breakout family.

    FAILED_UP began as an upside breakout but reads bearish, so it opposes a
    CONFIRMED_UP on the other plane.
    """
    body = client.get("/api/sectors").json()
    bias = {
        "PENDING_UP": 1, "CONFIRMED_UP": 1, "FAILED_DOWN": 1,
        "PENDING_DOWN": -1, "CONFIRMED_DOWN": -1, "FAILED_UP": -1,
        "NEUTRAL": 0,
    }
    for row in body["sectors"]:
        a, r = row["absolute"]["state"], row["relative"]["state"]
        assert row["disagreement"] is (bias[a] * bias[r] < 0)


# --------------------------------------------------------------------------- #
def test_history_defaults_and_bounds(client):
    body = client.get("/api/sectors/XLK/history").json()
    assert body["symbol"] == "XLK"
    assert body["days"] == 250
    assert len(body["series"]) <= 250
    assert body["series"][0]["date"] < body["series"][-1]["date"]


def test_history_respects_the_days_parameter(client):
    body = client.get("/api/sectors/XLK/history?days=60").json()
    assert len(body["series"]) == 60


def test_history_is_capped_at_the_configured_maximum(client):
    body = client.get("/api/sectors/XLK/history?days=99999").json()
    assert body["days"] == 2520


def test_history_serves_the_relative_plane(client):
    body = client.get("/api/sectors/XLK/history?plane=relative&days=30").json()
    assert body["plane"] == "relative"
    assert any(p["residual"] is not None for p in body["series"])


def test_history_includes_transitions_for_the_drawer(client):
    body = client.get("/api/sectors/XLK/history?days=250").json()
    assert isinstance(body["transitions"], list)
    for t in body["transitions"]:
        assert t["entered"] is True


def test_history_rejects_an_unknown_symbol(client):
    assert client.get("/api/sectors/NOPE/history").status_code == 404


def test_history_rejects_an_invalid_plane(client):
    assert client.get("/api/sectors/XLK/history?plane=sideways").status_code == 422


# --------------------------------------------------------------------------- #
def test_rrg_returns_all_sectors_with_tails(client):
    body = client.get("/api/rrg").json()
    assert body["origin"] == 100.0
    assert body["tail_length"] == 10
    assert len(body["series"]) == 12
    for s in body["series"]:
        assert len(s["tail"]) == 10
        dates = [p["date"] for p in s["tail"]]
        assert dates == sorted(dates), "tails must be oldest-first to render direction"
        assert s["tail"][-1]["date"] == body["as_of"]


def test_rrg_quadrants_agree_with_the_coordinates(client):
    body = client.get("/api/rrg").json()
    for s in body["series"]:
        for p in s["tail"]:
            x, y, quadrant = p["rs_ratio"], p["rs_momentum"], p["quadrant"]
            if x > 100 and y > 100:
                assert quadrant == "LEADING"
            elif x > 100 and y <= 100:
                assert quadrant == "WEAKENING"
            elif x <= 100 and y > 100:
                assert quadrant == "IMPROVING"
            else:
                assert quadrant == "LAGGING"


# --------------------------------------------------------------------------- #
def test_leaderboard_is_sorted_by_extension_descending(client):
    body = client.get("/api/leaderboard").json()
    assert body["plane"] == "relative"
    assert len(body["entries"]) == 12

    scores = [e["z_up"] for e in body["entries"] if e["z_up"] is not None]
    assert scores == sorted(scores, reverse=True)
    assert [e["rank"] for e in body["entries"]] == list(range(1, 13))


def test_leaderboard_can_serve_the_absolute_plane(client):
    body = client.get("/api/leaderboard?plane=absolute").json()
    assert body["plane"] == "absolute"
    assert len(body["entries"]) == 12


def test_leaderboard_rejects_an_unknown_plane(client):
    assert client.get("/api/leaderboard?plane=nonsense").status_code == 422


# --------------------------------------------------------------------------- #
def test_refresh_returns_a_pollable_run_id(client, monkeypatch):
    """The run must be pollable the instant the 202 lands."""
    monkeypatch.setattr(
        refresh_module, "build_provider",
        lambda cfg, st: StubProvider(list(client.app.state.settings.universe.all_symbols)),
    )
    response = client.post("/api/refresh")
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    status = client.get(f"/api/runs/{run_id}")
    assert status.status_code == 200
    assert status.json()["run_id"] == run_id


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/does-not-exist").status_code == 404


def test_recent_runs_lists_history(client):
    body = client.get("/api/runs?limit=5").json()
    assert len(body) >= 1
    assert all("status" in r for r in body)


def test_openapi_schema_is_generated(client):
    schema = client.get("/openapi.json").json()
    for path in (
        "/api/health", "/api/regime", "/api/sectors", "/api/sectors/{symbol}/history",
        "/api/rrg", "/api/leaderboard", "/api/refresh", "/api/runs/{run_id}",
    ):
        assert path in schema["paths"], f"{path} missing from the OpenAPI schema"
