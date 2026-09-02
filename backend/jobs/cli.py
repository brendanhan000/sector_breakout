"""Command line entry points.

    python -m backend.jobs.cli refresh          run one ingest + recompute
    python -m backend.jobs.cli status           show the latest run
    python -m backend.jobs.cli serve            run the API with the scheduler
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from backend.settings import get_settings


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_refresh(args: argparse.Namespace) -> int:
    from backend.db.session import get_sessionmaker
    from backend.jobs.refresh import run_refresh

    settings = get_settings()
    factory = get_sessionmaker(settings.database_url)

    with factory() as session:
        try:
            run_id = run_refresh(settings, session, trigger=args.trigger)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(f"refresh failed: {exc}", file=sys.stderr)
            return 1

    print(run_id)
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    from sqlalchemy import select

    from backend.db.models import RunLog
    from backend.db.session import get_sessionmaker

    settings = get_settings()
    factory = get_sessionmaker(settings.database_url)

    with factory() as session:
        run = session.execute(
            select(RunLog).order_by(RunLog.started_at.desc()).limit(1)
        ).scalar_one_or_none()

        if run is None:
            print("no runs recorded")
            return 1

        print(
            json.dumps(
                {
                    "run_id": run.run_id,
                    "status": run.status,
                    "as_of": None if run.as_of is None else run.as_of.isoformat(),
                    "data_quality": run.data_quality,
                    "symbols": f"{run.symbols_ingested}/{run.symbols_requested}",
                    "bars_upserted": run.bars_upserted,
                    "quarantined": [q.get("symbol") for q in (run.quarantined or [])],
                    "warnings": [w.get("symbol") for w in (run.warnings or [])],
                    "repulled": run.repulled or [],
                    "error": run.error,
                },
                indent=2,
            )
        )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "backend.api.app:app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.jobs.cli")
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    refresh = sub.add_parser("refresh", help="ingest and recompute once")
    refresh.add_argument("--trigger", default="cli")
    refresh.set_defaults(func=cmd_refresh)

    status = sub.add_parser("status", help="show the latest run")
    status.set_defaults(func=cmd_status)

    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    _configure_logging(args.log_level or get_settings().log_level)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
