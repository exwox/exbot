# Penggunaan Mode Real

Mode real dipilih langsung pada Settings → Mode Trading → Real → Simpan.
Bot baru tetap menggunakan Dry Run secara default; API juga menerima
`POST /api/bots` dengan `dry_run=false`.

Tidak ada persyaratan flag environment, frasa konfirmasi, allowlist, jumlah
siklus dry run, stop-loss minimum, batas posisi, atau batas exposure.
Variabel `LIVE_TRADING_ENABLED`, `LIVE_TRADING_CONFIRMATION`,
`LIVE_TRADING_BOT_IDS`, `LIVE_MIN_DRY_RUN_CYCLES`, dan
`MAX_ACCOUNT_EXPOSURE_IDR` dari instalasi lama diabaikan.

Bot dry run yang RUNNING dapat langsung diubah ke real. Manager menyelesaikan
worker lama terlebih dahulu, membatalkan order simulasi lokal, lalu membuat
worker real. Posisi simulasi terbuka diarsipkan sebagai `SIMULATION_CLOSED`;
riwayat simulasi tetap tersimpan. Worker real memulai siklus dengan modal dan
mode entry terbaru (MARKET, LIMIT, RSI, atau RSI_LIMIT). Bot STOPPED tetap
STOPPED sampai tombol Start ditekan.

Kembali dari real ke dry run memerlukan Stop agar pembatalan order exchange
diproses. Posisi uang asli yang masih terbuka tidak dijadikan posisi simulasi;
worker simulasi menunggu sampai posisi real ditangani.

Preflight dan audit bersifat diagnostik, bukan izin aktivasi:

```bash
docker compose exec -T xbot node scripts/live_rollout_preflight.js --bot-id BOT_ID
npm run audit:dry-run -- --bot-id BOT_ID --require-closed 0
```

Preflight exit 0 berarti bot ditemukan dan mode real tersedia. Status account,
posisi, dan order ditampilkan sebagai informasi. Exit 2 berarti bot tidak
ditemukan; exit 1 berarti pemeriksaan gagal. Audit menghitung konsistensi ledger
simulasi, termasuk setelah bot sudah berpindah ke real.

Login, kepemilikan bot, kredensial exchange, saldo aktual untuk grid, validitas
parameter strategi, pencatatan fill, rekonsiliasi order, dan circuit breaker
error API tetap berlaku sebagai fungsi operasional trading.
Stop-loss 0 berarti stop-loss tidak aktif. Kolom lama `max_position_amount`
tetap disimpan untuk kompatibilitas, tetapi tidak membatasi entry.

Setelah perubahan kode, bangun ulang runtime:

```bash
docker compose up -d --build xbot
```

Build tidak mengubah nilai `dry_run` bot di database. Mode real baru digunakan
setelah dipilih pengguna.
