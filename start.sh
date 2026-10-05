#!/usr/bin/env bash
# Starts the PhotoGuard backend (port 3000) and frontend (port 5173).
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

command -v python3 >/dev/null || { echo "Python 3 is required: https://www.python.org/downloads/"; exit 1; }
command -v npm >/dev/null || { echo "Node.js 18+ is required: https://nodejs.org/"; exit 1; }

# ── Backend ──────────────────────────────────────────────────────────────────
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "Installing Python dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "Starting backend on http://localhost:3000 ..."
uvicorn api:app --port 3000 --reload &
BACKEND_PID=$!

# ── Frontend ─────────────────────────────────────────────────────────────────
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  echo "Installing Node dependencies..."
  npm install --no-audit --no-fund
fi

echo "Starting frontend on http://localhost:5173 ..."
npm run dev -- --port 5173 --strictPort &
FRONTEND_PID=$!
cd "$ROOT"

# ── Cleanup on Ctrl-C ────────────────────────────────────────────────────────
trap 'echo ""; echo "Shutting down..."; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0' INT TERM

echo ""
echo "  App:      http://localhost:5173"
echo "  API docs: http://localhost:3000/docs"
echo ""
echo "Press Ctrl-C to stop both servers."

wait
