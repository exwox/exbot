# XBot Operations Runbook

## Supported runtime

- Production image: Node.js 22 (see `Dockerfile`) with an isolated Python venv.
- Local baseline checked on 2026-08-10: Node 24.19.0, npm 11.17.0, Python 3.14.4, SQLite 3.46.1.
- Supported process layout: `dashboard.js` for HTTP/API and `app.py --no-dashboard` for workers.
- `docker-entrypoint.sh` supervises both processes in production.

## Pre-deployment checklist

1. Stop every bot and confirm exchange orders are understood.
2. Stop the old XBot process so SQLite can checkpoint WAL safely.
3. Back up `.env`, `dca_bot.db`, and any remaining `-wal`/`-shm` files to protected storage.
4. Restore the backup into a temporary directory and run `PRAGMA quick_check`.
5. Activate the project virtual environment, then run `npm test`,
   `npm run test:integration`, and `npm run test:python`.
   After building `xbot:test`, run `npm run test:docker` to require both
   container health endpoints to become ready.
6. Run the credential migration with `--dry-run` before applying it.
7. Start in dry-run mode and inspect Node and Python logs.

## Startup

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f xbot
```

The image compiles the native Node `sqlite3` binding from source and exercises
an in-memory query during build. If logs report a missing GLIBC version for
`node_sqlite3.node`, rebuild with `docker compose build --no-cache xbot` and
force-recreate the service; never copy host `node_modules` into the image.

Verify both process layers after startup:

```bash
curl -fsS http://127.0.0.1:5000/healthz
curl -fsS http://127.0.0.1:5000/readyz
```

`/healthz` checks the Node HTTP process. `/readyz` returns HTTP 200 only when
SQLite responds and the Python Bot Manager heartbeat is no more than 15 seconds
old. Docker uses `/readyz` for its container healthcheck.

On a new database, set `ADMIN_PASSWORD` for the first startup. Remove that value after the administrator is created. There is no bundled default password.

## Trading safety controls

Set these values in `.env` before enabling a live bot:

```bash
MAX_ACCOUNT_EXPOSURE_IDR=1000000
API_CIRCUIT_FAILURE_THRESHOLD=5
API_CIRCUIT_COOLDOWN_SECONDS=120
LIVE_TRADING_ENABLED=false
LIVE_TRADING_CONFIRMATION=
LIVE_TRADING_BOT_IDS=
LIVE_MIN_DRY_RUN_CYCLES=1
```

`MAX_ACCOUNT_EXPOSURE_IDR=0` disables the account-wide cap and is therefore not
appropriate for the live rollout gate. A nonzero cap counts the full planned
capital of every active live cycle (BO plus all configured SO). The check and
reservation occur before the private order request. Dry-run positions do not
consume this live limit.

The circuit breaker is per bot. Repeated errors for the same exchange operation
open it at the configured threshold; nonce/timestamp drift and unreconcilable
order state open it immediately. While open, the worker performs no trading
tick. It permits one guarded retry after cooldown and records both transitions.

Authenticated clients can inspect the current reservation through
`GET /api/accounts/:id/exposure`. The endpoint is tenant-scoped and does not
contact the exchange.

## Fail-closed live rollout gate

Converting or starting a live bot requires all five conditions:

1. `LIVE_TRADING_ENABLED=true`.
2. `LIVE_TRADING_CONFIRMATION=I_ACCEPT_LIVE_TRADING_RISK`.
3. `MAX_ACCOUNT_EXPOSURE_IDR` is a finite value greater than zero.
4. The exact bot ID is present in comma-separated `LIVE_TRADING_BOT_IDS`.
5. Its ledger contains at least `LIVE_MIN_DRY_RUN_CYCLES` completed dry-run
   cycles (pilot default: one).

Keep the defaults (`false`, empty confirmation, and zero exposure) throughout
development and dry-run validation. Stop the bot before changing its mode,
then restart the Node and Python processes after changing environment values.
New bots must always be created in dry-run. After the required cycles finish,
stop the chosen bot, add only that bot ID to the allowlist, set the other gate
values, and restart both runtimes. Query
`GET /api/live-readiness?bot_id=<owned-bot-id>` before changing its mode. The
response reports the completed/required cycle counts and each gate as a
boolean, but never returns the allowlisted IDs or confirmation value.

The Python manager independently checks the same allowlist and dry-run ledger
evidence. A live bot inserted or modified directly in SQLite is stopped before
credential decryption or worker startup and produces a `LIVE_TRADING_BLOCKED`
alert. Closing the gate later
does not liquidate or cancel an already open live position automatically; use
the normal Stop and orphan-order procedures so exchange state remains explicit.

Only dry-run cycles closed by a recorded `TAKE_PROFIT` or `STOP_LOSS`, with a
positive exit price and traded amount, count toward rollout readiness. Manual
reset, mode-transition cleanup, and failed/cancelled base entries never count.
Node and Python also require `stop_loss_percent>0`, a positive strategy capital
plan fully covered by `max_position_amount`, and an account exposure cap large
enough for that plan.

After a rollout or emergency stop, return `LIVE_TRADING_ENABLED=false`, clear
the confirmation and allowlist, and restart XBot. Reopening live mode always
requires the operator to set every gate again.

Sebelum mengubah `dry_run`, jalankan preflight read-only untuk bot pilot:

```bash
npm run preflight:live -- --bot-id BOT_ID
```

Exit code `0` berarti gate environment, bukti dry-run, profil risiko, status
account, posisi, dan order ledger siap untuk langkah operator berikutnya. Exit
code `2` berarti rollout tetap diblokir; perbaiki seluruh `reasons` pada output
JSON. Preflight tidak mengubah database, mode bot, atau mengirim request ke
exchange. Pemeriksaan HTTPS/firewall, izin API tanpa withdrawal, pendanaan,
dan backup off-host tetap wajib dilakukan operator secara terpisah.

Audit ulang aritmetika setiap siklus dry-run terhadap trade ledger sebelum
preflight. Angka `--require-closed` harus sama dengan gate rollout:

```bash
npm run audit:dry-run -- --bot-id BOT_ID --require-closed 1
```

Audit menghitung ulang modal beli, jumlah aset, nilai jual gross/net, fee, dan
realized profit tanpa menulis database. Hanya siklus `TAKE_PROFIT` atau
`STOP_LOSS` dengan exit trade yang dihitung sebagai bukti rollout; reset manual
tetap dapat memiliki ledger yang konsisten tetapi tidak menambah evidence.

On restart with an active position, the worker restores TP/SO children from the
durable order ledger, including a terminal order committed immediately before
a crash. It applies cumulative downtime fills first and only then cancels and
rebuilds the remaining strategy orders. Orders predating the durable ledger and
lacking both exchange/client IDs still require the orphan-order procedure.

## Credential migration

```bash
bash scripts/run_python.sh scripts/migrate_credentials.py --dry-run
bash scripts/run_python.sh scripts/migrate_credentials.py
```

The migration is transactional and idempotent. The application can read v2,
legacy Node CBC, and Python Fernet values while new writes use account-bound
AES-256-GCM v3. Moving a v3 ciphertext to a different account ID makes
authenticated decryption fail.

## Rollback

1. Stop XBot before replacing SQLite files.
2. Preserve the failed database and logs for diagnosis.
3. Restore the matching database backup and its original `ENCRYPTION_KEY` together.
4. Restore all WAL/SHM files belonging to that same backup, or restore a clean checkpointed database without them.
5. Deploy the previous image and start in dry-run mode.
6. Verify account decryption and exchange state before permitting live mode.

Never restore a database with a different encryption key. Never overwrite the only known-good backup.

## Automated encrypted backups

XBot creates an online-consistent SQLite snapshot, runs `PRAGMA quick_check`,
encrypts it as an `XBOTBKP1` AES-GCM file, writes a SHA-256 manifest, and then
applies retention:

```bash
docker compose exec -T xbot python scripts/backup_database.py
```

Set a dedicated `BACKUP_ENCRYPTION_KEY` in production. If it is empty, the tool
falls back to `ENCRYPTION_KEY`. Preserve the chosen key separately from the
backup; losing it makes restore impossible. The default location is the
`./backups` bind mount and the default retention is 14 snapshots.

Example host cron entry for a daily 02:15 backup:

```cron
15 2 * * * cd /path/to/xbot && /usr/bin/docker compose exec -T xbot python scripts/backup_database.py >> /var/log/xbot-backup.log 2>&1
```

Verify a selected backup regularly:

```bash
docker compose exec -T xbot python scripts/backup_database.py \
  --verify /app/backups/xbot-YYYYMMDDTHHMMSSZ-xxxxxxxx.xbk
