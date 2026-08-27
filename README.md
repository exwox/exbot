# EXBOT DCA Bot - Multi-Account Version

Bot trading DCA (Dollar Cost Averaging) otomatis untuk Indodax Exchange dengan support multi-account.

## 🏗️ Arsitektur Produksi

Sistem menggunakan **Node.js dashboard/API + Python Bot Manager + SQLite**. Docker menjalankan dan mengawasi kedua proses; SQLite adalah sumber kebenaran bersama.
Komponen historis yang bukan entry point production dicatat di [LEGACY.md](LEGACY.md).

```
┌─────────────────────────────────────────┐
│ Node.js Dashboard/API (Port 5000)       │
│ - Auth, multi-user REST API, UI         │
└─────────────────────────────────────────┘
            │ SQLite
            ▼
┌─────────────────────────────────────────┐
│ Python Bot Manager                      │
│ - Worker per bot, reconciliation, ledger│
└─────────────────────────────────────────┘
            │ HTTPS API
            ▼
┌─────────────────────────────────────────┐
│      Indodax API (tapi.indodax.com)      │
└─────────────────────────────────────────┘
```

## 📁 Struktur File

```
xbot/
├── dashboard.js              # Main Express server
├── database.js               # SQLite database manager
├── accounts.js               # Account management (multi-account)
├── indodax-client.js         # Indodax API client
├── api-endpoints.js          # REST API routes
├── app.py                    # Python Bot Manager entry point
├── core/                     # Worker dan strategy engine
├── scripts/                  # Admin bootstrap dan migrasi credential
├── .env                      # Environment variables (ENCRYPTION_KEY)
├── data/
│   └── dca_bot.db            # SQLite database
├── templates/
│   ├── index.html            # Dashboard page
│   ├── settings.html         # Settings page (multi-account)
│   ├── trades.html           # Trade history
│   └── logs.html             # Logs viewer
├── run.sh                    # Launcher development Linux/VPS
├── docker-entrypoint.sh      # Supervisor production
└── package.json              # Node.js dependencies
```

## 🚀 Instalasi & Setup

### 1. Install Dependencies

```bash
npm install
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Command test Python otomatis memakai `.venv/bin/python` jika virtual environment
tersebut tersedia, dan memakai `python3` (atau nilai environment `PYTHON`) pada CI.

### 2. Buat konfigurasi aman

```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

Salin output ke file `.env`:
```
ENCRYPTION_KEY=<generated_key>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<password unik minimal 10 karakter>
MAX_ACCOUNT_EXPOSURE_IDR=0
LIVE_TRADING_ENABLED=false
LIVE_TRADING_CONFIRMATION=
LIVE_TRADING_BOT_IDS=
LIVE_MIN_DRY_RUN_CYCLES=1
API_CIRCUIT_FAILURE_THRESHOLD=5
API_CIRCUIT_COOLDOWN_SECONDS=120
TELEGRAM_PRICE_CHANGE_PERCENT=5
TELEGRAM_DIGEST_HOUR=8
TELEGRAM_TIMEZONE=Asia/Jakarta
TELEGRAM_MAX_MESSAGES_PER_RUN=1
BACKUP_ENCRYPTION_KEY=<secret backup terpisah>
BACKUP_DIR=backups
DOCKER_LOG_MAX_SIZE=10m
DOCKER_LOG_MAX_FILES=5
BACKUP_RETENTION=14
XBOT_BIND_ADDRESS=127.0.0.1
XBOT_HOST_PORT=5000
ADMIN_EMAIL=<email admin>
```

Telegram mengirim event transaksi/status secara langsung, sinyal harga setelah
perubahan minimum `TELEGRAM_PRICE_CHANGE_PERCENT`, sinyal RSI hanya saat masuk
zona oversold/overbought, serta ringkasan saldo/profit harian dan mingguan pada
jam `TELEGRAM_DIGEST_HOUR`. Perintah baca-saja yang tersedia: `/status`,
`/balance`, `/price`, dan `/report`.

### 3. Setup Database

**Opsi A: Otomatis melalui Docker**
```bash
docker compose up -d --build
```

Secara default Compose hanya mempublikasikan dashboard ke
`127.0.0.1:5000`. Gunakan reverse proxy HTTPS untuk akses jarak jauh. Ubah
`XBOT_BIND_ADDRESS=0.0.0.0` hanya jika firewall host sudah membatasi akses.

**Opsi B: Manual via Dashboard**
1. Jalankan `bash run.sh`
2. Buka `http://localhost:5000/settings`
3. Klik "Add Account" dan masukkan API keys

Administrator dibuat saat startup pertama. Setelah berhasil, hapus `ADMIN_PASSWORD` dari environment agar password bootstrap tidak tersimpan permanen.

Sebelum live trading, ubah `MAX_ACCOUNT_EXPOSURE_IDR` dari `0` ke batas total
modal aktif yang benar-benar disetujui untuk setiap akun. Nilai ini mencakup BO
dan seluruh SO yang direncanakan oleh siklus aktif.

Live trading bersifat fail-closed. Setelah seluruh checklist operator selesai,
set `LIVE_TRADING_ENABLED=true`, isi `LIVE_TRADING_CONFIRMATION` dengan frasa
yang didokumentasikan di [OPERATIONS.md](OPERATIONS.md), masukkan ID bot yang
telah lulus dry-run ke `LIVE_TRADING_BOT_IDS`, dan restart kedua runtime. Bot
harus berstatus `STOPPED` ketika mode diubah.

Backup database terenkripsi dapat dibuat dengan `npm run backup:db`. Detail
verifikasi, restore drill, retensi, dan penjadwalan tersedia di
[`OPERATIONS.md`](OPERATIONS.md).

