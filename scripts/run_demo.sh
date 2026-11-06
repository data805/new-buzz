#!/usr/bin/env bash
# Local demo: separate database in data/demo, simulated phone network only (no real calls
# are possible), sign-in disabled. Listens on 127.0.0.1 only. Never expose this to the internet.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
D="$ROOT/data/demo"
mkdir -p "$D/recordings" "$D/uploads"
cd "$ROOT/backend"
source ../.venv/bin/activate
export DATABASE_URL="sqlite+aiosqlite:///$D/demo.db" DATA_DIR="$D" RECORDINGS_DIR="$D/recordings" UPLOADS_DIR="$D/uploads" \
  ADMIN_EMAIL=demo@example.com ADMIN_PASSWORD="$(openssl rand -base64 24)" \
  TELEPHONY_PROVIDER=simulator OUTBOUND_ENABLED=true AUTH_DISABLED=true
exec uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8080}"