```

Test restore into a new filename while XBot remains isolated from that file:

```bash
docker compose exec -T xbot python scripts/backup_database.py \
  --restore /app/backups/xbot-YYYYMMDDTHHMMSSZ-xxxxxxxx.xbk \
  --restore-target /app/data/restore-test.db
```

The restore command refuses an existing target unless `--force` is explicit.
Never use `--force` against the active database. After verification, move the
test restore out of the runtime directory using an operator-approved,
recoverable procedure.

Retention on the local bind mount is not an off-host backup. Copy each `.xbk`
and its `.xbk.json` manifest to protected external storage, but store the backup
key through a separate secret-management path. Schedule a restore drill and
record its result at least monthly.

## Orphan-order recovery

If the exchange and SQLite disagree:

1. Stop the affected bot; do not reset its position immediately.
2. Record current exchange balances, open orders, and recent trade IDs.
3. Compare them with `positions`, `orders`, `trades`, and `dca_cycles` for that bot.
4. Cancel only order IDs proven to belong to the affected bot.
5. Preserve the held asset position until its cost basis is known.
6. Resume in live mode only after TP/SO state and ledger quantities match the exchange.

SO and TP fill reconciliation is cumulative and idempotent. Repeating the same
exchange response must not add a second trade. BO, SO, TP, and SL intents are
committed before submission and carry a unique `client_order_id`. On an
ambiguous ACK or restart, the worker first queries that client ID and resumes
the existing order. A base position remains `PENDING_BASE` until its fill is
known; a partial base fill is cancelled before the filled inventory is
activated and protected.

Treat the event as an orphan-order incident only when lookup by both exchange
order ID and `client_order_id` fails or the returned quantities conflict with
SQLite. In that case, keep the bot stopped and follow the manual steps above.

STOP and reset cancel only IDs recorded in the affected bot's position/order
ledger. They must never bulk-cancel every open order on the same pair because a
different bot or manual strategy may use that pair.

Reset Siklus is fail-closed and is available only after the bot reaches
`STOPPED`. If any live exchange cancellation returns an error or cannot be
confirmed, XBot keeps the position and failed order IDs in SQLite, returns an
error to the dashboard, and raises `ORDER_CANCELLATION_FAILED`. Do not force a
database reset in that state. Verify the order at Indodax, retry Stop, and use
the orphan-order procedure above. Only a fully confirmed cancellation may
archive the position as `MANUALLY_RESET`.

New Python runtime records use UTC ISO timestamps ending in `Z`, matching Node.
Existing historical naïve timestamps are preserved; the dashboard converts
both formats for display and must not rewrite history solely to change timezone.

## Log safety and correlation

Messages and metadata are redacted immediately before insertion into SQLite in
both runtimes. Known API-key, secret, authorization, cookie, session-token,
signature, encryption-key, Fernet, and v2 credential forms are replaced with
`[REDACTED]`. Do not bypass `Database.addLog`/`DatabaseManager.add_log` when
adding persistent application logs.

Worker events include `tick_id`, `cycle_id`, and, while processing an intent,
`client_order_id` in JSON metadata. HTTP responses include `X-Request-ID`, and
dashboard mutations that create ledger logs propagate that request ID.

## Operational alerts and log retention

XBot stores deduplicated alerts in SQLite for exchange circuit trips, order
reconciliation mismatches, credential decryption failures, and three or more
process starts within five minutes. A circuit or credential alert is resolved
automatically after recovery; an acknowledged alert is reopened if the same
condition occurs again.

Authenticated operators can list their tenant's alerts with
`GET /api/alerts?status=OPEN`. `POST /api/alerts/:id/acknowledge` acknowledges
only an alert owned by that user. Administrators additionally see and may
acknowledge process-level restart alerts. Alert messages and metadata pass
through the same central secret redaction as logs.

Python truncates `logs/dca_bot.log` in place at 1 MiB by default and does not
create rotated backup files. Override the hard limit with
`PYTHON_LOG_MAX_BYTES`. Docker
rotates combined container stdout (including Node) using `DOCKER_LOG_MAX_SIZE`
and `DOCKER_LOG_MAX_FILES`, defaulting to `10m` and `5`. External delivery to a
pager, chat service, or monitoring platform still requires operator-managed
infrastructure.
Dashboard diagnostic messages are silent by default; set `LOG_LEVEL=DEBUG` to
emit them as redacted JSON records during a bounded investigation.

## Live-trading gate

Profil risiko pilot yang disetujui operator pada 10 Agustus 2026 adalah satu
bot/siklus dengan `max_position_amount=90000`,
`MAX_ACCOUNT_EXPOSURE_IDR=100000`, dan `stop_loss_percent=8`. Anggaran rugi
operasional adalah Rp10.000 per siklus termasuk ruang untuk fee/slippage;
angka ini bukan jaminan harga eksekusi. Jangan menaikkan batas atau menambah
bot live tanpa persetujuan risiko baru.

Gunakan alat fail-closed berikut untuk menerapkan nilai strategy hanya ketika
bot masih dry-run. Tanpa `--apply`, perintah hanya menampilkan preview:

```bash
python scripts/set_bot_risk.py \
  --bot-id BOT_ID --stop-loss 8 --max-position 90000
python scripts/set_bot_risk.py \
  --bot-id BOT_ID --stop-loss 8 --max-position 90000 --apply
```

Alat menolak bot live, stop-loss nol, dan batas posisi yang tidak menutup modal
satu siklus penuh.

- No open P0/P1 security defect.
- No API key with withdrawal permission.
- HTTPS reverse proxy and firewall active.
- One full dry-run cycle completed.
- Maximum position amount configured and funded.
- Restart recovery tested with an open simulated position.
- Operator knows the maximum accepted loss and emergency stop procedure.
