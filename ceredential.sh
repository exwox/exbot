#!/usr/bin/env bash
# Bootstrap dependency EXBOT untuk VPS Ubuntu/Debian.
# Tidak menyimpan atau menimpa API Key, Secret Key, maupun .env yang sudah ada.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log() { printf '[EXBOT SETUP] %s\n' "$*"; }
fail() { printf '[EXBOT SETUP] ERROR: %s\n' "$*" >&2; exit 1; }

if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=()
elif command -v sudo >/dev/null 2>&1; then
    SUDO=(sudo)
else
    fail "Jalankan sebagai root atau instal sudo terlebih dahulu."
fi
APT=("${SUDO[@]}" apt-get)

command -v apt-get >/dev/null 2>&1 || fail "Script ini khusus VPS Ubuntu/Debian (apt-get)."

log "Memperbarui indeks paket..."
"${APT[@]}" update

log "Menginstal sistem dependency..."
"${APT[@]}" install -y \
    ca-certificates curl gnupg \
    build-essential python3 python3-venv python3-pip python3-dev \
    sqlite3

# Node.js is required by dashboard.js. Prefer the distro package when it is
# already modern enough; otherwise install the current Node.js LTS repository.
NODE_MAJOR=0
if command -v node >/dev/null 2>&1; then
    NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)"
fi
if [[ "$NODE_MAJOR" -lt 18 ]]; then
    log "Menginstal Node.js LTS..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | "${SUDO[@]}" bash -
    "${APT[@]}" install -y nodejs
fi

command -v python3 >/dev/null 2>&1 || fail "Python 3 gagal diinstal."
command -v node >/dev/null 2>&1 || fail "Node.js gagal diinstal."

mkdir -p data logs

if [[ ! -f .env ]]; then
    cp .env.example .env
    log "Membuat .env dari .env.example. Isi ENCRYPTION_KEY sebelum menjalankan bot."
fi

if [[ ! -f config.py && -f config.py.example ]]; then
    cp config.py.example config.py
    log "Membuat config.py dari config.py.example. Tinjau konfigurasinya sebelum menjalankan bot."
fi

if [[ ! -x .venv/bin/python ]]; then
    log "Membuat Python virtual environment aplikasi..."
    python3 -m venv .venv
fi
log "Menginstal dependency Python aplikasi..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
touch .venv/.requirements-installed

log "Menginstal dependency Node.js aplikasi..."
if [[ -f package-lock.json ]]; then npm ci; else npm install; fi

log "Versi terpasang: Python $(python3 --version), Node $(node --version), npm $(npm --version)"
log "Dependency VPS selesai. Jalankan: bash run.sh"
