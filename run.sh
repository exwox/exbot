#!/usr/bin/env bash
# EXBOT DCA Bot launcher for Linux/VPS.
# Run from any directory: bash run.sh

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Prevent a second `run.sh` invocation from starting another Python manager
# against the same SQLite file.
if command -v flock >/dev/null 2>&1; then
    exec 9>"$SCRIPT_DIR/.exbot-run.lock"
    flock -n 9 || { printf '[EXBOT] ERROR: EXBOT sudah berjalan dari run.sh lain.\n' >&2; exit 1; }
fi

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN=""
PYTHON_PID=""

log() { printf '[EXBOT] %s\n' "$*"; }
fail() { printf '[EXBOT] ERROR: %s\n' "$*" >&2; exit 1; }

cleanup() {
    if [[ -n "${PYTHON_PID:-}" ]] && kill -0 "$PYTHON_PID" 2>/dev/null; then
        log "Menghentikan Python Bot Manager (PID $PYTHON_PID)..."
        kill "$PYTHON_PID" 2>/dev/null || true
        wait "$PYTHON_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

command -v node >/dev/null 2>&1 || fail "Node.js tidak ditemukan. Instal Node.js LTS terlebih dahulu."

if command -v python3 >/dev/null 2>&1; then
    SYSTEM_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    SYSTEM_PYTHON="$(command -v python)"
else
    fail "Python 3 tidak ditemukan. Instal python3 dan python3-venv terlebih dahulu."
fi

if [[ ! -f .env ]]; then
    fail "File .env belum ada. Salin .env.example menjadi .env, lalu isi ENCRYPTION_KEY yang aman."
fi

if grep -q '^ENCRYPTION_KEY=your-encryption-key-here' .env; then
    fail "ENCRYPTION_KEY di .env masih contoh. Buat key baru sebelum menjalankan bot."
fi

create_venv() {
    log "Membuat Python virtual environment..."
    "$SYSTEM_PYTHON" -m venv "$VENV_DIR" || fail "Gagal membuat venv. Di Ubuntu/Debian instal paket python3-venv."
}

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    create_venv
fi
PYTHON_BIN="$VENV_DIR/bin/python"

# Some minimal VPS images create a venv without pip. Repair that case before
# attempting to install requirements; otherwise startup stops with
# "No module named pip".
if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    log "pip tidak tersedia di virtual environment. Memasang dukungan Python VPS..."
    if command -v apt-get >/dev/null 2>&1; then
        if [[ "$(id -u)" -eq 0 ]]; then
            APT=(apt-get)
        elif command -v sudo >/dev/null 2>&1; then
            APT=(sudo apt-get)
        else
            fail "Butuh hak root untuk memasang python3-venv/python3-pip. Jalankan dengan sudo atau instal paket tersebut manual."
        fi
        "${APT[@]}" update
        "${APT[@]}" install -y python3-venv python3-pip
        # The old environment has no pip and cannot be repaired reliably on
        # Debian/Ubuntu because ensurepip is intentionally disabled.
        rm -rf "$VENV_DIR"
        create_venv
        PYTHON_BIN="$VENV_DIR/bin/python"
    else
        fail "pip tidak tersedia. Instal paket Python venv/pip sesuai distro VPS, hapus .venv, lalu jalankan ulang."
    fi
fi

"$PYTHON_BIN" -m pip --version >/dev/null 2>&1 || fail "pip tetap tidak tersedia setelah perbaikan virtual environment."

if [[ ! -f "$VENV_DIR/.requirements-installed" || requirements.txt -nt "$VENV_DIR/.requirements-installed" ]]; then
    log "Menginstal dependensi Python..."
    "$PYTHON_BIN" -m pip install --upgrade pip
    "$PYTHON_BIN" -m pip install -r requirements.txt
    touch "$VENV_DIR/.requirements-installed"
fi

if [[ ! -d node_modules ]]; then
    log "Menginstal dependensi Node.js..."
    if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
fi

mkdir -p data logs
if [[ ! -f data/dca_bot.db ]]; then
    log "Menyiapkan database..."
    "$PYTHON_BIN" app.py --setup
fi

log "Memverifikasi administrator..."
node scripts/ensure_admin.js || fail "Administrator belum dikonfigurasi. Isi ADMIN_PASSWORD di .env untuk startup pertama."

PYTHON_LOG="$SCRIPT_DIR/logs/python-manager.log"
touch "$PYTHON_LOG"

log "Memulai Python Bot Manager (log: logs/python-manager.log)..."
PYTHONUNBUFFERED=1 "$PYTHON_BIN" app.py --no-dashboard >> "$PYTHON_LOG" 2>&1 &
PYTHON_PID=$!

sleep 3
if ! kill -0 "$PYTHON_PID" 2>/dev/null; then
    tail -n 40 "$PYTHON_LOG" >&2 || true
    fail "Python Bot Manager berhenti saat startup. Periksa logs/python-manager.log."
fi

log "Python Bot Manager aktif (PID $PYTHON_PID)."
log "Status awal Python Bot Manager:"
tail -n 20 "$PYTHON_LOG" || true
log "Memulai dashboard di port ${PORT:-5000}..."
log "Tekan Ctrl+C untuk menghentikan dashboard dan Python Bot Manager."
node dashboard.js
