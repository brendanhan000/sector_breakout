"""API routes."""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.db.models import DataQuality, RunLog, RunStatus
from backend.settings import Settings

from . import queries as q
from .deps import get_app_settings, get_session
from .schemas import (
    BenchmarkView,
    HealthResponse,
    HistoryPoint,
    HistoryResponse,
    HorizonPoint,
    LeaderboardEntry,
    LeaderboardResponse,
    PlaneView,
    RRGResponse,
    RRGSeries,
    RRGTailPoint,
    RefreshResponse,
    RegimeResponse,
    RunResponse,
    SectorRow,
    SectorsResponse,
    SparkPoint,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

LOW_DISPERSION_MESSAGE = "Low dispersion — rotation signals unreliable."


# --------------------------------------------------------------------------- #
@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health(
    session: Session = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> HealthResponse:
    database_ok = True
    try:
        session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        log.exception("health check could not reach the database")
        database_ok = False

    if not database_ok:
        return HealthResponse(
            status="degraded", database=False, as_of=None,
            data_quality=DataQuality.STALE.value,
        )

    run = q.latest_completed_run(session)
    as_of, quality = q.resolve_envelope(session)

    if run is None:
        status = "empty"
    elif quality == DataQuality.OK.value:
        status = "ok"
    else:
        status = "degraded"

    return HealthResponse(
        status=status,
        database=True,
        latest_run_id=None if run is None else run.run_id,
        last_run_at=None if run is None else run.started_at,
        as_of=as_of,
        data_quality=quality,
    )


# --------------------------------------------------------------------------- #
@router.get("/regime", response_model=RegimeResponse, tags=["regime"])
def regime(
    session: Session = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> RegimeResponse:
    as_of, quality = q.resolve_envelope(session)
    config = settings.config
    threshold = config.regime.dispersion.low_threshold

    if as_of is None:
        raise HTTPException(status_code=503, detail="No completed run yet. POST /api/refresh.")

    row = q.regime_on(session, as_of)
    if row is None:
        raise HTTPException(status_code=503, detail=f"No regime row for {as_of}")

    spark = [
        SparkPoint(date=r.date, value=r.correlation)
        for r in q.correlation_series(session, as_of, config.regime.correlation.sparkline_bars)
    ]

    return RegimeResponse(
        as_of=as_of,
        data_quality=quality,
        dispersion=row.dispersion,
        dispersion_raw=row.dispersion_raw,
        dispersion_percentile=row.dispersion_percentile,
        low_dispersion=bool(row.low_dispersion),
        low_dispersion_threshold=threshold,
        correlation=row.correlation,
        correlation_sparkline=spark,
        benchmark=BenchmarkView(
            symbol=config.universe.benchmark,
            state=row.benchmark_state,
            bars_in_state=row.benchmark_bars_in_state,
        ),
        message=LOW_DISPERSION_MESSAGE if row.low_dispersion else None,
    )


# --------------------------------------------------------------------------- #
def _plane_view(plane: str, signal, state, close, horizons, state_horizon) -> PlaneView | None:
    if signal is None:
        return None

    term = []
    for h in horizons:
        at = signal.term.get(str(h)) or {}
        term.append(
            HorizonPoint(
                horizon=h,
                signal=at.get("signal"),
                c=at.get("c"),
                z_up=at.get("z_up"),
                z_dn=at.get("z_dn"),
                channel_max=at.get("max"),
                channel_min=at.get("min"),
            )
        )
    at_state = signal.term.get(str(state_horizon)) or {}

    return PlaneView(
        plane=plane,
        state=None if state is None else state.state,
        bars_in_state=None if state is None else state.bars_in_state,
        term=term,
        rvol=signal.rvol,
        narrow=bool(signal.narrow),
        breadth_spread=signal.breadth_spread,
        equal_weight_signal=signal.equal_weight_signal,
        z_up=at_state.get("z_up"),
        z_dn=at_state.get("z_dn"),
        close=close,
    )


@router.get("/sectors", response_model=SectorsResponse, tags=["sectors"])
def sectors(
    session: Session = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> SectorsResponse:
    as_of, quality = q.resolve_envelope(session)
    if as_of is None:
        raise HTTPException(status_code=503, detail="No completed run yet. POST /api/refresh.")

    config = settings.config
    horizons = list(config.engine.channel.horizons)
    state_horizon = config.engine.channel.state_horizon

    signals = q.signals_on(session, as_of)
    states = q.states_on(session, as_of)
    closes = q.closes_on(session, as_of)
    quarantined = q.quarantined_symbols(session)
    regime_row = q.regime_on(session, as_of)

    rows: list[SectorRow] = []
    for spec in settings.universe.sectors:
        symbol = spec.symbol
        abs_sig, rel_sig = signals.get((symbol, "absolute")), signals.get((symbol, "relative"))
        abs_state, rel_state = states.get((symbol, "absolute")), states.get((symbol, "relative"))

        rrg_points = q.rrg_tail(session, symbol, as_of, 1)

        rows.append(
            SectorRow(
                symbol=symbol,
                name=spec.name,
                equal_weight=spec.equal_weight,
                quarantined=symbol in quarantined,
                absolute=_plane_view(
                    "absolute", abs_sig, abs_state, closes.get(symbol), horizons, state_horizon
                ),
                relative=_plane_view(
                    "relative", rel_sig, rel_state,
                    None if rel_sig is None else rel_sig.residual_close,
                    horizons, state_horizon,
                ),
                beta=None if abs_sig is None else abs_sig.beta,
                disagreement=q.planes_disagree(abs_state, rel_state),
                quadrant=rrg_points[-1].quadrant if rrg_points else None,
            )
        )

    return SectorsResponse(
        as_of=as_of,
        data_quality=quality,
        state_horizon=state_horizon,
        horizons=horizons,
        low_dispersion=bool(regime_row.low_dispersion) if regime_row else False,
        sectors=rows,
    )


# --------------------------------------------------------------------------- #
@router.get("/sectors/{symbol}/history", response_model=HistoryResponse, tags=["sectors"])
def sector_history(
    symbol: str,
    days: int | None = Query(default=None, ge=1),
    plane: str = Query(default="absolute", pattern="^(absolute|relative)$"),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HistoryResponse:
    symbol = symbol.upper()
    config = settings.config
    try:
        spec = settings.universe.by_symbol(symbol)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"{symbol} is not a sector in this universe")

    as_of, quality = q.resolve_envelope(session)
    if as_of is None:
        raise HTTPException(status_code=503, detail="No completed run yet. POST /api/refresh.")

    days = min(days or config.api.default_history_days, config.api.max_history_days)

    signals, states, bars = q.history(session, symbol, plane, as_of, days)
    state_by_date = {s.date: s for s in states}

    series = [
        HistoryPoint(
            date=sig.date,
            close=None if sig.date not in bars else bars[sig.date].close,
            high=None if sig.date not in bars else bars[sig.date].high,
            low=None if sig.date not in bars else bars[sig.date].low,
            residual=sig.residual_close,
            beta=sig.beta,
            rvol=sig.rvol,
            state=None if sig.date not in state_by_date else state_by_date[sig.date].state,
            bars_in_state=(
                None if sig.date not in state_by_date else state_by_date[sig.date].bars_in_state
            ),
            entered=bool(sig.date in state_by_date and state_by_date[sig.date].entered),
            from_state=None if sig.date not in state_by_date else state_by_date[sig.date].from_state,
            term=sig.term,
        )
        for sig in signals
    ]

    transitions = [
        HistoryPoint(
            date=t.date, close=None, high=None, low=None, residual=None, beta=None, rvol=None,
            state=t.state, bars_in_state=t.bars_in_state, entered=True,
            from_state=t.from_state, term={},
        )
        for t in q.transition_history(session, symbol, plane, as_of)
    ]

    return HistoryResponse(
        as_of=as_of,
        data_quality=quality,
        symbol=symbol,
        name=spec.name,
        plane=plane,
        days=days,
        series=series,
        transitions=transitions,
    )


# --------------------------------------------------------------------------- #
@router.get("/rrg", response_model=RRGResponse, tags=["rrg"])
def rrg(
    session: Session = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> RRGResponse:
    as_of, quality = q.resolve_envelope(session)
    if as_of is None:
        raise HTTPException(status_code=503, detail="No completed run yet. POST /api/refresh.")

    config = settings.config
    tail_length = config.rrg.tail_length

    series: list[RRGSeries] = []
    for spec in settings.universe.sectors:
        points = q.rrg_tail(session, spec.symbol, as_of, tail_length)
        tail = [
            RRGTailPoint(
                date=p.date, rs_ratio=p.rs_ratio, rs_momentum=p.rs_momentum, quadrant=p.quadrant
            )
            for p in points
        ]
        latest = points[-1] if points else None
        series.append(
            RRGSeries(
                symbol=spec.symbol,
                name=spec.name,
                rs_ratio=None if latest is None else latest.rs_ratio,
                rs_momentum=None if latest is None else latest.rs_momentum,
                quadrant=None if latest is None else latest.quadrant,
                tail=tail,
            )
        )

    return RRGResponse(
        as_of=as_of,
        data_quality=quality,
        origin=config.rrg.origin,
        tail_length=tail_length,
        series=series,
    )


# --------------------------------------------------------------------------- #
@router.get("/leaderboard", response_model=LeaderboardResponse, tags=["sectors"])
def leaderboard(
    plane: str = Query(default="relative", pattern="^(absolute|relative)$"),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> LeaderboardResponse:
    as_of, quality = q.resolve_envelope(session)
    if as_of is None:
        raise HTTPException(status_code=503, detail="No completed run yet. POST /api/refresh.")

    config = settings.config
    horizon = config.engine.channel.state_horizon
    signals = q.signals_on(session, as_of)
    states = q.states_on(session, as_of)
    regime_row = q.regime_on(session, as_of)

    rows = []
    for spec in settings.universe.sectors:
        sig = signals.get((spec.symbol, plane))
        if sig is None:
            continue
        at = sig.term.get(str(horizon)) or {}
        state = states.get((spec.symbol, plane))
        rows.append(
            {
                "symbol": spec.symbol,
                "name": spec.name,
                "z_up": at.get("z_up"),
                "z_dn": at.get("z_dn"),
                "signal": at.get("signal"),
                "state": None if state is None else state.state,
                "bars_in_state": None if state is None else state.bars_in_state,
                "narrow": bool(sig.narrow),
                "disagreement": q.planes_disagree(
                    states.get((spec.symbol, "absolute")), states.get((spec.symbol, "relative"))
                ),
            }
        )

    # Sorted by extension, descending. z_up is ATR-normalised and therefore the
    # only quantity here that is genuinely comparable across sectors.
    rows.sort(key=lambda r: (r["z_up"] is None, -(r["z_up"] or 0.0)))

    return LeaderboardResponse(
        as_of=as_of,
        data_quality=quality,
        plane=plane,
        horizon=horizon,
        low_dispersion=bool(regime_row.low_dispersion) if regime_row else False,
        entries=[LeaderboardEntry(rank=i + 1, plane=plane, **r) for i, r in enumerate(rows)],
    )


# --------------------------------------------------------------------------- #
@router.post("/refresh", response_model=RefreshResponse, status_code=202, tags=["ops"])
def refresh(
    background: BackgroundTasks,
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> RefreshResponse:
    """Trigger an ingest + recompute. Returns immediately with a run_id.

    A full refresh takes longer than a sensible HTTP timeout, so the work runs
    in the background and progress is polled via GET /api/runs/{run_id}.
    """
    from backend.jobs.refresh import register_run, run_refresh

    factory = request.app.state.session_factory

    # Register the run synchronously so GET /api/runs/{run_id} works the instant
    # this response lands, rather than 404-ing until the background task starts.
    with factory() as registrar:
        run_id = register_run(registrar, trigger="api")
        registrar.commit()

    def _work() -> None:
        try:
            run_refresh(settings, session_factory=factory, trigger="api", run_id=run_id)
        except Exception:
            log.exception("background refresh %s failed", run_id)

    background.add_task(_work)

    return RefreshResponse(
        run_id=run_id, status=RunStatus.RUNNING.value,
        accepted_at=dt.datetime.now(dt.timezone.utc),
    )


@router.get("/runs/{run_id}", response_model=RunResponse, tags=["ops"])
def run_status(run_id: str, session: Session = Depends(get_session)) -> RunResponse:
    run = session.get(RunLog, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id}")
    return RunResponse(
        run_id=run.run_id,
        status=run.status,
        trigger=run.trigger,
        provider=run.provider,
        started_at=run.started_at,
        finished_at=run.finished_at,
        as_of=run.as_of,
        data_quality=run.data_quality,
        symbols_requested=run.symbols_requested,
        symbols_ingested=run.symbols_ingested,
        bars_upserted=run.bars_upserted,
        quarantined=run.quarantined or [],
        warnings=run.warnings or [],
        repulled=run.repulled or [],
        error=run.error,
    )


@router.get("/runs", response_model=list[RunResponse], tags=["ops"])
def recent_runs(
    limit: int = Query(default=20, ge=1, le=200), session: Session = Depends(get_session)
) -> list[RunResponse]:
    runs = (
        session.execute(select(RunLog).order_by(RunLog.started_at.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [run_status(r.run_id, session) for r in runs]
