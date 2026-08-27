# Rollout Live Checklist — Satu Akun Uji (Pilot)

Dokumen ini adalah panduan eksekusi **oleh operator** untuk membuka live trading
secara bertahap sesuai `plan.md` Fase 8. Seluruh langkah di sini bersifat
manusia/operator; **tidak ada skrip yang membuka gate secara otomatis**. Gate
tetap tertutup (`LIVE_TRADING_ENABLED=false`) sampai operator menyelesaikan
tahapan berikut satu per satu.

> Prinsip: pembukaan live hanya dilakukan melalui langkah eksplisit operator
> setelah (a) bukti dry-run valid, (b) profil risiko disetujui, (c) preflight
> read-only lulus dengan `allowed=true`, dan (d) semua prasyarat eksternal
> (HTTPS/firewall, API key tanpa withdrawal, pendanaan, backup off-host) aman.

---

## 1. Status terverifikasi (18 Agustus 2026)

| Item | Nilai | Status |
|---|---|---|
| Bot pilot | `bot_1786372559701_55c6bec2` | RUNNING, `dry_run=true` |
| Bukti dry-run | `pos_c78ad7395dd7` CLOSED/`TAKE_PROFIT` (7 trade, audit valid) | ✅ |
| Audit aritmetika | `scripts/audit_dry_run_cycles.py` → `valid: true` | ✅ |
| Preflight | `dry_run_evidence_ready=true`, `strategy_risk_ready=true`, `allowed=false` (gate tertutup, sesuai) | ✅ |
| Profil risiko | SL 8%, modal siklus Rp90.000, batas posisi Rp90.000 | ✅ disetujui operator |
| `MAX_ACCOUNT_EXPOSURE_IDR` | 100000 (nonzero) | ✅ |
| `LIVE_TRADING_ENABLED` | `false` | 🔒 tertutup |
| `LIVE_TRADING_CONFIRMATION` | kosong | 🔒 tertutup |
| `LIVE_TRADING_BOT_IDS` | kosong | 🔒 tertutup |
| Backup | `xbot-20260818T120212Z-f57a23e3.xbk` terverifikasi | ✅ |
| Test | Node 15/15, Python 55/55 | ✅ |
| Container | `xbot:1.0.0` healthy, `/readyz` HTTP 200 | ✅ |

## 2. Prasyarat eksternal (wajib, di luar workspace)

- [ ] HTTPS reverse proxy aktif dan HTTP diarahkan ke HTTPS (Fase 3 plan.md).
- [ ] Firewall hanya membuka port yang diperlukan.
- [ ] API key Indodax **tanpa izin withdrawal**; hanya read + trade.
- [ ] Saldo akun uji terisi nominal minimum untuk satu pair yang dipilih.
- [ ] Salinan `.env` dan encryption key disimpan **off-host** (mis. password manager/secret store operator).
- [ ] Restore drill backup terjadwal (lihat `OPERATIONS.md`).

## 3. Verifikasi read-only sebelum membuka gate

```bash
# Dari root repositori
npm run preflight:live -- --bot-id bot_1786372559701_55c6bec2
# Harus: allowed=false dan semua reasons hanya berupa gate environment/
# posisi aktif — bukan kegagalan bukti/profil risiko.

npm run audit:dry-run -- --bot-id bot_1786372559701_55c6bec2 --require-closed 1
# Harus: valid=true, valid_closed_cycles>=1.
```

Preview profil risiko (tidak menulis database):

```bash
docker compose exec -T xbot python scripts/set_bot_risk.py \
  --bot-id bot_1786372559701_55c6bec2 \
  --stop-loss 8 --max-position 90000
# "applied": false. Hasil saat ini sudah lolos:
#   planned_capital_idr=90000, stop_loss_percent=8, max_position_amount=90000
```

## 4. Tahapan eksekusi operator

### Step 1 — Hentikan bot dan pastikan state bersih
1. Hentikan bot pilot via dashboard **Stop** (atau `POST /api/bots/:id/stop`).
2. Tunggu hingga status bot `STOPPED`.
3. Pastikan **tidak ada posisi aktif** (`OPEN`/`PENDING_BASE`) dan **tidak ada
   order/intent recoverable** pada bot tersebut. Cek via dashboard; posisi
   dry-run yang sedang OPEN harus diselesaikan/di-reset dulu dengan prosedur
   Reset Siklus fail-closed (`POST /api/bots/:id/reset-position`) — **bukan**
   mengubah mode saat masih ada posisi.

### Step 2 — Siapkan akun uji (opsional jika memakai bot pilot yang sama)
- Buat bot baru selalu dimulai dalam `dry_run=true` (`POST /api/bots`).
- Strategi mengikuti profil yang disetujui (modal siklus Rp90.000, SL 8%,
  exposure akun Rp100.000), satu pair pilihan operator.
- Validasi kembali satu siklus dry-run CLOSED untuk bot baru sebelum lanjut.

