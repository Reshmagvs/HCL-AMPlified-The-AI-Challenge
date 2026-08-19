#!/usr/bin/env bash
# ===========================================================================
#  Lodestar - one-command bootstrap and run (macOS / Linux)
#
#    ./run.sh                normal start
#    ./run.sh --reset        wipe venv, node_modules and the database first
#    ./run.sh --backend      API only
#    ./run.sh --no-browser   do not open a browser
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

DO_RESET=0
BACKEND_ONLY=0
OPEN_BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --reset)      DO_RESET=1 ;;
    --backend)    BACKEND_ONLY=1 ;;
    --no-browser) OPEN_BROWSER=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

printf '\n  LODESTAR\n  Learning is a dependency graph, not a search result.\n  ---------------------------------------------------\n\n'

# --- 1. Locate Python --------------------------------------------------------
PY=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "[X] Python 3.11+ not found. Install it and re-run." >&2
  exit 1
fi
echo "[1/7] Python $("$PY" -c 'import sys;print(sys.version.split()[0])') via $PY"

# --- 2. Reset ----------------------------------------------------------------
if [ "$DO_RESET" = "1" ]; then
  echo "[--] Reset requested: removing venv, node_modules and database"
  rm -rf backend/.venv frontend/node_modules backend/.install_hash
  rm -f lodestar.db lodestar.db-wal lodestar.db-shm
fi

# --- 3. Virtual environment --------------------------------------------------
VPY="backend/.venv/bin/python"
if [ ! -x "$VPY" ]; then
  echo "[2/7] Creating virtual environment"
  "$PY" -m venv backend/.venv
else
  echo "[2/7] Virtual environment present"
fi

# --- 4. Backend deps, only when requirements.txt changed ---------------------
REQ_HASH="$("$PY" -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('backend/requirements.txt').read_bytes()).hexdigest()[:16])")"
OLD_HASH="$(cat backend/.install_hash 2>/dev/null || true)"
if [ "$REQ_HASH" != "$OLD_HASH" ]; then
  echo "[3/7] Installing backend dependencies (this happens once)"
  "$VPY" -m pip install --quiet --upgrade pip
  "$VPY" -m pip install --quiet -r backend/requirements.txt
  echo "$REQ_HASH" > backend/.install_hash
else
  echo "[3/7] Backend dependencies up to date"
fi

# --- 5. Environment file -----------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[4/7] Created .env from .env.example (mock mode, no API key needed)"
else
  echo "[4/7] Using existing .env"
fi

# --- 6. Seed -----------------------------------------------------------------
echo "[5/7] Preparing database"
(cd backend && ../"$VPY" -m scripts.seed)

# --- 7. API ------------------------------------------------------------------
echo "[6/7] Starting API on http://127.0.0.1:8000"
(cd backend && exec ../"$VPY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) &
API_PID=$!

cleanup() { kill "$API_PID" ${WEB_PID:-} 2>/dev/null || true; }
trap cleanup EXIT INT TERM

for _ in $(seq 1 60); do
  if "$VPY" -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)" 2>/dev/null; then
    echo "      API is healthy"; break
  fi
  sleep 1
done

if [ "$BACKEND_ONLY" = "1" ]; then
  echo; echo "  API ready: http://127.0.0.1:8000/docs"; echo "  Ctrl-C to stop."
  wait "$API_PID"; exit 0
fi

# --- 8. Frontend -------------------------------------------------------------
if ! command -v node >/dev/null 2>&1; then
  echo "[!] Node.js not found - running backend only. Install Node LTS and re-run."
  wait "$API_PID"; exit 0
fi
if [ ! -d frontend/node_modules ]; then
  echo "[7/7] Installing frontend dependencies (this happens once)"
  (cd frontend && npm install --no-fund --no-audit)
else
  echo "[7/7] Frontend dependencies up to date"
fi

echo "      Starting web app on http://localhost:5173"
(cd frontend && exec npm run dev) &
WEB_PID=$!

if [ "$OPEN_BROWSER" = "1" ]; then
  sleep 4
  (command -v open >/dev/null && open http://localhost:5173) ||
  (command -v xdg-open >/dev/null && xdg-open http://localhost:5173) || true
fi

printf '\n  Web:  http://localhost:5173\n  API:  http://127.0.0.1:8000/docs\n  Ctrl-C to stop.\n\n'
wait
