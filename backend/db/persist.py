"""Write engine output to the database.

The whole computed history is upserted on every run, not just the newest bar.
That costs a couple of seconds for 23 symbols and buys an important property:
after a split forces a full re-pull, EVERY historical signal changes, and a
tail-only write would leave the database holding signals computed from an
adjustment basis that no longer exists.

Everything is keyed on its natural composite key, so a re-run overwrites rather
than duplicating and a half-finished run can simply be run again.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.engine.pipeline import ABSOLUTE, RELATIVE, PipelineResult
from backend.engine.state import State

from .models import Base, RRGPoint, Regime, SectorState, Signal

log = logging.getLogger(__name__)

CHUNK_SIZE = 2_000


def _clean(value: Any) -> Any:
    """NaN/inf -> None. The database should hold nulls, not sentinel floats."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.date()
    return value


def _upsert(session: Session, model: type[Base], rows: Sequence[dict], keys: list[str]) -> int:
    if not rows:
        return 0

    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    if dialect not in ("postgresql", "sqlite"):  # pragma: no cover
        raise RuntimeError(f"upsert is not implemented for dialect {dialect!r}")

    updatable = [c.name for c in model.__table__.columns if c.name not in keys]

    written = 0
    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start : start + CHUNK_SIZE]
        stmt = insert(model).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=keys,
            set_={col: getattr(stmt.excluded, col) for col in updatable},
        )
        session.execute(stmt)
        written += len(chunk)
    return written


def persist_pipeline(session: Session, result: PipelineResult, run_id: str) -> dict[str, int]:
    """Write signals, states, RRG points and the regime series. Returns row counts."""
    signal_rows: list[dict] = []
    state_rows: list[dict] = []
    rrg_rows: list[dict] = []

    for symbol, sector in result.sectors.items():
        for plane_name, plane in ((ABSOLUTE, sector.absolute), (RELATIVE, sector.relative)):
            frame = plane.frame
            horizons = _horizons_in(frame)

            beta = sector.beta.reindex(frame.index)
            residual = sector.residual.reindex(frame.index)
            equal_signal = sector.breadth["equal_signal"].reindex(frame.index)

            for date, row in frame.iterrows():
                term = {
                    str(h): {
                        "c": _clean(row.get(f"c_{h}")),
                        "signal": _clean(row.get(f"signal_{h}")),
                        "max": _clean(row.get(f"max_{h}")),
                        "min": _clean(row.get(f"min_{h}")),
                        "z_up": _clean(row.get(f"z_up_{h}")),
                        "z_dn": _clean(row.get(f"z_dn_{h}")),
                    }
                    for h in horizons
                }
                signal_rows.append(
                    {
                        "symbol": symbol,
                        "date": _clean(date),
                        "plane": plane_name,
                        "run_id": run_id,
                        "term": term,
                        "rvol": _clean(row.get("rvol")),
                        "beta": _clean(beta.get(date)),
                        "residual_close": _clean(residual.get(date)),
                        "breadth_spread": _clean(row.get("breadth_spread")),
                        "equal_weight_signal": _clean(equal_signal.get(date)),
                        "narrow": bool(_clean(row.get("narrow")) or False),
                    }
                )

                state = row.get("state")
                if state is None:
                    continue
                state_rows.append(
                    {
                        "symbol": symbol,
                        "date": _clean(date),
                        "plane": plane_name,
                        "run_id": run_id,
                        "state": str(State(state)),
                        "bars_in_state": int(row["bars_in_state"]),
                        "entered": bool(row.get("entered", False)),
                        "from_state": None,
                        "consec_above": int(row.get("consec_above", 0)),
                        "consec_below": int(row.get("consec_below", 0)),
                    }
                )

        for date, row in sector.rrg.iterrows():
            rrg_rows.append(
                {
                    "symbol": symbol,
                    "date": _clean(date),
                    "run_id": run_id,
                    "rs": _clean(row.get("rs")),
                    "rs_ratio": _clean(row.get("rs_ratio")),
                    "rs_momentum": _clean(row.get("rs_momentum")),
                    "quadrant": None if row.get("quadrant") is None else str(row["quadrant"]),
                }
            )

    _fill_from_states(state_rows)

    benchmark = result.benchmark_state
    regime_rows = []
    for date, row in result.regime.iterrows():
        bench = benchmark.loc[date] if date in benchmark.index else None
        bench_state = None if bench is None else bench.get("state")
        regime_rows.append(
            {
                "date": _clean(date),
                "run_id": run_id,
                "dispersion_raw": _clean(row.get("dispersion_raw")),
                "dispersion": _clean(row.get("dispersion")),
                "dispersion_percentile": _clean(row.get("dispersion_percentile")),
                "correlation": _clean(row.get("correlation")),
                "low_dispersion": bool(_clean(row.get("low_dispersion")) or False),
                "benchmark_state": None if bench_state is None else str(State(bench_state)),
                "benchmark_bars_in_state": (
                    None
                    if bench is None or pd.isna(bench.get("bars_in_state"))
                    else int(bench["bars_in_state"])
                ),
            }
        )

    counts = {
        "signals": _upsert(session, Signal, signal_rows, ["symbol", "date", "plane"]),
        "states": _upsert(session, SectorState, state_rows, ["symbol", "date", "plane"]),
        "rrg": _upsert(session, RRGPoint, rrg_rows, ["symbol", "date"]),
        "regime": _upsert(session, Regime, regime_rows, ["date"]),
    }
    log.info("persisted %s", counts)
    return counts


def _fill_from_states(rows: list[dict]) -> None:
    """Populate ``from_state`` on transition rows.

    Stored on the row rather than derived at read time so the detail drawer can
    render a transition history — including past FAILED_* events — with a single
    indexed query instead of walking the whole series.
    """
    previous: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (row["symbol"], row["plane"])
        if row["entered"]:
            row["from_state"] = previous.get(key)
        previous[key] = row["state"]


def _horizons_in(frame: pd.DataFrame) -> list[int]:
    return sorted(
        int(col.split("_", 1)[1]) for col in frame.columns if col.startswith("signal_")
    )


def purge_run(session: Session, run_id: str) -> None:
    """Remove everything a given run wrote. Used to roll back a bad run."""
    for model in (Signal, SectorState, RRGPoint, Regime):
        session.execute(delete(model).where(model.run_id == run_id))
