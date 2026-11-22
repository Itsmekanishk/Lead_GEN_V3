#!/usr/bin/env bash
# Start LocalLead directly on this machine (no Docker):
#   backend API + Celery worker + Next.js web UI.
#
#   ./run.sh              # start everything
#   API_PORT=8001 ./run.sh
#
# Ctrl+C stops all three.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
API_PORT="${API_PORT:-8000}"
PYTHON="$BACKEND/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "Missing backend virtualenv: $PYTHON" >&2
  echo "Create it with:" >&2
  echo "  python3 -m venv backend/.venv" >&2
  echo "  backend/.venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi

if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "Missing frontend dependencies. Run:" >&2
  echo "  (cd frontend && npm install)" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm was not found on PATH." >&2
  exit 1
fi

pids=()
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "Stopping LocalLead..."
  for pid in ${pids[@]+"${pids[@]}"}; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in ${pids[@]+"${pids[@]}"}; do
    wait "$pid" 2>/dev/null || true
  done
  echo "Stopped."
  exit 0
}
trap cleanup INT TERM EXIT

echo "Starting API on http://127.0.0.1:${API_PORT} ..."
( cd "$BACKEND" && exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" ) &
pids+=($!)

# Without a worker, searches stay in "queued" forever (the API only runs them
# in-process as a fallback when no worker answers a control ping).
echo "Starting Celery worker ..."
( cd "$BACKEND" && exec "$PYTHON" -m celery -A app.workers.celery_app.celery_app worker \
    --loglevel=INFO --concurrency=1 ) &
pids+=($!)

# Next.js proxies /backend/* to the API; point that rewrite at whatever port
# uvicorn was given, otherwise API_PORT would only move half the stack.
echo "Starting web UI (proxying /backend/* to 127.0.0.1:${API_PORT}) ..."
( cd "$FRONTEND" && exec env INTERNAL_API_URL="http://127.0.0.1:${API_PORT}" npm run dev ) &
pids+=($!)

cat <<EOF

LocalLead is running:
  Web:  http://localhost:3000
  API:  http://127.0.0.1:${API_PORT}/api/docs

Press Ctrl+C to stop everything.

EOF

# Exit (and stop the rest) as soon as one service dies, so failures surface.
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "A service exited — shutting down the rest." >&2
      exit 1
    fi
  done
  sleep 2
done