### 4. Jalankan Aplikasi

**Production (direkomendasikan)**
```bash
docker compose up -d --build
```

**Development Linux/VPS tanpa Docker**
```bash
bash run.sh
```

`node dashboard.js` hanya menjalankan UI/API. `python app.py` hanya menjalankan Bot Manager. Gunakan Docker atau `run.sh` agar keduanya berjalan bersama.

## 🔧 Konfigurasi

### Multi-Account Support

Sekarang Anda bisa menambahkan multiple akun Indodax:

1. Buka `http://localhost:5000/settings`
2. Klik "+ Add Account"
3. Masukkan:
   - Account Name
   - API Key
   - API Secret
   - Exchange (Indodax)
4. Klik "Save Account"
5. Test koneksi dengan klik "Test"

### API Keys Storage

API keys **tidak disimpan di config.py**. Sekarang disimpan di:
- **Database**: `data/dca_bot.db` (AES-256-GCM v3 dengan AAD terikat account ID; pembaca format lama tetap tersedia)
- **Encryption Key**: `.env` file

### Migrasi ciphertext lama

Backup database, lalu periksa migrasi:

```bash
bash scripts/run_python.sh scripts/migrate_credentials.py --dry-run
bash scripts/run_python.sh scripts/migrate_credentials.py
```

## 📊 Fitur

### Dashboard
- Real-time price monitoring
- Balance tracking (IDR & Crypto)
- Profit/Loss calculation
- Trade history
- Bot status

### Multi-Account Management
- Add/Edit/Delete accounts
- Test API connection
- Encrypted credential storage
- Per-account bot configuration

### Bot Control
- Start/Stop bot
- Dry run mode
- Safety orders
- Take profit / Stop loss
- RSI-based re-entry

### Strategy Configuration
- Base order amount
- Safety order amount
- Max safety orders
- Price deviation
- Martingale mode
- Volume scale
- Step scale

## 🔌 API Endpoints

### Accounts
- `GET /api/accounts` - List all accounts
- `POST /api/accounts` - Create account
- `PUT /api/accounts/:id` - Update account
- `DELETE /api/accounts/:id` - Delete account
- `POST /api/accounts/:id/test` - Test connection

### Bots
- `GET /api/bots` - List all bots
- `POST /api/bots` - Create bot
- `PUT /api/bots/:id` - Update bot
- `DELETE /api/bots/:id` - Delete bot

### Strategies
- `GET /api/strategies` - List all strategies
- `POST /api/strategies` - Create strategy
- `PUT /api/strategies/:id` - Update strategy
- `DELETE /api/strategies/:id` - Delete strategy

### Status
- `GET /api/status` - Get system status
- `GET /api/live-readiness?bot_id=:id` - Status gate dan bukti dry-run bot tanpa membuka secret
- `GET /api/balances` - Get balances
- `GET /api/trades` - Get trade history
- `GET /api/open-orders` - Get open orders

### Operational alerts
- `GET /api/alerts?status=OPEN` - Alert akun milik user; admin juga melihat alert proses
- `POST /api/alerts/:id/acknowledge` - Akui alert terbuka yang dimiliki user

## 🔒 Security

- API keys dienkripsi dengan AES-256-GCM dan payload berversi
- Encryption key disimpan di `.env` (tidak di-commit ke git)
- Database file `data/dca_bot.db` berisi data terenkripsi
- Masked credentials di UI (hanya menampilkan 6 karakter awal & 4 akhir)
- Session disimpan server-side di SQLite dan browser hanya menerima cookie HttpOnly
- Endpoint private memeriksa autentikasi dan ownership user

## 📝 Catatan Penting

1. **Backup `.env`** - Jangan hilangkan encryption key, atau data terenkripsi tidak bisa di-decrypt
2. **Backup `data/dca_bot.db`** - Berisi semua akun, bot, dan history
3. Gunakan API key exchange tanpa izin withdrawal.
4. Bot baru menggunakan dry-run secara default. Aktifkan live hanya setelah pengujian dan backup.

## Pengujian

```bash
npm test
npm run test:integration
npm run test:python
# Setelah image `xbot:test` berhasil dibangun:
npm run test:docker
```

## 🐛 Troubleshooting

### "No ENCRYPTION_KEY found"
```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
# Copy output to .env file
```

### "Failed to decrypt credentials"
- Pastikan `.env` file ada dan berisi `ENCRYPTION_KEY` yang sama
- Jika hilang, data terenkripsi tidak bisa di-recover

### `GLIBC_x.xx not found` dari `node_sqlite3.node`

Image mengompilasi `sqlite3` dari source di dalam Debian dan memverifikasinya
saat build. Hapus penggunaan layer lama dengan:

```bash
sudo docker compose build --no-cache xbot
sudo docker compose up -d --force-recreate xbot
```

Jangan menghapus `node_modules` dari `.dockerignore`; dependency native host
tidak kompatibel secara portabel dengan image Linux lain.

### "No accounts found"
1. Buka `/settings`
2. Add account baru
3. Jalankan `npm run setup` jika database belum dibuat, lalu tambahkan akun melalui Settings.

## 📚 Dokumentasi

- `API_DOCUMENTATION.md` - API documentation lengkap
- `multiakunplan.md` - Plan untuk multi-account support

## 🆘 Support

Jika mengalami masalah:
1. Check logs di console
2. Pastikan `.env` berisi `ENCRYPTION_KEY`
3. Pastikan `data/dca_bot.db` bisa di-write
4. Test API connection di halaman Settings
