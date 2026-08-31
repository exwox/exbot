/**
 * Database Manager - SQLite untuk Node.js
 * Menyimpan akun, bot, dan konfigurasi
 */
const sqlite3 = require('sqlite3').verbose();
const { redactSensitive, safeMetadata } = require('./log-redaction');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { decryptCredential, encryptCredential, validateMasterKey } = require('./credential-crypto');

const DB_PATH = process.env.DB_PATH || 'data/dca_bot.db';

class Database {
    constructor() {
        this.db = null;
        this.encryptionKey = null;
    }

    init() {
        // Ensure data directory exists
        const dir = path.dirname(DB_PATH);
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }

        this.db = new sqlite3.Database(DB_PATH, (err) => {
            if (err) {
                console.error('[DB] Failed to connect:', err.message);
                process.exit(1);
            }
            console.log('[DB] Connected to SQLite database');
        });

        this.db.serialize(() => {
            // Enable WAL mode for better performance
            this.db.run('PRAGMA journal_mode=WAL');
            this.db.run('PRAGMA busy_timeout=30000');
            this.db.run('PRAGMA synchronous=NORMAL');
            this.db.run('PRAGMA foreign_keys=ON');

            // Accounts table
            this.db.run(`
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL DEFAULT 'Indodax',
                    api_version TEXT NOT NULL DEFAULT 'v1',
                    api_key_encrypted TEXT NOT NULL DEFAULT '',
                    api_secret_encrypted TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_connected_at TEXT,
                    last_error TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            `);

            // Bots table
            this.db.run(`
                CREATE TABLE IF NOT EXISTS bots (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL DEFAULT 'Indodax',
                    pair TEXT NOT NULL DEFAULT 'btcidr',
                    status TEXT NOT NULL DEFAULT 'STOPPED',
                    dry_run INTEGER NOT NULL DEFAULT 1,
                    strategy_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
            `);

            // Strategies table
            this.db.run(`
                CREATE TABLE IF NOT EXISTS strategies (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT 'Default',
                    base_order_amount REAL NOT NULL DEFAULT 15000,
                    safety_order_amount REAL NOT NULL DEFAULT 15000,
                    max_safety_orders INTEGER NOT NULL DEFAULT 5,
                    price_deviation REAL NOT NULL DEFAULT 1.2,
                    deviation_scale REAL NOT NULL DEFAULT 1.5,
                    volume_scale REAL NOT NULL DEFAULT 1.5,
                    take_profit_percent REAL NOT NULL DEFAULT 1.0,
                    stop_loss_percent REAL NOT NULL DEFAULT 0.0,
                    max_position_amount REAL NOT NULL DEFAULT 0,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 0,
                    martingale_enabled INTEGER NOT NULL DEFAULT 0,
                    rsi_period INTEGER NOT NULL DEFAULT 14,
                    rsi_oversold INTEGER NOT NULL DEFAULT 60,
                    rsi_overbought INTEGER NOT NULL DEFAULT 70,
                    step_scale_enabled INTEGER NOT NULL DEFAULT 0,
                    limit_buy_fee_percent REAL NOT NULL DEFAULT 0.15,
                    limit_sell_fee_percent REAL NOT NULL DEFAULT 0.15,
                    market_buy_fee_percent REAL NOT NULL DEFAULT 0.30,
                    market_sell_fee_percent REAL NOT NULL DEFAULT 0.30,
                    initial_entry_mode TEXT NOT NULL DEFAULT 'MARKET',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            `);

            // Positions table
            this.db.run(`
                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    bot_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    base_price REAL NOT NULL DEFAULT 0,
                    average_entry_price REAL NOT NULL DEFAULT 0,
                    base_amount REAL NOT NULL DEFAULT 0,
                total_amount REAL NOT NULL DEFAULT 0,
                sold_amount REAL NOT NULL DEFAULT 0,
                total_invested REAL NOT NULL DEFAULT 0,
                reserved_capital REAL NOT NULL DEFAULT 0,
                    take_profit_price REAL NOT NULL DEFAULT 0,
                    stop_loss_price REAL NOT NULL DEFAULT 0,
                    current_price REAL NOT NULL DEFAULT 0,
                    so_entries TEXT DEFAULT '[]',
                    tp_order_id TEXT,
                    exit_order_id TEXT,
                    exit_reason TEXT NOT NULL DEFAULT '',
                    open_orders TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (bot_id) REFERENCES bots(id)
                )
            `);

            // Orders table
            this.db.run(`
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    bot_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    position_id TEXT NOT NULL DEFAULT '',
                    exchange_order_id TEXT NOT NULL DEFAULT '',
                    client_order_id TEXT NOT NULL DEFAULT '',
                    order_type TEXT NOT NULL DEFAULT 'buy',
                    side TEXT NOT NULL DEFAULT '',
                    pair TEXT NOT NULL DEFAULT 'btcidr',
                    price REAL NOT NULL DEFAULT 0,
                    amount REAL NOT NULL DEFAULT 0,
                    amount_quote REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    is_dca INTEGER NOT NULL DEFAULT 1,
                    dca_level INTEGER NOT NULL DEFAULT 0,
                    so_number INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (bot_id) REFERENCES bots(id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
            `);

            // Trades table
            this.db.run(`
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    bot_id TEXT NOT NULL,
                    position_id TEXT,
                    order_id TEXT,
                    exchange_trade_id TEXT,
                    pair TEXT NOT NULL,
                    side TEXT NOT NULL,
                    trade_type TEXT NOT NULL DEFAULT '',
                    price REAL NOT NULL DEFAULT 0,
                    amount REAL NOT NULL DEFAULT 0,
                    amount_quote REAL NOT NULL DEFAULT 0,
                    fee REAL NOT NULL DEFAULT 0,
                    fee_currency TEXT DEFAULT 'IDR',
                    cost_basis REAL NOT NULL DEFAULT 0,
                    realized_profit REAL NOT NULL DEFAULT 0,
                    realized_profit_percent REAL NOT NULL DEFAULT 0,
                    close_reason TEXT DEFAULT '',
                    dry_run INTEGER NOT NULL DEFAULT 1,
                    executed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (bot_id) REFERENCES bots(id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
            `);

            // Bot logs table
            this.db.run(`
                CREATE TABLE IF NOT EXISTS bot_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    bot_id TEXT,
                    level TEXT NOT NULL DEFAULT 'INFO',
                    event TEXT,
                    message TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    telegram_notified_at TEXT,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
            `);

            // System logs table
            this.db.run(`
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL DEFAULT 'INFO',
                    component TEXT,
                    message TEXT,
                    created_at TEXT NOT NULL
                )
            `);

            this.db.run(`
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT,
                    bot_id TEXT,
                    severity TEXT NOT NULL DEFAULT 'WARNING',
                    kind TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    message TEXT NOT NULL,
                    metadata TEXT,
                    occurrences INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    acknowledged_by TEXT,
                    FOREIGN KEY (account_id) REFERENCES accounts(id),
                    FOREIGN KEY (bot_id) REFERENCES bots(id)
                )
            `);
            this.db.run(`CREATE INDEX IF NOT EXISTS idx_alerts_status_last_seen
                         ON alerts(status, last_seen_at DESC)`);
            this.db.run(`
                CREATE TABLE IF NOT EXISTS runtime_starts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    component TEXT NOT NULL,
                    started_at TEXT NOT NULL
                )
            `);
            this.db.run(`CREATE INDEX IF NOT EXISTS idx_runtime_starts_component_time
                         ON runtime_starts(component, started_at DESC)`);

            // Users table for authentication
            this.db.run(`
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    expired_at TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            `);

            this.db.run(`
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            `);
            this.db.run('CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)');
            this.db.run('CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at)');
            this.db.run(`
                CREATE TABLE IF NOT EXISTS auth_login_failures (
                    username TEXT PRIMARY KEY,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    updated_at TEXT NOT NULL
                )
            `);

            // Telegram notification integration. The bot token is stored
            // encrypted with an account-bound context (the owner user id) and
            // is only ever decrypted inside the Telegram service. Settings for
            // the trading bots themselves remain web-only; Telegram only
            // surfaces read-only strategy/status and starts/stops a bot.
            this.db.run(`
                CREATE TABLE IF NOT EXISTS telegram_config (
                    user_id TEXT PRIMARY KEY,
                    bot_token_encrypted TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            `);
            this.db.run(`
                CREATE TABLE IF NOT EXISTS telegram_chat_bindings (
                    chat_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            `);
            this.db.run(`
                CREATE TABLE IF NOT EXISTS telegram_link_codes (
                    code TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            `);
            this.db.run(`
                CREATE TABLE IF NOT EXISTS telegram_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    source_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            `);
            this.db.run('CREATE INDEX IF NOT EXISTS idx_telegram_notifications_status ON telegram_notifications(status)');
            this.db.run('CREATE INDEX IF NOT EXISTS idx_telegram_bindings_user ON telegram_chat_bindings(user_id)');
            this.db.run('CREATE INDEX IF NOT EXISTS idx_telegram_link_codes_user ON telegram_link_codes(user_id)');
            this.db.run(`
                CREATE TABLE IF NOT EXISTS telegram_digest_state (
                    user_id TEXT NOT NULL,
                    digest_kind TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, digest_kind, period_key),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            `);


            this.db.run(`
                CREATE TABLE IF NOT EXISTS dca_cycles (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    bot_id TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    dry_run INTEGER NOT NULL DEFAULT 1,
                    base_price REAL NOT NULL DEFAULT 0,
                    average_entry_price REAL NOT NULL DEFAULT 0,
                    exit_price REAL NOT NULL DEFAULT 0,
                    total_invested REAL NOT NULL DEFAULT 0,
                    total_amount REAL NOT NULL DEFAULT 0,
                    gross_exit_value REAL NOT NULL DEFAULT 0,
                    net_exit_value REAL NOT NULL DEFAULT 0,
                    total_fees REAL NOT NULL DEFAULT 0,
                    safety_orders_filled INTEGER NOT NULL DEFAULT 0,
                    realized_profit REAL NOT NULL DEFAULT 0,
                    realized_profit_percent REAL NOT NULL DEFAULT 0,
                    close_reason TEXT DEFAULT '',
                    started_at TEXT NOT NULL,
                    closed_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (bot_id) REFERENCES bots(id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
            `);
            this.db.run(
                `CREATE INDEX IF NOT EXISTS idx_dca_cycles_bot_started
                 ON dca_cycles(bot_id, started_at DESC)`
            );
            // Migrate databases created by earlier versions.  Those versions
            // had authentication, but did not persist resource ownership.
            this.migrateLegacySchema();

            console.log('[DB] Tables created/verified');
        });
    }

    setEncryptionKey(key) {
        this.encryptionKey = validateMasterKey(key);
    }

    migrateLegacySchema() {
        const addColumn = (table, column, definition) => {
            this.db.run(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`, err => {
                // SQLite has no ADD COLUMN IF NOT EXISTS. A duplicate-column
                // error only means this migration was already applied.
                if (err && !err.message.includes('duplicate column name')) {
                    console.error(`[DB] Migration failed for ${table}.${column}:`, err.message);
                }
            });
        };

        addColumn('users', 'email', "TEXT NOT NULL DEFAULT ''");
        addColumn('users', 'is_active', 'INTEGER NOT NULL DEFAULT 0');
        addColumn('users', 'is_admin', 'INTEGER NOT NULL DEFAULT 0');
        addColumn('users', 'expired_at', 'TEXT DEFAULT NULL');
        addColumn('users', 'updated_at', "TEXT NOT NULL DEFAULT ''");
        addColumn('accounts', 'user_id', 'TEXT');
        addColumn('accounts', 'api_version', "TEXT NOT NULL DEFAULT 'v1'");
        addColumn('strategies', 'user_id', 'TEXT');
        addColumn('strategies', 'limit_buy_fee_percent', 'REAL NOT NULL DEFAULT 0.15');
        addColumn('strategies', 'limit_sell_fee_percent', 'REAL NOT NULL DEFAULT 0.15');
        addColumn('strategies', 'market_buy_fee_percent', 'REAL NOT NULL DEFAULT 0.30');
        addColumn('strategies', 'market_sell_fee_percent', 'REAL NOT NULL DEFAULT 0.30');
        addColumn('strategies', 'initial_entry_mode', "TEXT NOT NULL DEFAULT 'MARKET'");
        addColumn('strategies', 'step_scale_enabled', 'INTEGER NOT NULL DEFAULT 0');
        addColumn('trades', 'position_id', 'TEXT');
        addColumn('trades', 'trade_type', "TEXT NOT NULL DEFAULT ''");
        addColumn('trades', 'amount_quote', 'REAL NOT NULL DEFAULT 0');
        addColumn('trades', 'cost_basis', 'REAL NOT NULL DEFAULT 0');
        addColumn('trades', 'realized_profit', 'REAL NOT NULL DEFAULT 0');
        addColumn('trades', 'realized_profit_percent', 'REAL NOT NULL DEFAULT 0');
        addColumn('trades', 'close_reason', "TEXT DEFAULT ''");
        addColumn('trades', 'dry_run', 'INTEGER NOT NULL DEFAULT 1');
        addColumn('positions', 'sold_amount', 'REAL NOT NULL DEFAULT 0');
        addColumn('positions', 'reserved_capital', 'REAL NOT NULL DEFAULT 0');
        addColumn('positions', 'exit_order_id', 'TEXT');
        addColumn('positions', 'exit_reason', "TEXT NOT NULL DEFAULT ''");
        addColumn('orders', 'position_id', "TEXT NOT NULL DEFAULT ''");
        addColumn('orders', 'client_order_id', "TEXT NOT NULL DEFAULT ''");
        addColumn('alerts', 'telegram_notified_at', 'TEXT');
        addColumn('dca_cycles', 'telegram_notified_at', 'TEXT');
        addColumn('orders', 'telegram_notified_at', 'TEXT');
        this.db.run(
            "ALTER TABLE telegram_notifications ADD COLUMN source_key TEXT NOT NULL DEFAULT ''",
            err => {
                if (!err) {
                    // Affected installations can contain a large queue built
                    // by the old non-text source marker. Quarantine it rather
                    // than replaying stale messages after the upgrade.
                    this.db.run(`UPDATE telegram_notifications
                                 SET status='CANCELLED',
                                     last_error='Superseded by idempotent Telegram feed migration'
                                 WHERE status='PENDING'`);
                    this.db.run(`UPDATE orders
                                 SET telegram_notified_at=COALESCE(updated_at, created_at)
                                 WHERE status='FILLED'
                                   AND telegram_notified_at IS NULL`);
                } else if (!err.message.includes('duplicate column name')) {
                    console.error('[DB] Migration failed for telegram_notifications.source_key:', err.message);
                }
            }
        );
        this.db.run(`CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_notifications_source
                     ON telegram_notifications(user_id, source_key)
                     WHERE source_key<>''`);
        this.db.run('ALTER TABLE bot_logs ADD COLUMN telegram_notified_at TEXT', err => {
            if (!err) {
                // Do not replay the complete historical log when this feature
                // is first installed. Only logs created afterwards are feeds.
                this.db.run(`UPDATE bot_logs
                             SET telegram_notified_at=created_at
                             WHERE telegram_notified_at IS NULL`);
            } else if (!err.message.includes('duplicate column name')) {
                console.error('[DB] Migration failed for bot_logs.telegram_notified_at:', err.message);
            }
        });
        this.db.run("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_order_id ON orders(client_order_id) WHERE client_order_id<>''");
        this.db.run(`
            INSERT OR IGNORE INTO dca_cycles
                (id, account_id, bot_id, pair, status, dry_run, base_price,
                 average_entry_price, exit_price, total_invested, total_amount,
                 gross_exit_value, net_exit_value, total_fees,
                 safety_orders_filled, realized_profit,
                 realized_profit_percent, close_reason, started_at, closed_at,
                 updated_at)
            SELECT position_id, MIN(account_id), MIN(bot_id), MIN(pair),
                   CASE WHEN SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END)>0
                        THEN 'CLOSED' ELSE 'OPEN' END,
                   MIN(dry_run),
                   MAX(CASE WHEN trade_type='base' THEN price ELSE 0 END),
                   SUM(CASE WHEN side='buy' THEN amount_quote ELSE 0 END) /
                       NULLIF(SUM(CASE WHEN side='buy' THEN amount ELSE 0 END), 0),
                   MAX(CASE WHEN side='sell' THEN price ELSE 0 END),
                   SUM(CASE WHEN side='buy' THEN amount_quote ELSE 0 END),
                   SUM(CASE WHEN side='buy' THEN amount ELSE 0 END),
                   SUM(CASE WHEN side='sell' THEN amount_quote+fee ELSE 0 END),
                   SUM(CASE WHEN side='sell' THEN amount_quote ELSE 0 END),
                   SUM(fee),
                   SUM(CASE WHEN trade_type LIKE 'so_%' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN side='sell' THEN realized_profit ELSE 0 END),
                   SUM(CASE WHEN side='sell' THEN realized_profit_percent ELSE 0 END),
                   MAX(close_reason),
                   COALESCE(MIN(CASE WHEN side='buy' THEN executed_at END),
                            MIN(executed_at)),
                   MAX(CASE WHEN side='sell' THEN executed_at END),
                   MAX(executed_at)
            FROM trades
            WHERE position_id IS NOT NULL AND position_id<>''
            GROUP BY position_id
        `, error => {
            if (error) console.error('[DB] DCA cycle backfill failed:', error.message);
        });

        // Keep existing installations usable: legacy resources belong to
        // the oldest account, rather than being exposed to every user.
        this.db.run("UPDATE users SET updated_at = created_at WHERE updated_at = '' OR updated_at IS NULL");
        this.db.run("UPDATE users SET is_admin=1 WHERE username='admin'");
        this.db.run(`UPDATE accounts SET user_id = (SELECT id FROM users ORDER BY created_at ASC LIMIT 1) WHERE user_id IS NULL`);
        this.db.run(`UPDATE strategies SET user_id = (
            SELECT a.user_id FROM bots b JOIN accounts a ON a.id = b.account_id
            WHERE b.strategy_id = strategies.id LIMIT 1
        ) WHERE user_id IS NULL`);
        this.db.run(`UPDATE strategies SET user_id = (SELECT id FROM users ORDER BY created_at ASC LIMIT 1) WHERE user_id IS NULL`);
    }

    encrypt(text, context = '') {
        return encryptCredential(text, this.encryptionKey, context);
    }

    decrypt(text, context = '') {
        return decryptCredential(text, this.encryptionKey, context);
    }

    // Account CRUD
    addAccount(account) {
        return new Promise((resolve, reject) => {
            const now = new Date().toISOString();
            account.created_at = now;
            account.updated_at = now;
            this.db.run(
                `INSERT INTO accounts (id, user_id, name, exchange, api_version,
                    api_key_encrypted, api_secret_encrypted, is_active, created_at,
                    updated_at, last_connected_at, last_error)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                [
                    account.id, account.user_id, account.name, account.exchange,
                    account.api_version || 'v1',
                    account.api_key_encrypted || '', account.api_secret_encrypted || '',
                    account.is_active ? 1 : 0, account.created_at, account.updated_at,
                    account.last_connected_at, account.last_error
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.lastID);
                }
            );
        });
    }

    updateAccount(account) {
        return new Promise((resolve, reject) => {
            account.updated_at = new Date().toISOString();
            this.db.run(
                `UPDATE accounts SET name=?, exchange=?, api_version=?,
                    api_key_encrypted=?, api_secret_encrypted=?, is_active=?,
                    updated_at=?, last_connected_at=?, last_error=? WHERE id=?`,
                [
                    account.name, account.exchange,
                    account.api_version || 'v1',
                    account.api_key_encrypted || '', account.api_secret_encrypted || '',
                    account.is_active ? 1 : 0, account.updated_at,
                    account.last_connected_at, account.last_error, account.id
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes);
                }
            );
        });
    }

    getAccount(id) {
        return new Promise((resolve, reject) => {
            this.db.get('SELECT * FROM accounts WHERE id=?', [id], (err, row) => {
                if (err) reject(err);
                else resolve(row ? { ...row, is_active: !!row.is_active } : null);
            });
        });
    }

    getAllAccounts() {
        return new Promise((resolve, reject) => {
            this.db.all('SELECT * FROM accounts ORDER BY created_at DESC', (err, rows) => {
                if (err) reject(err);
                else resolve(rows.map(r => ({ ...r, is_active: !!r.is_active })));
            });
        });
    }

    getUserAccounts(userId, activeOnly = false) {
        return new Promise((resolve, reject) => {
            const activeClause = activeOnly ? ' AND is_active=1' : '';
            this.db.all(`SELECT * FROM accounts WHERE user_id=?${activeClause} ORDER BY created_at DESC`, [userId], (err, rows) => {
                if (err) reject(err);
                else resolve(rows.map(r => ({ ...r, is_active: !!r.is_active })));
            });
        });
    }

    getActiveAccounts() {
        return new Promise((resolve, reject) => {
            this.db.all('SELECT * FROM accounts WHERE is_active=1 ORDER BY created_at DESC', (err, rows) => {
                if (err) reject(err);
                else resolve(rows.map(r => ({ ...r, is_active: !!r.is_active })));
            });
        });
    }

    deleteAccount(id) {
        const run = (sql, params = []) => new Promise((resolve, reject) => {
            this.db.run(sql, params, function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });
        return (async () => {
            await run('BEGIN');
            try {
                await run('DELETE FROM alerts WHERE account_id=?', [id]);
                const changes = await run('DELETE FROM accounts WHERE id=?', [id]);
                await run('COMMIT');
                return changes;
            } catch (error) {
                await run('ROLLBACK').catch(() => {});
                throw error;
            }
        })();
    }

    // Bot CRUD
    addBot(bot) {
        return new Promise((resolve, reject) => {
            const now = new Date().toISOString();
            bot.created_at = now;
            bot.updated_at = now;
            this.db.run(
                `INSERT INTO bots (id, account_id, name, exchange, pair, status, dry_run,
                    strategy_id, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                [
                    bot.id, bot.account_id, bot.name, bot.exchange, bot.pair,
                    bot.status, bot.dry_run ? 1 : 0, bot.strategy_id,
                    bot.created_at, bot.updated_at
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.lastID);
                }
            );
        });
    }

    updateBot(bot) {
        return new Promise((resolve, reject) => {
            bot.updated_at = new Date().toISOString();
            this.db.run(
                `UPDATE bots SET name=?, exchange=?, pair=?, status=?, dry_run=?,
                    strategy_id=?, updated_at=? WHERE id=?`,
                [
                    bot.name, bot.exchange, bot.pair, bot.status,
                    bot.dry_run ? 1 : 0, bot.strategy_id, bot.updated_at, bot.id
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes);
                }
            );
        });
    }

    getBot(id) {
        return new Promise((resolve, reject) => {
            this.db.get('SELECT * FROM bots WHERE id=?', [id], (err, row) => {
                if (err) reject(err);
                else resolve(row ? { ...row, dry_run: !!row.dry_run } : null);
            });
        });
    }

    getAllBots() {
        return new Promise((resolve, reject) => {
            this.db.all('SELECT * FROM bots ORDER BY created_at DESC', (err, rows) => {
                if (err) reject(err);
                else resolve(rows.map(r => ({ ...r, dry_run: !!r.dry_run })));
            });
        });
    }

    getAccountBots(accountId) {
        return new Promise((resolve, reject) => {
            this.db.all('SELECT * FROM bots WHERE account_id=? ORDER BY created_at', [accountId], (err, rows) => {
                if (err) reject(err);
                else resolve(rows.map(r => ({ ...r, dry_run: !!r.dry_run })));
            });
        });
    }

    deleteBot(id) {
        const run = (sql, params = []) => new Promise((resolve, reject) => {
            this.db.run(sql, params, function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });

        // A bot owns its runtime records. Delete them atomically so foreign
        // keys cannot leave a half-deleted bot or block the user action.
        return (async () => {
            await run('BEGIN');
            try {
                await run('UPDATE alerts SET bot_id=NULL WHERE bot_id=?', [id]);
                await run('DELETE FROM positions WHERE bot_id=?', [id]);
                await run('DELETE FROM orders WHERE bot_id=?', [id]);
                await run('DELETE FROM trades WHERE bot_id=?', [id]);
                await run('DELETE FROM dca_cycles WHERE bot_id=?', [id]);
                await run('DELETE FROM bot_logs WHERE bot_id=?', [id]);
                const changes = await run('DELETE FROM bots WHERE id=?', [id]);
                await run('COMMIT');
                return changes;
            } catch (error) {
                try { await run('ROLLBACK'); } catch (_) { /* ignore rollback failure */ }
                throw error;
            }
        })();
    }

    // Strategy CRUD

    // Strategy CRUD
    addStrategy(strategy) {
        return new Promise((resolve, reject) => {
            const now = new Date().toISOString();
            strategy.created_at = now;
            strategy.updated_at = now;
            this.db.run(
                `INSERT INTO strategies (id, user_id, name, base_order_amount, safety_order_amount,
                    max_safety_orders, price_deviation, deviation_scale, volume_scale,
                    take_profit_percent, stop_loss_percent, max_position_amount,
                    cooldown_seconds, martingale_enabled, rsi_period, rsi_oversold,
                    rsi_overbought, step_scale_enabled, limit_buy_fee_percent, limit_sell_fee_percent,
                    market_buy_fee_percent, market_sell_fee_percent, initial_entry_mode, enabled, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                [
                    strategy.id, strategy.user_id, strategy.name, strategy.base_order_amount,
                    strategy.safety_order_amount, strategy.max_safety_orders,
                    strategy.price_deviation, strategy.deviation_scale, strategy.volume_scale,
                    strategy.take_profit_percent, strategy.stop_loss_percent,
                    strategy.max_position_amount || 0, strategy.cooldown_seconds || 0,
                    strategy.martingale_enabled ? 1 : 0, strategy.rsi_period,
                    strategy.rsi_oversold, strategy.rsi_overbought, strategy.step_scale_enabled ? 1 : 0,
                    strategy.limit_buy_fee_percent ?? 0.15, strategy.limit_sell_fee_percent ?? 0.15,
                    strategy.market_buy_fee_percent ?? 0.30, strategy.market_sell_fee_percent ?? 0.30,
                    strategy.initial_entry_mode || 'MARKET',
                    strategy.enabled ? 1 : 0, strategy.created_at, strategy.updated_at
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.lastID);
                }
            );
        });
    }

    getStrategy(id) {
        return new Promise((resolve, reject) => {
            this.db.get('SELECT * FROM strategies WHERE id=?', [id], (err, row) => {
                if (err) reject(err);
                else if (!row) resolve(null);
                else resolve({
                    ...row,
                    initial_entry_mode: row.initial_entry_mode || 'MARKET',
                    martingale_enabled: !!row.martingale_enabled,
                    step_scale_enabled: !!row.step_scale_enabled,
                    enabled: !!row.enabled
                });
            });
        });
    }

    getAllStrategies() {
        return new Promise((resolve, reject) => {
            this.db.all('SELECT * FROM strategies ORDER BY name', (err, rows) => {
                if (err) reject(err);
                else resolve(rows.map(r => ({
                    ...r,
                    initial_entry_mode: r.initial_entry_mode || 'MARKET',
                    martingale_enabled: !!r.martingale_enabled,
                    step_scale_enabled: !!r.step_scale_enabled,
                    enabled: !!r.enabled
                })));
            });
        });
    }

    getUserStrategies(userId) {
        return new Promise((resolve, reject) => {
            this.db.all('SELECT * FROM strategies WHERE user_id=? ORDER BY name', [userId], (err, rows) => {
                if (err) reject(err);
                else resolve(rows ? rows.map(r => ({
                    ...r,
                    initial_entry_mode: r.initial_entry_mode || 'MARKET',
                    martingale_enabled: !!r.martingale_enabled,
                    step_scale_enabled: !!r.step_scale_enabled,
                    enabled: !!r.enabled
                })) : []);
            });
        });
    }

    updateStrategy(strategy) {
        return new Promise((resolve, reject) => {
            strategy.updated_at = new Date().toISOString();
            this.db.run(
                `UPDATE strategies SET name=?, base_order_amount=?, safety_order_amount=?,
                    max_safety_orders=?, price_deviation=?, deviation_scale=?, volume_scale=?,
                    take_profit_percent=?, stop_loss_percent=?, max_position_amount=?,
                    cooldown_seconds=?, martingale_enabled=?, rsi_period=?, rsi_oversold=?,
                    rsi_overbought=?, step_scale_enabled=?, limit_buy_fee_percent=?, limit_sell_fee_percent=?,
                    market_buy_fee_percent=?, market_sell_fee_percent=?, initial_entry_mode=?, enabled=?, updated_at=? WHERE id=?`,
                [
                    strategy.name, strategy.base_order_amount, strategy.safety_order_amount,
                    strategy.max_safety_orders, strategy.price_deviation, strategy.deviation_scale,
                    strategy.volume_scale, strategy.take_profit_percent, strategy.stop_loss_percent,
                    strategy.max_position_amount || 0, strategy.cooldown_seconds || 0,
                    strategy.martingale_enabled ? 1 : 0, strategy.rsi_period,
                    strategy.rsi_oversold, strategy.rsi_overbought, strategy.step_scale_enabled ? 1 : 0,
                    strategy.limit_buy_fee_percent ?? 0.15, strategy.limit_sell_fee_percent ?? 0.15,
                    strategy.market_buy_fee_percent ?? 0.30, strategy.market_sell_fee_percent ?? 0.30,
                    strategy.initial_entry_mode || 'MARKET',
                    strategy.enabled ? 1 : 0, strategy.updated_at, strategy.id
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes);
                }
            );
        });
    }

    deleteStrategy(id) {
        return new Promise((resolve, reject) => {
            this.db.run('DELETE FROM strategies WHERE id=?', [id], function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });
    }

    // Position CRUD
    savePosition(position) {
        return new Promise((resolve, reject) => {
            position.updated_at = new Date().toISOString();
            if (!position.created_at) {
                position.created_at = position.updated_at;
            }

            // Convert arrays to JSON
            if (Array.isArray(position.so_entries)) {
                position.so_entries = JSON.stringify(position.so_entries);
            }
            if (Array.isArray(position.open_orders)) {
                position.open_orders = JSON.stringify(position.open_orders);
            }

            this.db.run(
                `INSERT OR REPLACE INTO positions
                    (id, bot_id, status, base_price, average_entry_price, base_amount,
                     total_amount, sold_amount, total_invested, reserved_capital,
                     take_profit_price, stop_loss_price,
                     current_price, so_entries, tp_order_id, exit_order_id, exit_reason, open_orders,
                     created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                [
                    position.id, position.bot_id, position.status,
                    position.base_price, position.average_entry_price, position.base_amount,
                    position.total_amount, position.sold_amount || 0,
                    position.total_invested, position.reserved_capital || 0,
                    position.take_profit_price, position.stop_loss_price,
                    position.current_price, position.so_entries, position.tp_order_id,
                    position.exit_order_id || null, position.exit_reason || '',
                    position.open_orders, position.created_at, position.updated_at
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.lastID);
                }
            );
        });
    }

    getPosition(botId) {
        return new Promise((resolve, reject) => {
            this.db.get(
                "SELECT * FROM positions WHERE bot_id=? AND status IN ('OPEN', 'PENDING_BASE') ORDER BY created_at DESC LIMIT 1",
                [botId],
                (err, row) => {
                    if (err) reject(err);
                    else if (!row) resolve(null);
                    else {
                        // Parse JSON fields
                        const position = { ...row };
                        if (typeof position.so_entries === 'string') {
                            position.so_entries = JSON.parse(position.so_entries);
                        }
                        if (typeof position.open_orders === 'string') {
                            position.open_orders = JSON.parse(position.open_orders);
                        }
                        resolve(position);
                    }
                }
            );
        });
    }

    closePosition(botId, status = 'CLOSED') {
        const run = (sql, params = []) => new Promise((resolve, reject) => {
            this.db.run(sql, params, function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });

        const now = new Date().toISOString();
        return (async () => {
            await run('BEGIN IMMEDIATE');
            try {
                const posChanges = await run("UPDATE positions SET status=?, updated_at=? WHERE bot_id=? AND status IN ('OPEN', 'PENDING_BASE')", [status, now, botId]);
                const cycleChanges = await run('UPDATE dca_cycles SET status=\'CLOSED\', close_reason=?, closed_at=?, updated_at=? WHERE bot_id=? AND status=\'OPEN\'', [status, now, now, botId]);
                const orderChanges = await run(`UPDATE orders SET status='CANCELLED', updated_at=?
                    WHERE bot_id=? AND status IN ('REQUESTED', 'SUBMISSION_UNKNOWN',
                    'OPEN', 'PENDING', 'PARTIALLY_FILLED')`, [now, botId]);
                await run('COMMIT');
                return { posChanges, cycleChanges, orderChanges };
            } catch (error) {
                await run('ROLLBACK').catch(() => {});
                throw error;
            }
        })();
    }
    // Order CRUD
    addOrder(order) {
        return new Promise((resolve, reject) => {
            order.created_at = new Date().toISOString();
            order.updated_at = order.created_at;
            this.db.run(
                `INSERT INTO orders (id, bot_id, account_id, position_id,
                    exchange_order_id, client_order_id, order_type,
                    side, pair, price, amount, amount_quote, status, is_dca, dca_level,
                    so_number, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                [
                    order.id, order.bot_id, order.account_id, order.position_id || '',
                    order.exchange_order_id, order.client_order_id || '', order.order_type,
                    order.side, order.pair, order.price, order.amount,
                    order.amount_quote, order.status, order.is_dca ? 1 : 0,
                    order.dca_level, order.so_number, order.created_at, order.updated_at
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.lastID);
                }
            );
        });
    }

    updateOrderStatus(orderId, status) {
        return new Promise((resolve, reject) => {
            this.db.run(
                'UPDATE orders SET status=?, updated_at=? WHERE id=?',
                [status, new Date().toISOString(), orderId],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes);
                }
            );
        });
    }

    updateOrderExchangeId(orderId, exchangeOrderId) {
        return new Promise((resolve, reject) => {
            this.db.run(
                'UPDATE orders SET exchange_order_id=?, updated_at=? WHERE id=?',
                [exchangeOrderId, new Date().toISOString(), orderId],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes);
                }
            );
        });
    }

    getOpenOrders(botId) {
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT * FROM orders WHERE bot_id=?
                 AND status IN ('REQUESTED', 'SUBMISSION_UNKNOWN', 'OPEN',
                                'PENDING', 'PARTIALLY_FILLED')
                 ORDER BY created_at`,
                [botId],
                (err, rows) => {
                    if (err) reject(err);
                    else resolve(rows);
                }
            );
        });
    }

    getBotOrders(botId, limit = 50) {
        return new Promise((resolve, reject) => {
            this.db.all(
                'SELECT * FROM orders WHERE bot_id=? ORDER BY created_at DESC LIMIT ?',
                [botId, limit],
                (err, rows) => {
                    if (err) reject(err);
                    else resolve(rows);
                }
            );
        });
    }

    orderExistsByExchangeId(exchangeOrderId) {
        return new Promise((resolve, reject) => {
            this.db.get(
                'SELECT COUNT(*) as count FROM orders WHERE exchange_order_id=?',
                [exchangeOrderId],
                (err, row) => {
                    if (err) reject(err);
                    else resolve(row.count > 0);
                }
            );
        });
    }

    getBotTrades(botId, limit = 200) {
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT t.*, p.status AS position_status
                 FROM trades t
                 LEFT JOIN positions p ON p.id=t.position_id
                 WHERE t.bot_id=?
                 ORDER BY COALESCE(t.executed_at, t.created_at) DESC LIMIT ?`,
                [botId, limit],
                (err, rows) => {
                    if (err) reject(err);
                    else resolve(rows.map(row => ({ ...row, dry_run: !!row.dry_run })));
                }
            );
        });
    }

    getAccountExposure(accountId) {
        return new Promise((resolve, reject) => {
            this.db.get(
                `SELECT COALESCE(SUM(
                    MAX(
                        COALESCE(p.reserved_capital, 0),
                        CASE
                            WHEN p.total_amount > 0 THEN
                                p.total_invested * MAX(
                                    p.total_amount - p.sold_amount, 0
                                ) / p.total_amount
                            ELSE COALESCE(p.total_invested, 0)
                        END
                    )
                ), 0) AS exposure
                 FROM positions p
                 JOIN bots b ON b.id=p.bot_id
                 WHERE b.account_id=? AND b.dry_run=0
                   AND p.status IN ('OPEN', 'PENDING_BASE')`,
                [accountId],
                (err, row) => {
                    if (err) reject(err);
                    else resolve(Number(row?.exposure) || 0);
                }
            );
        });
    }

    getBotTradeStats(botId) {
        return new Promise((resolve, reject) => {
            this.db.get(
                `SELECT COUNT(*) AS total_trades,
                        COALESCE(SUM(CASE WHEN side='sell' THEN realized_profit ELSE 0 END), 0)
                            AS realized_profit,
                        (SELECT COUNT(*) FROM dca_cycles WHERE bot_id=? AND status='CLOSED')
                            AS completed_cycles
                 FROM trades WHERE bot_id=?`,
                [botId, botId],
                (err, row) => {
                    if (err) reject(err);
                    else resolve(row || { total_trades: 0, realized_profit: 0, completed_cycles: 0 });
                }
            );
        });
    }

    getBotCycles(botId, limit = 100) {
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT * FROM dca_cycles WHERE bot_id=?
                 ORDER BY started_at DESC LIMIT ?`,
                [botId, limit],
                (err, rows) => {
                    if (err) reject(err);
                    else resolve(rows.map(row => ({ ...row, dry_run: !!row.dry_run })));
                }
            );
        });
    }

    getBotCycleStats(botId) {
        return new Promise((resolve, reject) => {
            this.db.get(
                `SELECT COUNT(*) AS total_cycles,
                        SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) AS closed_cycles,
                        SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_cycles,
                        SUM(CASE WHEN status='CLOSED' AND realized_profit>0 THEN 1 ELSE 0 END) AS wins,
                        SUM(CASE WHEN status='CLOSED' AND realized_profit<0 THEN 1 ELSE 0 END) AS losses,
                        COALESCE(SUM(realized_profit), 0) AS realized_profit,
                        COALESCE(SUM(total_fees), 0) AS total_fees,
                        COALESCE(AVG(CASE WHEN status='CLOSED' THEN realized_profit_percent END), 0)
                            AS average_profit_percent,
                        COALESCE(MAX(total_invested), 0) AS max_capital_used,
                        COALESCE(AVG(safety_orders_filled), 0) AS average_safety_orders,
                        COALESCE(AVG(CASE WHEN closed_at IS NOT NULL
                            THEN (julianday(closed_at)-julianday(started_at))*24 END), 0)
                            AS average_duration_hours
                 FROM dca_cycles WHERE bot_id=?`,
                [botId],
                (err, row) => {
                    if (err) reject(err);
                    else {
                        const stats = row || {};
                        const closed = Number(stats.closed_cycles) || 0;
                        const wins = Number(stats.wins) || 0;
                        resolve({
                            ...stats,
                            win_rate: closed > 0 ? (wins / closed) * 100 : 0
                        });
                    }
                }
            );
        });
    }

    getCompletedDryRunCycleCount(botId) {
        return new Promise((resolve, reject) => {
            this.db.get(
                `SELECT COUNT(*) AS count FROM dca_cycles
                 WHERE bot_id=? AND dry_run=1 AND status='CLOSED'
                   AND close_reason IN ('TAKE_PROFIT', 'STOP_LOSS')
                   AND closed_at IS NOT NULL
                   AND exit_price>0 AND total_amount>0`,
                [botId],
                (err, row) => err
                    ? reject(err)
                    : resolve(Number(row?.count) || 0)
            );
        });
    }

    resetUserData(userId) {
        const run = (sql, params = []) => new Promise((resolve, reject) => {
            this.db.run(sql, params, function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });
        const get = (sql, params = []) => new Promise((resolve, reject) => {
            this.db.get(sql, params, (err, row) => {
                if (err) reject(err);
                else resolve(row);
            });
        });

        return (async () => {
            await run('BEGIN IMMEDIATE');
            try {
                // Never erase the local record of working exchange orders.
                // The user must Stop every bot and wait for the Python worker
                // to finish cancelling TP/SO orders first.
                const unsafe = await get(
                    `SELECT COUNT(*) AS count
                     FROM bots b
                     LEFT JOIN positions p ON p.bot_id=b.id AND p.status='OPEN'
                     WHERE b.account_id IN (SELECT id FROM accounts WHERE user_id=?)
                       AND (
                           b.status <> 'STOPPED'
                           OR (
                               b.dry_run=0 AND p.id IS NOT NULL AND (
                                   COALESCE(p.tp_order_id, '') <> ''
                                   OR COALESCE(p.open_orders, '[]') NOT IN ('', '[]')
                               )
                           )
                       )`,
                    [userId]
                );
                if (Number(unsafe?.count || 0) > 0) {
                    const error = new Error(
                        'Stop semua bot dan tunggu pembatalan open order selesai sebelum Reset All.'
                    );
                    error.code = 'BOTS_NOT_SAFE_TO_RESET';
                    throw error;
                }

                const accountIds = `SELECT id FROM accounts WHERE user_id=?`;
                const botIds = `SELECT id FROM bots WHERE account_id IN (${accountIds})`;
                const counts = {};
                counts.trades = await run(`DELETE FROM trades WHERE bot_id IN (${botIds})`, [userId]);
                counts.cycles = await run(`DELETE FROM dca_cycles WHERE bot_id IN (${botIds})`, [userId]);
                counts.orders = await run(`DELETE FROM orders WHERE bot_id IN (${botIds})`, [userId]);
                counts.positions = await run(`DELETE FROM positions WHERE bot_id IN (${botIds})`, [userId]);
                counts.logs = await run(
                    `DELETE FROM bot_logs WHERE account_id IN (${accountIds})`, [userId]);
                counts.alerts = await run(
                    `DELETE FROM alerts WHERE account_id IN (${accountIds})`, [userId]);
                counts.bots = await run(`DELETE FROM bots WHERE account_id IN (${accountIds})`, [userId]);
                counts.strategies = await run('DELETE FROM strategies WHERE user_id=?', [userId]);
                counts.accounts = await run('DELETE FROM accounts WHERE user_id=?', [userId]);
                await run('COMMIT');
                return counts;
            } catch (error) {
                await run('ROLLBACK').catch(() => {});
                throw error;
            }
        })();
    }

    // Log CRUD
    addLog(accountId, level, event, message, botId = null, metadata = null) {
        return new Promise((resolve, reject) => {
            this.db.run(
                `INSERT INTO bot_logs (account_id, bot_id, level, event, message, metadata, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?)`,
                [accountId, botId, level, event, redactSensitive(message),
                    safeMetadata(metadata), new Date().toISOString()],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.lastID);
                }
            );
        });
    }

    getLogs(accountId = null, botId = null, level = null, limit = 100) {
        return new Promise((resolve, reject) => {
            let query = 'SELECT * FROM bot_logs WHERE 1=1';
            const params = [];

            if (accountId) {
                query += ' AND account_id=?';
                params.push(accountId);
            }
            if (botId) {
                query += ' AND bot_id=?';
                params.push(botId);
            }
            if (level) {
                query += ' AND level=?';
                params.push(level);
            }

            query += ' ORDER BY created_at DESC LIMIT ?';
            params.push(limit);

            this.db.all(query, params, (err, rows) => {
                if (err) reject(err);
                else resolve(rows);
            });
        });
    }

    addSystemLog(level, component, message) {
        return new Promise((resolve, reject) => {
            this.db.run(
                'INSERT INTO system_logs (level, component, message, created_at) VALUES (?, ?, ?, ?)',
                [level, component, redactSensitive(message), new Date().toISOString()],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.lastID);
                }
            );
        });
    }

    raiseAlert(alert) {
        const now = new Date().toISOString();
        const params = [
            alert.account_id || null,
            alert.bot_id || null,
            String(alert.severity || 'WARNING').toUpperCase(),
            String(alert.kind || 'OPERATIONAL').toUpperCase(),
            String(alert.dedupe_key),
            redactSensitive(alert.message || 'Operational alert'),
            safeMetadata(alert.metadata),
            now,
            now
        ];
        return new Promise((resolve, reject) => {
            this.db.run(
                `INSERT INTO alerts
                    (account_id, bot_id, severity, kind, dedupe_key, status,
                     message, metadata, occurrences, first_seen_at, last_seen_at)
                 VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, 1, ?, ?)
                 ON CONFLICT(dedupe_key) DO UPDATE SET
                    account_id=excluded.account_id,
                    bot_id=excluded.bot_id,
                    severity=excluded.severity,
                    kind=excluded.kind,
                    status='OPEN',
                    message=excluded.message,
                    metadata=excluded.metadata,
                    occurrences=alerts.occurrences + 1,
                    last_seen_at=excluded.last_seen_at,
                    acknowledged_at=NULL,
                    acknowledged_by=NULL`,
                params,
                err => {
                    if (err) return reject(err);
                    this.db.get(
                        'SELECT id FROM alerts WHERE dedupe_key=?',
                        [String(alert.dedupe_key)],
                        (selectError, row) => selectError
                            ? reject(selectError)
                            : resolve(row.id)
                    );
                }
            );
        });
    }

    resolveAlert(dedupeKey) {
        return new Promise((resolve, reject) => {
            this.db.run(
                `UPDATE alerts SET status='RESOLVED', last_seen_at=?
                 WHERE dedupe_key=? AND status='OPEN'`,
                [new Date().toISOString(), String(dedupeKey)],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes > 0);
                }
            );
        });
    }

    getUserAlerts(userId, status = 'OPEN', limit = 100, includeSystem = false) {
        const safeLimit = Math.min(Math.max(Number(limit) || 100, 1), 500);
        const allowedStatus = new Set(['OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'ALL']);
        const selectedStatus = allowedStatus.has(String(status).toUpperCase())
            ? String(status).toUpperCase()
            : 'OPEN';
        const clauses = ['(a.user_id=?' + (includeSystem ? ' OR al.account_id IS NULL)' : ')')];
        const params = [userId];
        if (selectedStatus !== 'ALL') {
            clauses.push('al.status=?');
            params.push(selectedStatus);
        }
        params.push(safeLimit);
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT al.* FROM alerts al
                 LEFT JOIN accounts a ON a.id=al.account_id
                 WHERE ${clauses.join(' AND ')}
                 ORDER BY al.last_seen_at DESC LIMIT ?`,
                params,
                (err, rows) => err ? reject(err) : resolve(rows || [])
            );
        });
    }

    acknowledgeAlert(alertId, userId, includeSystem = false) {
        return new Promise((resolve, reject) => {
            this.db.run(
                `UPDATE alerts
                 SET status='ACKNOWLEDGED', acknowledged_at=?, acknowledged_by=?
                 WHERE id=? AND status='OPEN' AND (
                    account_id IN (SELECT id FROM accounts WHERE user_id=?)
                    OR (?=1 AND account_id IS NULL)
                 )`,
                [new Date().toISOString(), userId, Number(alertId), userId,
                    includeSystem ? 1 : 0],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes > 0);
                }
            );
        });
    }

    async recordRuntimeStart(component, windowSeconds = 300, threshold = 3) {
        const now = new Date();
        const cutoff = new Date(now.getTime() - Math.max(1, windowSeconds) * 1000).toISOString();
        const retention = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();
        const run = (sql, params = []) => new Promise((resolve, reject) => {
            this.db.run(sql, params, function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });
        const get = (sql, params = []) => new Promise((resolve, reject) => {
            this.db.get(sql, params, (err, row) => err ? reject(err) : resolve(row));
        });
        await run('INSERT INTO runtime_starts (component, started_at) VALUES (?, ?)',
            [component, now.toISOString()]);
        await run('DELETE FROM runtime_starts WHERE started_at<?', [retention]);
        const row = await get(
            `SELECT COUNT(*) AS count FROM runtime_starts
             WHERE component=? AND started_at>=?`,
            [component, cutoff]
        );
        const count = Number(row?.count || 0);
        if (count >= Math.max(2, threshold)) {
            await this.raiseAlert({
                kind: 'RESTART_LOOP',
                dedupe_key: `restart:${component}`,
                severity: 'CRITICAL',
                message: `${component} started ${count} times within ${windowSeconds} seconds`,
                metadata: { component, starts_in_window: count, window_seconds: windowSeconds }
            });
        }
        return count;
    }

    // User CRUD
    addUser(user) {
        return new Promise((resolve, reject) => {
            const now = new Date().toISOString();
            user.created_at = user.created_at || now;
            user.updated_at = user.updated_at || now;
            this.db.run(
                `INSERT INTO users (id, username, email, password_hash, salt, is_active, is_admin, expired_at, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                [
                    user.id, user.username, user.email || '',
                    user.password_hash, user.salt,
                    user.is_active ? 1 : 0, user.is_admin ? 1 : 0, user.expired_at || null,
                    user.created_at, user.updated_at
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.lastID);
                }
            );
        });
    }

    getUser(id) {
        return new Promise((resolve, reject) => {
            this.db.get('SELECT * FROM users WHERE id=?', [id], (err, row) => {
                if (err) reject(err);
                else resolve(row ? { ...row, is_active: !!row.is_active, is_admin: !!row.is_admin, expired_at: row.expired_at || null } : null);
            });
        });
    }

    getUserByUsername(username) {
        return new Promise((resolve, reject) => {
            this.db.get('SELECT * FROM users WHERE username=?', [username], (err, row) => {
                if (err) reject(err);
                else resolve(row ? { ...row, is_active: !!row.is_active, is_admin: !!row.is_admin, expired_at: row.expired_at || null } : null);
            });
        });
    }

    getAllUsers() {
        return new Promise((resolve, reject) => {
            this.db.all('SELECT id, username, email, is_active, is_admin, expired_at, created_at, updated_at FROM users ORDER BY created_at DESC', (err, rows) => {
                if (err) reject(err);
                else resolve(rows.map(r => ({ ...r, is_active: !!r.is_active, is_admin: !!r.is_admin, expired_at: r.expired_at || null })));
            });
        });
    }

    updateUser(user) {
        return new Promise((resolve, reject) => {
            user.updated_at = new Date().toISOString();
            this.db.run(
                `UPDATE users SET username=?, email=?, password_hash=?, salt=?, is_active=?, is_admin=?, expired_at=?, updated_at=? WHERE id=?`,
                [
                    user.username, user.email || '',
                    user.password_hash, user.salt,
                    user.is_active ? 1 : 0, user.is_admin ? 1 : 0, user.expired_at || null,
                    user.updated_at, user.id
                ],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes);
                }
            );
        });
    }

    getLoginFailure(username) {
        return new Promise((resolve, reject) => {
            this.db.get(
                'SELECT username, failed_count, locked_until, updated_at FROM auth_login_failures WHERE username=?',
                [String(username).toLowerCase()],
                (err, row) => err ? reject(err) : resolve(row || null)
            );
        });
    }

    recordLoginFailure(username, failedCount, lockedUntil = null) {
        return new Promise((resolve, reject) => {
            this.db.run(
                `INSERT INTO auth_login_failures
                    (username, failed_count, locked_until, updated_at)
                 VALUES (?, ?, ?, ?)
                 ON CONFLICT(username) DO UPDATE SET
                    failed_count=excluded.failed_count,
                    locked_until=excluded.locked_until,
                    updated_at=excluded.updated_at`,
                [String(username).toLowerCase(), Number(failedCount), lockedUntil,
                    new Date().toISOString()],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes);
                }
            );
        });
    }

    clearLoginFailures(username) {
        return new Promise((resolve, reject) => {
            this.db.run(
                'DELETE FROM auth_login_failures WHERE username=?',
                [String(username).toLowerCase()],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes);
                }
            );
        });
    }

    addSession(session) {
        return new Promise((resolve, reject) => {
            this.db.run(
                `INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at)
                 VALUES (?, ?, ?, ?)`,
                [session.token_hash, session.user_id, session.created_at, session.expires_at],
                function (err) {
                    if (err) reject(err);
                    else resolve(this.changes);
                }
            );
        });
    }

    getSession(tokenHash) {
        return new Promise((resolve, reject) => {
            this.db.get(
                'SELECT token_hash, user_id, created_at, expires_at FROM auth_sessions WHERE token_hash=?',
                [tokenHash],
                (err, row) => err ? reject(err) : resolve(row || null)
            );
        });
    }

    deleteSession(tokenHash) {
        return new Promise((resolve, reject) => {
            this.db.run('DELETE FROM auth_sessions WHERE token_hash=?', [tokenHash], function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });
    }

    deleteUserSessions(userId) {
        return new Promise((resolve, reject) => {
            this.db.run('DELETE FROM auth_sessions WHERE user_id=?', [userId], function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });
    }

    pruneExpiredSessions(now = new Date().toISOString()) {
        return new Promise((resolve, reject) => {
            this.db.run('DELETE FROM auth_sessions WHERE expires_at<=?', [now], function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });
    }

    deleteUser(id) {
        return new Promise((resolve, reject) => {
            this.db.run('DELETE FROM users WHERE id=?', [id], function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });
    }

    async exportDatabaseJSON() {
        const queryAll = (sql, params = []) => new Promise((resolve, reject) => {
            this.db.all(sql, params, (err, rows) => {
                if (err) reject(err);
                else resolve(rows || []);
            });
        });

        const users = await queryAll('SELECT * FROM users');
        const accounts = await queryAll('SELECT * FROM accounts');
        const bots = await queryAll('SELECT * FROM bots');
        const strategies = await queryAll('SELECT * FROM strategies');
        const positions = await queryAll('SELECT * FROM positions');
        const orders = await queryAll('SELECT * FROM orders');
        const trades = await queryAll('SELECT * FROM trades');
        const dca_cycles = await queryAll('SELECT * FROM dca_cycles');

        return {
            version: "1.0",
            exported_at: new Date().toISOString(),
            app: "EXBOT DCA Manager",
            data: {
                users,
                accounts,
                bots,
                strategies,
                positions,
                orders,
                trades,
                dca_cycles
            }
        };
    }

    async importDatabaseJSON(backupObj, mode = 'merge') {
        if (!backupObj || !backupObj.data) {
            throw new Error('Format JSON backup tidak valid. Harus memiliki properti "data".');
        }

        const data = backupObj.data;
        const runSql = (sql, params = []) => new Promise((resolve, reject) => {
            this.db.run(sql, params, function (err) {
                if (err) reject(err);
                else resolve(this.changes);
            });
        });

        const tables = ['users', 'accounts', 'strategies', 'bots', 'positions', 'orders', 'trades', 'dca_cycles'];
        const counts = {};

        await runSql('BEGIN TRANSACTION');
        try {
            if (mode === 'replace') {
                for (const table of [...tables].reverse()) {
                    await runSql(`DELETE FROM ${table}`);
                }
            }

            for (const table of tables) {
                const rows = Array.isArray(data[table]) ? data[table] : [];
                counts[table] = 0;
                if (rows.length === 0) continue;

                const sample = rows[0];
                const cols = Object.keys(sample);
                const colNames = cols.join(', ');
                const placeholders = cols.map(() => '?').join(', ');
                const sql = `INSERT OR REPLACE INTO ${table} (${colNames}) VALUES (${placeholders})`;

                for (const row of rows) {
                    const params = cols.map(c => row[c]);
                    await runSql(sql, params);
                    counts[table]++;
                }
            }

            await runSql('COMMIT');
            return counts;
        } catch (err) {
            await runSql('ROLLBACK');
            throw err;
        }
    }

    // ============================================================
    // Telegram integration storage
    // ============================================================

    getAllTelegramConfigs() {
        return new Promise((resolve, reject) => {
            this.db.all(
                'SELECT * FROM telegram_config WHERE enabled=1 ORDER BY updated_at ASC',
                [],
                (error, rows) => error ? reject(error) : resolve(rows || [])
            );
        });
    }

    getPendingTelegramLinkCode(userId) {
        const now = new Date().toISOString();
        return new Promise((resolve, reject) => {
            this.db.get(
                `SELECT * FROM telegram_link_codes
                 WHERE user_id=? AND status='PENDING' AND expires_at>?
                 ORDER BY created_at DESC LIMIT 1`,
                [String(userId), now],
                (error, row) => error ? reject(error) : resolve(row || null)
            );
        });
    }

    getTelegramBindingByChat(chatId) {
        return new Promise((resolve, reject) => {
            this.db.get(
                'SELECT * FROM telegram_chat_bindings WHERE chat_id=?',
                [String(chatId)],
                (error, row) => error ? reject(error) : resolve(row || null)
            );
        });
    }

    getTelegramBindingsByUser(userId) {
        return new Promise((resolve, reject) => {
            this.db.all(
                'SELECT * FROM telegram_chat_bindings WHERE user_id=? ORDER BY linked_at ASC',
                [String(userId)],
                (error, rows) => error ? reject(error) : resolve(rows || [])
            );
        });
    }

    unbindTelegramChat(userId, chatId) {
        return new Promise((resolve, reject) => {
            this.db.run(
                'DELETE FROM telegram_chat_bindings WHERE chat_id=? AND user_id=?',
                [String(chatId), String(userId)],
                (error) => error ? reject(error) : resolve(true)
            );
        });
    }

    markTelegramNotificationFailed(id, errorMessage) {
        return new Promise((resolve, reject) => {
            this.db.run(
                `UPDATE telegram_notifications
                 SET attempts=attempts+1, last_error=?,
                     status=CASE WHEN attempts+1 >= 8 THEN 'FAILED' ELSE 'PENDING' END
                 WHERE id=?`,
                [String(errorMessage).slice(0, 500), id],
                (error) => error ? reject(error) : resolve(true)
            );
        });
    }

    markTelegramNotified(kind, id, at) {
        const column = kind === 'alert' ? 'alerts'
            : kind === 'cycle' ? 'dca_cycles'
            : kind === 'order' ? 'orders' : null;
        if (!column) return Promise.resolve(false);
        return new Promise((resolve, reject) => {
            this.db.run(
                `UPDATE ${column} SET telegram_notified_at=? WHERE id=? AND telegram_notified_at IS NULL`,
                [at, String(id)],
                (error) => error ? reject(error) : resolve(true)
            );
        });
    }

    getUnnotifiedAlertsForUser(userId, isAdmin, limit = 50) {
        const adminFilter = isAdmin
            ? "(a.account_id IS NOT NULL AND ac.user_id=?) OR (a.account_id IS NULL)"
            : "(a.account_id IS NOT NULL AND ac.user_id=?)";
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT a.* FROM alerts a
                 LEFT JOIN accounts ac ON ac.id=a.account_id
                 WHERE a.telegram_notified_at IS NULL
                   AND a.status='OPEN'
                   AND (${adminFilter})
                 ORDER BY a.last_seen_at ASC LIMIT ?`,
                [String(userId), limit],
                (error, rows) => error ? reject(error) : resolve(rows || [])
            );
        });
    }

    getUnnotifiedClosedCyclesForUser(userId, limit = 50) {
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT c.* FROM dca_cycles c
                 JOIN bots b ON b.id=c.bot_id
                 JOIN accounts ac ON ac.id=c.account_id
                 WHERE c.status='CLOSED'
                   AND c.telegram_notified_at IS NULL
                   AND ac.user_id=?
                 ORDER BY c.closed_at ASC LIMIT ?`,
                [String(userId), limit],
                (error, rows) => error ? reject(error) : resolve(rows || [])
            );
        });
    }

    getUnnotifiedBaseFillsForUser(userId, limit = 20) {
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT o.* FROM orders o
                 JOIN accounts ac ON ac.id=o.account_id
                 WHERE o.status='FILLED'
                   AND o.side='buy'
                   AND o.dca_level=0
                   AND o.telegram_notified_at IS NULL
                   AND ac.user_id=?
                 ORDER BY o.updated_at ASC LIMIT ?`,
                [String(userId), limit],
                (error, rows) => error ? reject(error) : resolve(rows || [])
            );
        });
    }

    getUnnotifiedBotEventsForUser(userId, limit = 50) {
        const events = [
            'BOT_START', 'BOT_STOP', 'BOT_PAUSE', 'BOT_ERROR',
            'ORDER_PLACED', 'ORDER_CANCELLED', 'ORDER_FAILED',
            'DCA_ENTRY', 'STOP_LOSS', 'API_ERROR', 'CONFIG_CHANGE',
            'PRICE_SIGNAL', 'RSI_SIGNAL'
        ];
        const placeholders = events.map(() => '?').join(', ');
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT l.*, b.name AS bot_name, b.pair, b.dry_run
                 FROM bot_logs l
                 JOIN accounts ac ON ac.id=l.account_id
                 LEFT JOIN bots b ON b.id=l.bot_id
                 WHERE l.telegram_notified_at IS NULL
                   AND ac.user_id=?
                   AND l.event IN (${placeholders})
                   AND (l.event<>'ORDER_PLACED' OR l.message LIKE '%TP order%')
                 ORDER BY l.created_at ASC LIMIT ?`,
                [String(userId), ...events, limit],
                (error, rows) => error ? reject(error) : resolve(rows || [])
            );
        });
    }

    getTelegramUserSummary(userId, sinceIso = null) {
        return new Promise((resolve, reject) => {
            this.db.get(
                `WITH owned_bots AS (
                    SELECT b.* FROM bots b
                    JOIN accounts ac ON ac.id=b.account_id
                    WHERE ac.user_id=?
                 ), filtered_cycles AS (
                    SELECT c.* FROM dca_cycles c
                    JOIN owned_bots b ON b.id=c.bot_id
                    WHERE (? IS NULL OR c.closed_at>=?)
                 )
                 SELECT
                    (SELECT COUNT(*) FROM filtered_cycles WHERE status='CLOSED') AS closed_cycles,
                    COALESCE((SELECT SUM(realized_profit) FROM filtered_cycles WHERE status='CLOSED'), 0) AS realized_profit,
                    COALESCE((SELECT SUM(total_fees) FROM filtered_cycles WHERE status='CLOSED'), 0) AS total_fees,
                    (SELECT COUNT(*) FROM filtered_cycles WHERE status='CLOSED' AND realized_profit>0) AS wins,
                    (SELECT COUNT(*) FROM owned_bots WHERE status='RUNNING') AS running_bots,
                    (SELECT COUNT(*) FROM owned_bots) AS total_bots,
                    COALESCE((SELECT SUM(p.total_invested) FROM positions p
                              JOIN owned_bots b ON b.id=p.bot_id
                              WHERE p.status IN ('OPEN','PENDING_BASE')), 0) AS active_capital`,
                [String(userId), sinceIso, sinceIso],
                (error, row) => error ? reject(error) : resolve(row || {})
            );
        });
    }

    hasTelegramDigest(userId, kind, periodKey) {
        return new Promise((resolve, reject) => {
            this.db.get(
                `SELECT 1 AS present FROM telegram_digest_state
                 WHERE user_id=? AND digest_kind=? AND period_key=?`,
                [String(userId), String(kind), String(periodKey)],
                (error, row) => error ? reject(error) : resolve(Boolean(row))
            );
        });
    }

    markTelegramDigest(userId, kind, periodKey) {
        return new Promise((resolve, reject) => {
            this.db.run(
                `INSERT OR IGNORE INTO telegram_digest_state
                    (user_id, digest_kind, period_key, sent_at)
                 VALUES (?, ?, ?, ?)`,
                [String(userId), String(kind), String(periodKey), new Date().toISOString()],
                error => error ? reject(error) : resolve()
            );
        });
    }

    // ============================================================
    // Telegram notification integration. The bot token is stored
    // encrypted with an account-bound credential context (the owner
    // user id, v3) and is only ever decrypted inside the Telegram
    // service. Feed getters below reuse the columns migrated by
    // migrateLegacySchema() (telegram_notified_at on alerts,
    // dca_cycles and orders).
    // ============================================================
    _telegramConfigRow(userId) {
        return new Promise((resolve, reject) => {
            this.db.get(
                'SELECT * FROM telegram_config WHERE user_id=?',
                [String(userId)],
                (error, row) => error ? reject(error) : resolve(row || null)
            );
        });
    }

    async getTelegramConfig(userId) {
        const [row, bindings] = await Promise.all([
            this._telegramConfigRow(userId),
            this.getTelegramChatBindings(userId)
        ]);
        return {
            enabled: Boolean(row && row.enabled),
            has_token: Boolean(row && row.bot_token_encrypted),
            linked_chats: bindings,
            chat_id: bindings.length ? bindings[0].chat_id : '',
        };
    }

    /**
     * Create or update the Telegram configuration for one user. When
     * `botToken` is provided it replaces the stored secret; enabling the
     * integration is refused while no usable token is known, so callers can
     * never arm an integration that could not deliver messages.
     */
    async setTelegramConfig(userId, options = {}) {
        const key = String(userId);
        const current = await this._telegramConfigRow(key);
        const hadToken = Boolean(current && current.bot_token_encrypted);

        let tokenEncrypted = current ? current.bot_token_encrypted : '';
        if (typeof options.botToken === 'string' && options.botToken.trim()) {
            const token = options.botToken.trim();
            if (!/^\d{5,16}:[A-Za-z0-9_-]{25,}$/.test(token)) {
                throw new Error('Format token Telegram tidak valid (harus format BotFather seperti 123456789:AA...)');
            }
            if (token.length > 64) {
                throw new Error('Token Telegram terlalu panjang');
            }
            tokenEncrypted = encryptCredential(token, this.encryptionKey, key);
        } else if (typeof options.enabled === 'boolean' && options.enabled && !hadToken) {
            throw new Error('Simpan token BotFather terlebih dahulu sebelum mengaktifkan notifikasi');
        }

        const now = new Date().toISOString();
        const enabledValue = typeof options.enabled === 'boolean'
            ? (options.enabled ? 1 : 0)
            : (current ? current.enabled : 0);
        await new Promise((resolve, reject) => {
            this.db.run(
                `INSERT INTO telegram_config
                     (user_id, bot_token_encrypted, enabled, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?)
                 ON CONFLICT(user_id) DO UPDATE SET
                    bot_token_encrypted=excluded.bot_token_encrypted,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at`,
                [key, tokenEncrypted, enabledValue, now, now],
                error => error ? reject(error) : resolve()
            );
        });
        return this.getTelegramConfig(key);
    }

    /** Users whose integration is armed (token present) for fan-out work. */
    listTelegramRecipientUsers() {
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT tc.user_id AS user_id, u.is_admin AS is_admin
                 FROM telegram_config tc
                 JOIN users u ON u.id=tc.user_id
                 WHERE tc.enabled=1 AND tc.bot_token_encrypted<>''`,
                [],
                (error, rows) => error ? reject(error) : resolve(rows || [])
            );
        });
    }

    /** Decrypt the stored token for the delivery worker (service-internal). */
    async getTelegramBotToken(userId) {
        const row = await this._telegramConfigRow(String(userId));
        if (!row || !row.bot_token_encrypted) return '';
        if (!this.encryptionKey) return '';
        try {
            return decryptCredential(row.bot_token_encrypted, this.encryptionKey, String(userId));
        } catch (error) {
            console.error('[DATABASE] Failed to decrypt Telegram bot token:', error.message);
            return '';
        }
    }


    // --- Chat linking ----------------------------------------------------
    static LINK_CODE_TTL_MS = 15 * 60 * 1000;
    static LINK_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';

    async createTelegramLinkCode(userId) {
        const key = String(userId);
        // A fresh generation invalidates outstanding codes: codes are bearer
        // secrets and piling them up widens the takeover window for no gain.
        await new Promise((resolve, reject) => {
            this.db.run(
                "UPDATE telegram_link_codes SET status='EXPIRED' WHERE user_id=? AND status='PENDING'",
                [key],
                error => error ? reject(error) : resolve()
            );
        });
        const codeBytes = crypto.randomBytes(8);
        const alphabet = Database.LINK_CODE_ALPHABET;
        let code = '';
        for (let i = 0; i < 8; i += 1) {
            code += alphabet[codeBytes[i] % alphabet.length];
        }
        const now = Date.now();
        const expiresAt = new Date(now + Database.LINK_CODE_TTL_MS).toISOString();
        await new Promise((resolve, reject) => {
            this.db.run(
                `INSERT INTO telegram_link_codes (code, user_id, status, expires_at, created_at)
                 VALUES (?, ?, 'PENDING', ?, ?)`,
                [code, key, expiresAt, new Date(now).toISOString()],
                error => error ? reject(error) : resolve()
            );
        });
        return { code, expires_at: expiresAt };
    }

    /** Ownership-safe validation used by the web UI before asking for /start. */
    async peekTelegramLinkCode(code, userId) {
        return new Promise((resolve, reject) => {
            this.db.get(
                'SELECT * FROM telegram_link_codes WHERE code=?',
                [String(code || '').toUpperCase()],
                (error, row) => {
                    if (error) return reject(error);
                    if (!row || row.user_id !== String(userId)) return resolve(null);
                    if (row.status !== 'PENDING' || new Date(row.expires_at).getTime() <= Date.now()) {
                        return resolve({ ...row, status: 'EXPIRED', expired: true });
                    }
                    resolve(row);
                }
            );
        });
    }

    /**
     * Bind a Telegram chat to the owner of a still-pending link code. Called
     * by the Telegram service after `/start <CODE>`; possession of the chat is
     * proven by the fact that the message arrived through the paired bot.
     */
    async consumeTelegramLinkCode(code, chatId) {
        const normalized = String(code || '').toUpperCase().trim();
        const chat = String(chatId || '').trim();
        if (!normalized || !chat) return null;
        const row = await new Promise((resolve, reject) => {
            this.db.get(
                "SELECT * FROM telegram_link_codes WHERE code=? AND status='PENDING'",
                [normalized],
                (error, found) => error ? reject(error) : resolve(found || null)
            );
        });
        if (!row) return null;
        if (new Date(row.expires_at).getTime() < Date.now()) {
            await new Promise((resolve, reject) => {
                this.db.run(
                    "UPDATE telegram_link_codes SET status='EXPIRED' WHERE code=?",
                    [normalized],
                    error => error ? reject(error) : resolve()
                );
            });
            return null;
        }
        const consumed = await new Promise((resolve, reject) => {
            this.db.run(
                "UPDATE telegram_link_codes SET status='CONSUMED' WHERE code=? AND status='PENDING' AND expires_at>?",
                [normalized, new Date().toISOString()],
                function (error) {
                    if (error) reject(error);
                    else resolve(this.changes === 1);
                }
            );
        });
        if (!consumed) return null;
        await new Promise((resolve, reject) => {
            this.db.run(
                `INSERT INTO telegram_chat_bindings (chat_id, user_id, linked_at)
                 VALUES (?, ?, ?)
                 ON CONFLICT(chat_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    linked_at=excluded.linked_at`,
                [chat, row.user_id, new Date().toISOString()],
                error => error ? reject(error) : resolve()
            );
        });
        return { user_id: row.user_id, chat_id: chat };
    }

    getTelegramChatBindings(userId) {
        return new Promise((resolve, reject) => {
            this.db.all(
                'SELECT chat_id, linked_at FROM telegram_chat_bindings WHERE user_id=?',
                [String(userId)],
                (error, rows) => error ? reject(error) : resolve(rows || [])
            );
        });
    }
    // --- Outbound queue ---------------------------------------------------
    enqueueTelegramNotification(notification) {
        const now = new Date().toISOString();
        return new Promise((resolve, reject) => {
            this.db.run(
                `INSERT OR IGNORE INTO telegram_notifications
                     (user_id, chat_id, kind, title, message, status, attempts,
                      source_key, created_at)
                 VALUES (?, '', ?, ?, ?, 'PENDING', 0, ?, ?)`,
                [
                    String(notification.userId),
                    String(notification.kind || 'INFO'),
                    redactSensitive(String(notification.title || 'XBot')),
                    redactSensitive(String(notification.message || '')),
                    String(notification.sourceKey || ''),
                    now
                ],
                function (error) {
                    if (error) reject(error);
                    else resolve(this.changes === 1 ? this.lastID : null);
                }
            );
        });
    }

    getPendingTelegramNotifications(limit = 10) {
        const safeLimit = Math.min(Math.max(Number(limit) || 10, 1), 50);
        return new Promise((resolve, reject) => {
            this.db.all(
                `SELECT * FROM telegram_notifications
                 WHERE status='PENDING' AND attempts < 5
                 ORDER BY id ASC LIMIT ?`,
                [safeLimit],
                (error, rows) => error ? reject(error) : resolve(rows || [])
            );
        });
    }

    markTelegramNotificationSent(id, chatId) {
        return new Promise((resolve, reject) => {
            this.db.run(
                `UPDATE telegram_notifications
                 SET status='SENT', chat_id=?, sent_at=?, last_error=NULL
                 WHERE id=?`,
                [String(chatId), new Date().toISOString(), Number(id)],
                error => error ? reject(error) : resolve()
            );
        });
    }

    /** Retry-with-cap bookkeeping: FAILED is terminal after MAX_ATTEMPTS tries. */
    failTelegramNotification(id, errorMessage) {
        return new Promise((resolve, reject) => {
            this.db.run(
                `UPDATE telegram_notifications
                 SET attempts=attempts+1,
                     last_error=?,
                     status=CASE WHEN attempts+1 >= 5 THEN 'FAILED' ELSE 'PENDING' END
                 WHERE id=?`,
                [redactSensitive(String(errorMessage || 'unknown error')).slice(0, 400), Number(id)],
                error => error ? reject(error) : resolve()
            );
        });
    }

    // --- Feed bookkeeping ("handed to the outbound queue") -----------------
    markAlertsNotified(ids) {
        return this._markIdsNotified('alerts', ids);
    }

    markDcaCyclesNotified(ids) {
        return this._markIdsNotified('dca_cycles', ids);
    }

    markBaseFillOrdersNotified(ids) {
        return this._markIdsNotified('orders', ids);
    }

    markBotLogsNotified(ids) {
        return this._markIdsNotified('bot_logs', ids);
    }

    _markIdsNotified(table, ids) {
        const allowed = { alerts: 1, dca_cycles: 1, orders: 1, bot_logs: 1 };
        if (!allowed[table] || !Array.isArray(ids) || ids.length === 0) {
            return Promise.resolve(false);
        }
        const placeholders = ids.map(() => '?').join(', ');
        return new Promise((resolve, reject) => {
            this.db.run(
                `UPDATE ${table} SET telegram_notified_at=? WHERE id IN (${placeholders})`,
                [new Date().toISOString(), ...ids],
                function (error) {
                    if (error) reject(error);
                    else resolve(this.changes > 0);
                }
            );
        });
    }

    close() {
        if (this.db) {
            this.db.close();
            console.log('[DB] Database connection closed');
        }
    }
}

module.exports = Database;