### Step 3 — Buka gate di `.env` (keputusan operator eksplisit)
Di file `.env` pada VPS (bukan di commit, `.env` sudah di-ignore):

```dotenv
LIVE_TRADING_ENABLED=true
LIVE_TRADING_CONFIRMATION=I_ACCEPT_LIVE_TRADING_RISK
LIVE_TRADING_BOT_IDS=bot_1786372559701_55c6bec2
MAX_ACCOUNT_EXPOSURE_IDR=100000
LIVE_MIN_DRY_RUN_CYCLES=1
```

Kelima kondisi dari `OPERATIONS.md` § Fail-closed live rollout gate harus
terpenuhi. Hanya ID bot uji yang masuk allowlist — jangan pernah memasukkan
semua bot.

### Step 4 — Restart runtime dan verifikasi preflight
```bash
docker compose up -d --force-recreate xbot
docker compose ps                 # status healthy
curl -sf http://localhost:5000/healthz && echo OK
curl -sf http://localhost:5000/readyz && echo OK

npm run preflight:live -- --bot-id bot_1786372559701_55c6bec2
# Exit code 0 dan "allowed": true adalah syarat lanjut.
```

Cek readiness via API (dengan session operator):

```bash
curl -s -b cookies.txt \
  'http://localhost:5000/api/live-readiness?bot_id=bot_1786372559701_55c6bec2'
# Semua gate boolean harus true; respon tidak pernah mengembalikan
# konfirmasi/allowlist.
```

### Step 5 — Aktifkan live pada bot uji
Bot harus dalam status `STOPPED` sebelum mengubah mode (validasi di
`PUT /api/bots/:id` menolak perubahan saat bot berjalan):

```bash
curl -s -b cookies.txt -X PUT \
  -H 'Content-Type: application/json' \
  -d '{"dry_run": false}' \
  'http://localhost:5000/api/bots/:id'
```

Jika gate gagal, respons `409`/`403` berisi alasan; jangan paksa. Perbaiki
alasan tersebut, bukan menekan mode live lewat SQLite (Python manager akan
menghentikan bot langsung dan memicu alert `LIVE_TRADING_BLOCKED`).

### Step 6 — Mulai dan pantau satu siklus lengkap
1. `POST /api/bots/:id/start` (setelah pastikan status STOPPED dan mode live aktif).
2. Pantau: **BO → SO (opsional) → TP/SL** hingga posisi `CLOSED`.
3. Amati alert operasional (`/api/alerts`) — khususnya circuit exchange,
   restart loop, mismatch rekonsiliasi.
4. Pastikan `/healthz` dan `/readyz` tetap 200 dan tidak ada `LIVE_TRADING_BLOCKED`.

### Step 7 — Rekonsiliasi ledger vs balance exchange
- Bandingkan ledger trade dengan history exchange untuk siklus pertama live.
- Periksa inventory, fee, realized profit konsisten dengan saldo akun.
- Jika cocok → lanjut Step 8. Jika tidak → Stop, gunakan prosedur
  orphan-order recovery (`OPERATIONS.md` § Orphan-order recovery), jangan reset posisi dulu.

### Step 8 — Perluas ke akun lain secara bertahap
- Tambahkan satu akun/bot per langkah, ulangi Step 3–7 untuk masing-masing.
- Jangan pernah menambah bot baru ke allowlist sekaligus.

## 5. Rollback / emergency stop

- **Stop darurat:** `POST /api/bots/:id/stop`. Stop hanya membatalkan order
  exchange ID yang tercatat untuk bot tersebut. Jika posisi terbuka,
  jangan langsung reset; gunakan prosedur orphan-order.
- **Tutup gate:** setelah rollout/emergency, kembalikan
  `LIVE_TRADING_ENABLED=false`, kosongkan `LIVE_TRADING_CONFIRMATION` dan
  `LIVE_TRADING_BOT_IDS`, lalu restart XBot. Membuka live lagi selalu
  mengharuskan semua gate di-set ulang.
- **Rollback deployment:** ikuti `OPERATIONS.md` § Rollback — restore database
  **beserta** encryption key yang sama, jangan timpa backup satu-satunya.

## 6. Go/no-go final (centang oleh operator)

- [ ] Tidak ada temuan P0/P1 yang terbuka.
- [ ] HTTPS + firewall aktif; API key tanpa withdrawal.
- [ ] Encryption key dan backup tersimpan aman off-host.
- [ ] Preflight `allowed=true` dan live-readiness semua gate `true`.
- [ ] Satu akun uji nominal minimum + satu pair berjalan.
- [ ] Satu siklus live lengkap BO → SO → TP/SL tertutup bersih.
- [ ] Ledger dan balance cocok setelah siklus pertama.
- [ ] Buat keputusan: buka live lanjutan atau kembalikan gate ke `false`.

> Setelah dokumen ini dieksekusi penuh, centang item Fase 8 yang tersisa di
> `plan.md` secara manual.

