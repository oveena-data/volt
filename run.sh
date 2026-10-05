#!/usr/bin/env bash
# VOLT dev launcher: local Postgres must be running (see README quick start).
set -euo pipefail
cd "$(dirname "$0")/backend"

if ! python3 -c "import starlette, asyncpg, argon2, pydantic" 2>/dev/null; then
  echo "Installing backend deps..."
  python3 -m pip install -r requirements.lock.txt
fi

export VOLT_ENV="${VOLT_ENV:-development}"
PORT="${PORT:-8099}"
echo "VOLT backend on http://127.0.0.1:${PORT} (env: ${VOLT_ENV})"
echo "Frontend: cd frontend && npm install && npm run dev"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
