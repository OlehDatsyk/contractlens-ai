#!/usr/bin/env bash
# ContractLens AI - macOS launcher. Double-click in Finder (first time: right-click > Open).
# Safe to run repeatedly. Never deletes files or resets the database.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV_PY="$BACKEND/.venv/bin/python"

ok()   { printf "\033[32m[OK]\033[0m %s\n" "$1"; }
warn() { printf "\033[33m[WARN]\033[0m %s\n" "$1"; }
fail() { printf "\033[31m[ERROR]\033[0m %s\n" "$1"; echo "Fix the problem above and run the launcher again."; read -r -p "Press Enter to close..."; exit 1; }

echo
echo "=============================================="
echo "  ContractLens AI - starting local environment"
echo "=============================================="
echo

# 1. Required software
PY=""
for cand in python3.13 python3.12 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' 2>/dev/null; then PY="$cand"; break; fi
done
[ -n "$PY" ] || fail "Python 3.12 or newer is required. Install it from https://www.python.org/downloads/ or with 'brew install python@3.12'."
ok "Python found: $($PY --version)"
command -v node >/dev/null 2>&1 || fail "Node.js 20 or newer is required. Install it from https://nodejs.org/ or with 'brew install node'."
command -v npm >/dev/null 2>&1 || fail "npm was not found. Reinstall Node.js."
ok "Node.js $(node --version)"
HAVE_DOCKER=0
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then HAVE_DOCKER=1; ok "Docker is running"; else warn "Docker is not running - start Docker Desktop to use the bundled PostgreSQL database."; fi

# 2. Backend dependencies
if [ ! -x "$VENV_PY" ]; then echo "[..] Creating Python virtual environment"; "$PY" -m venv "$BACKEND/.venv" || fail "Could not create the virtual environment."; fi
echo "[..] Installing / updating backend dependencies"
"$VENV_PY" -m pip install --disable-pip-version-check -q -r "$BACKEND/requirements.txt" || fail "Backend dependency installation failed. Check your internet connection."
ok "Backend dependencies installed"

# 3. Frontend dependencies
if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "[..] Installing frontend dependencies (first run can take a few minutes)"
  (cd "$FRONTEND" && npm ci --no-audit --no-fund) || fail "Frontend dependency installation failed."
fi
ok "Frontend dependencies installed"

# 4. Configuration (created once, never overwritten)
if [ ! -f "$BACKEND/.env" ]; then
  echo "[..] Creating backend/.env with freshly generated secrets"
  "$VENV_PY" - "$ROOT" <<'PYEOF' || fail "Could not create backend/.env"
import pathlib, re, secrets, sys
from cryptography.fernet import Fernet
root = pathlib.Path(sys.argv[1])
src = (root / ".env.example").read_text(encoding="utf-8")
src = re.sub(r"(?m)^JWT_SECRET_KEY=.*$", "JWT_SECRET_KEY=" + secrets.token_urlsafe(48), src)
src = re.sub(r"(?m)^ENCRYPTION_KEY=.*$", "ENCRYPTION_KEY=" + Fernet.generate_key().decode(), src)
(root / "backend" / ".env").write_text(src, encoding="utf-8")
PYEOF
  chmod 600 "$BACKEND/.env"
  ok "backend/.env created - AI is off until you add a provider key there"
else
  ok "Using existing backend/.env"
fi

# 5. Database
if [ "$HAVE_DOCKER" = "1" ]; then
  echo "[..] Starting PostgreSQL container"
  docker compose up -d db || fail "Could not start the database container."
  for i in $(seq 1 40); do
    if docker compose exec -T db pg_isready -U contractlens -d contractlens >/dev/null 2>&1; then break; fi
    [ "$i" = "40" ] && fail "The database did not become ready in time."
    sleep 1
  done
  ok "PostgreSQL is ready on 127.0.0.1:5437"
else
  warn "Continuing without Docker: DATABASE_URL in backend/.env must point to a running PostgreSQL server."
fi
echo "[..] Applying database migrations"
(cd "$BACKEND" && "$VENV_PY" -m alembic upgrade head) || fail "Database migrations failed - check DATABASE_URL in backend/.env and that PostgreSQL is running."
ok "Database is up to date"

# 6. Start servers (stopped together with Ctrl+C)
lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1 && warn "Port 8000 is already in use - an API server may already be running."
lsof -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1 && warn "Port 5173 is already in use - the web app may already be running."
echo "[..] Starting API on http://127.0.0.1:8000 and web app on http://localhost:5173"
(cd "$BACKEND" && "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
API_PID=$!
(cd "$FRONTEND" && npm run dev) &
WEB_PID=$!
trap 'echo; echo "Stopping..."; kill $API_PID $WEB_PID 2>/dev/null || true' INT TERM EXIT
sleep 6
open "http://localhost:5173" >/dev/null 2>&1 || true
echo
echo "ContractLens AI is running. Web app: http://localhost:5173  API docs: http://127.0.0.1:8000/api/docs"
echo "Press Ctrl+C in this window to stop."
wait
