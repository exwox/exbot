# XBot Docker Deployment

## 1. Security first

The original project archive contained a `config.py` with credentials that looked live.
This Docker package intentionally excludes `config.py` and `.env` from the image.
Rotate the old exchange API credentials before production deployment if they are still valid.

## 2. Prepare VPS directory

```bash
sudo mkdir -p /opt/xbot/data /opt/xbot/logs
sudo chown -R $USER:$USER /opt/xbot
cd /opt/xbot
```

Upload/extract this Docker-ready source into `/opt/xbot`.

## 3. Create production `.env`

Start from `.env.example`:

```bash
cp .env.example .env
nano .env
```

Required:

```env
ENCRYPTION_KEY=YOUR_EXISTING_PRODUCTION_KEY
ADMIN_USERNAME=admin
ADMIN_PASSWORD=USE_A_LONG_UNIQUE_PASSWORD_ON_FIRST_START
ADMIN_EMAIL=admin@example.com
DATABASE_PATH=/app/data/dca_bot.db
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=5000
DASHBOARD_DEBUG=false
LOG_LEVEL=INFO
```

IMPORTANT: If you are migrating an existing encrypted database, use the SAME
`ENCRYPTION_KEY` that encrypted its account credentials. Do not generate a new
key unless you intentionally start with a new database.

`ADMIN_PASSWORD` is required only when no administrator exists. Remove it from
the environment after the first successful startup. Never use a default or
shared password.

## 4. Existing SQLite database

If this is an upgrade from an already-running XBot, stop the old bot first and
copy its SQLite files into `/opt/xbot/data`.

Because SQLite WAL mode is used, stop the old application before copying. The
main file is:

```text
data/dca_bot.db
```

If `dca_bot.db-wal` / `dca_bot.db-shm` still exist after a clean shutdown, copy
them together with the main DB or checkpoint the DB before migration.

## 5. Build

```bash
cd /opt/xbot
docker compose build
```

For an existing database that uses CBC/Fernet credential records, make a
backup and migrate before live trading:

```bash
docker compose run --rm xbot python scripts/migrate_credentials.py --dry-run
docker compose run --rm xbot python scripts/migrate_credentials.py
```

## 6. Start

```bash
docker compose up -d
```

## 7. Check

```bash
docker compose ps
docker compose logs -f xbot
curl -fsS http://127.0.0.1:5000/healthz
curl -fsS http://127.0.0.1:5000/readyz
```

`healthz` proves the Node process is alive. `readyz` also checks SQLite and the
Python Bot Manager heartbeat; this is the endpoint used by Docker HEALTHCHECK.

Dashboard default:

```text
http://VPS_IP:5000
```

Do not expose plain HTTP directly to the internet. Put XBot behind an HTTPS
reverse proxy and restrict port 5000 with the VPS firewall.

## 8. Stop / restart

```bash
docker compose stop xbot
docker compose restart xbot
```

To remove the container but KEEP host-mounted database/log data:

```bash
docker compose down
```

`./data` and `./logs` remain on the VPS.

## 9. Update later

After replacing source code with a newer XBot version while keeping `.env`,
`data/`, and `logs/`:

```bash
docker compose up -d --build xbot
docker compose logs -f xbot
```

## Important paths

- Production config: `/opt/xbot/.env`
- Persistent SQLite: `/opt/xbot/data/dca_bot.db`
- Persistent logs: `/opt/xbot/logs/`
- Container database path: `/app/data/dca_bot.db`
