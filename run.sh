#!/usr/bin/env bash
# VOLT dev launcher. Runs the backend (which also serves the frontend).
set -euo pipefail
cd "$(dirname "$0")/backend"

if ! python -c "import starlette, uvicorn" 2>/dev/null; then
  echo "Installing backend deps..."
  python -m pip install -r requirements.txt
fi

PORT="${PORT:-8099}"
echo "VOLT running at http://127.0.0.1:${PORT}  (provider: ${VOLT_PROVIDER:-mock})"
exec python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
