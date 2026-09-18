#!/usr/bin/env bash
# One command: setup (first run only), refresh data, start API + UI, open browser.
#   ./run.sh          refresh data, then serve
#   ./run.sh fast     skip the refresh
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || { python3 -m venv .venv; .venv/bin/python -m pip install -q -e ".[dev]"; }
[ -f .env ] || echo 'SECTOR_DATABASE_URL=sqlite:///./sector_breakout.db' > .env
[ -d frontend/node_modules ] || (cd frontend && npm install)

.venv/bin/alembic upgrade head
[ "${1:-}" = fast ] || { .venv/bin/sector refresh; .venv/bin/sector status; }

trap 'trap - EXIT INT TERM; kill 0' EXIT INT TERM   # Ctrl-C stops both servers
.venv/bin/uvicorn backend.api.app:app --factory --port 8000 &
(cd frontend && npm run dev) &
sleep 3 && open http://localhost:5173
wait
