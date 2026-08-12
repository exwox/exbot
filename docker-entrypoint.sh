#!/usr/bin/env bash
set -Eeuo pipefail

cd /app
mkdir -p /app/data /app/logs /app/backups

log() { printf '[XBOT-DOCKER] %s\n' "$*"; }
fail() { printf '[XBOT-DOCKER] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -n "${ENCRYPTION_KEY:-}" ]] || fail "ENCRYPTION_KEY belum di-set. Isi file .env production di VPS."
[[ "${#ENCRYPTION_KEY}" -ge 16 ]] || fail "ENCRYPTION_KEY minimal 16 byte; gunakan secret acak 32 byte atau lebih."

DB_FILE="${DATABASE_PATH:-/app/data/dca_bot.db}"
export DATABASE_PATH="$DB_FILE"
export DB_PATH="${DB_PATH:-$DB_FILE}"
export DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
export DASHBOARD_PORT="${DASHBOARD_PORT:-5000}"
export PORT="${PORT:-5000}"

DB_DIR="$(dirname -- "$DB_FILE")"
[[ -d "$DB_DIR" && -w "$DB_DIR" ]] || fail "Direktori database tidak dapat ditulis: $DB_DIR"

PYTHON_PID=""
NODE_PID=""

shutdown() {
    log "Menghentikan XBot..."
    if [[ -n "${NODE_PID:-}" ]] && kill -0 "$NODE_PID" 2>/dev/null; then
        kill -TERM "$NODE_PID" 2>/dev/null || true
    fi
    if [[ -n "${PYTHON_PID:-}" ]] && kill -0 "$PYTHON_PID" 2>/dev/null; then
        kill -TERM "$PYTHON_PID" 2>/dev/null || true
    fi
    wait "${NODE_PID:-}" 2>/dev/null || true
    wait "${PYTHON_PID:-}" 2>/dev/null || true
}
trap shutdown TERM INT EXIT

if [[ ! -f "$DB_FILE" ]]; then
    log "Database belum ada. Menjalankan initial setup..."
    python app.py --setup
fi

log "Memverifikasi administrator..."
node scripts/ensure_admin.js || fail "Bootstrap administrator gagal. Periksa error Node/admin tepat di atas."

log "Memulai Python Bot Manager..."
python app.py --no-dashboard &
PYTHON_PID=$!

sleep 3
if ! kill -0 "$PYTHON_PID" 2>/dev/null; then
    tail -n 80 /app/logs/dca_bot.log >&2 || true
    fail "Python Bot Manager gagal startup."
fi

log "Memulai Node Dashboard pada port $PORT..."
node dashboard.js &
NODE_PID=$!

# If either process exits, stop the companion process so Docker can restart
# the service cleanly according to restart_policy.
set +e
wait -n "$PYTHON_PID" "$NODE_PID"
EXIT_CODE=$?
set -e

log "Salah satu proses XBot berhenti (exit=$EXIT_CODE). Menutup container agar dapat direstart."
exit "$EXIT_CODE"
