"""
Database Manager for Multi-Account DCA Bot
Menggunakan SQLite sebagai database lokal
"""
import sqlite3
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from utils.redaction import redact_sensitive


def utc_now_iso() -> str:
    """Return an unambiguous UTC timestamp shared with Node's ISO dates."""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class ResilientCursor(sqlite3.Cursor):
    """Retry short-lived SQLite write contention from Node/Python workers."""
    RETRIES = 8

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        for attempt in range(self.RETRIES):
            try:
                return super().execute(sql, parameters)
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error).lower() or attempt == self.RETRIES - 1:
                    raise
                time.sleep(0.05 * (2 ** attempt))


class ResilientConnection(sqlite3.Connection):
    def cursor(self, factory: Any = ResilientCursor) -> sqlite3.Cursor:
        return super().cursor(factory)


class DatabaseManager:
    def __init__(self, db_path: str = "data/dca_bot.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else '.', exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self.conn is None:
            raise RuntimeError("Database not connected")
        return self.conn

    def connect(self):
        """Connect to database and ensure tables exist"""
        # Node dashboard and multiple Python workers share this file. WAL and
        # a busy timeout avoid failing a trade merely because another process
        # is committing a short transaction.
        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30,
            factory=ResilientConnection,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_tables()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _ensure_tables(self):
        """Create all required tables if they don't exist"""
        cursor = self.connection.cursor()

        # Users table
        cursor.execute("""
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
        """)

        # Accounts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'Indodax',
                api_version TEXT NOT NULL DEFAULT 'v1',
                api_key_encrypted TEXT NOT NULL DEFAULT '',
                api_secret_encrypted TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_connected_at TEXT,
                last_error TEXT
            )
        """)

        # Bots table
        cursor.execute("""
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
        """)

        # Strategies table
        cursor.execute("""
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
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Keep databases created before fee-aware strategies compatible.
        for column, definition in (
            ('limit_buy_fee_percent', 'REAL NOT NULL DEFAULT 0.15'),
            ('limit_sell_fee_percent', 'REAL NOT NULL DEFAULT 0.15'),
            ('market_buy_fee_percent', 'REAL NOT NULL DEFAULT 0.30'),
            ('market_sell_fee_percent', 'REAL NOT NULL DEFAULT 0.30'),
            ('step_scale_enabled', 'INTEGER NOT NULL DEFAULT 0'),
            ('initial_entry_mode', "TEXT NOT NULL DEFAULT 'MARKET'"),
        ):
            try:
                cursor.execute(f'ALTER TABLE strategies ADD COLUMN {column} {definition}')
            except sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    raise

        for column, definition in (
            ('user_id', "TEXT NOT NULL DEFAULT ''"),
            ('api_version', "TEXT NOT NULL DEFAULT 'v1'"),
        ):
            try:
                cursor.execute(f'ALTER TABLE accounts ADD COLUMN {column} {definition}')
            except sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    pass

        for column, definition in (
            ('expired_at', 'TEXT DEFAULT NULL'),
            ('is_active', 'INTEGER NOT NULL DEFAULT 0'),
            ('is_admin', 'INTEGER NOT NULL DEFAULT 0'),
        ):
            try:
                cursor.execute(f'ALTER TABLE users ADD COLUMN {column} {definition}')
            except sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    pass
        cursor.execute("UPDATE users SET is_admin=1 WHERE username='admin'")

        # One row represents a complete BO -> SO(s) -> TP/SL lifecycle.
        cursor.execute("""
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
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_dca_cycles_bot_started "
            "ON dca_cycles(bot_id, started_at DESC)"
        )
        # Positions table
        cursor.execute("""
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
        """)

        # Orders table
        cursor.execute("""
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
        """)

        # Trades table
        cursor.execute("""
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
        """)
        try:
            cursor.execute('ALTER TABLE positions ADD COLUMN sold_amount REAL NOT NULL DEFAULT 0')
        except sqlite3.OperationalError as error:
            if 'duplicate column name' not in str(error).lower():
                raise
        try:
            cursor.execute(
                'ALTER TABLE positions ADD COLUMN reserved_capital '
                'REAL NOT NULL DEFAULT 0')
        except sqlite3.OperationalError as error:
            if 'duplicate column name' not in str(error).lower():
                raise
        for column, definition in (
            ('position_id', "TEXT NOT NULL DEFAULT ''"),
            ('client_order_id', "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                cursor.execute(f'ALTER TABLE orders ADD COLUMN {column} {definition}')
            except sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    raise
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_order_id "
            "ON orders(client_order_id) WHERE client_order_id<>''"
        )
        for column, definition in (
            ('exit_order_id', 'TEXT'),
            ('exit_reason', "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                cursor.execute(f'ALTER TABLE positions ADD COLUMN {column} {definition}')
            except sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    raise
        for column, definition in (
            ('position_id', 'TEXT'),
            ('trade_type', "TEXT NOT NULL DEFAULT ''"),
            ('amount_quote', 'REAL NOT NULL DEFAULT 0'),
            ('cost_basis', 'REAL NOT NULL DEFAULT 0'),
            ('realized_profit', 'REAL NOT NULL DEFAULT 0'),
            ('realized_profit_percent', 'REAL NOT NULL DEFAULT 0'),
            ('close_reason', "TEXT DEFAULT ''"),
            ('dry_run', 'INTEGER NOT NULL DEFAULT 1'),
        ):
            try:
                cursor.execute(f'ALTER TABLE trades ADD COLUMN {column} {definition}')
            except sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    raise

        # Backfill cycles for trade-ledger rows created before dca_cycles was
        # introduced. This is intentionally after the trade-column migration.
        cursor.execute("""
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
                   MAX(CASE WHEN trade_type LIKE 'base%' THEN price ELSE 0 END),
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
        """)

        # Bot logs table
        cursor.execute("""
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
        """)
        try:
            cursor.execute(
                'ALTER TABLE bot_logs ADD COLUMN telegram_notified_at TEXT')
            cursor.execute("""
                UPDATE bot_logs SET telegram_notified_at=created_at
                WHERE telegram_notified_at IS NULL
            """)
        except sqlite3.OperationalError as error:
            if 'duplicate column name' not in str(error).lower():
                raise

        # System logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL DEFAULT 'INFO',
                component TEXT,
                message TEXT,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
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
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_alerts_status_last_seen
            ON alerts(status, last_seen_at DESC)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS runtime_starts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                component TEXT NOT NULL,
                started_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_runtime_starts_component_time
            ON runtime_starts(component, started_at DESC)
        """)

        self.connection.commit()

    # ============================================================
    # Account CRUD
    # ============================================================
    def add_account(self, account_dict: dict) -> str:
        cursor = self.connection.cursor()
        now = utc_now_iso()
        account_dict.setdefault('user_id', '')
        account_dict.setdefault('api_version', 'v1')
        account_dict['created_at'] = now
        account_dict['updated_at'] = now
        cursor.execute("""
            INSERT INTO accounts (id, user_id, name, exchange, api_version,
                                  api_key_encrypted, api_secret_encrypted, is_active,
                                  created_at, updated_at, last_connected_at, last_error)
            VALUES (:id, :user_id, :name, :exchange, :api_version,
                    :api_key_encrypted, :api_secret_encrypted, :is_active,
                    :created_at, :updated_at, :last_connected_at, :last_error)
        """, account_dict)
        self.connection.commit()
        return account_dict['id']

    def update_account(self, account_dict: dict):
        cursor = self.connection.cursor()
        account_dict.setdefault('user_id', '')
        account_dict.setdefault('api_version', 'v1')
        account_dict['updated_at'] = utc_now_iso()
        cursor.execute("""
            UPDATE accounts SET user_id=:user_id, name=:name, exchange=:exchange,
                api_version=:api_version, api_key_encrypted=:api_key_encrypted,
                api_secret_encrypted=:api_secret_encrypted,
                is_active=:is_active, updated_at=:updated_at,
                last_connected_at=:last_connected_at, last_error=:last_error
            WHERE id=:id
        """, account_dict)
        self.connection.commit()

    def get_account(self, account_id: str) -> Optional[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM accounts WHERE id=?", (account_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def get_all_accounts(self) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM accounts ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def get_active_accounts(self) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM accounts WHERE is_active=1 ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def delete_account(self, account_id: str):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM alerts WHERE account_id=?", (account_id,))
        cursor.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        self.connection.commit()

    # ============================================================
    # Bot CRUD
    # ============================================================
    def add_bot(self, bot_dict: dict) -> str:
        cursor = self.connection.cursor()
        now = utc_now_iso()
        bot_dict['created_at'] = now
        bot_dict['updated_at'] = now
        cursor.execute("""
            INSERT INTO bots (id, account_id, name, exchange, pair, status, dry_run,
                              strategy_id, created_at, updated_at)
            VALUES (:id, :account_id, :name, :exchange, :pair, :status, :dry_run,
                    :strategy_id, :created_at, :updated_at)
        """, bot_dict)
        self.connection.commit()
        return bot_dict['id']

    def update_bot(self, bot_dict: dict):
        cursor = self.connection.cursor()
        bot_dict['updated_at'] = utc_now_iso()
        cursor.execute("""
            UPDATE bots SET name=:name, exchange=:exchange, pair=:pair,
                status=:status, dry_run=:dry_run, strategy_id=:strategy_id,
                updated_at=:updated_at
            WHERE id=:id
        """, bot_dict)
        self.connection.commit()

    def get_bot(self, bot_id: str) -> Optional[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM bots WHERE id=?", (bot_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def get_account_bots(self, account_id: str) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM bots WHERE account_id=? ORDER BY created_at", (account_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_all_bots(self) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM bots ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def get_active_bots(self) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM bots WHERE status='RUNNING' ORDER BY created_at")
        return [dict(row) for row in cursor.fetchall()]

    def delete_bot(self, bot_id: str):
        cursor = self.connection.cursor()
        cursor.execute("UPDATE alerts SET bot_id=NULL WHERE bot_id=?", (bot_id,))
        cursor.execute("DELETE FROM bots WHERE id=?", (bot_id,))
        self.connection.commit()

    # ============================================================
    # Strategy CRUD
    # ============================================================
    def add_strategy(self, strategy_dict: dict) -> str:
        cursor = self.connection.cursor()
        now = utc_now_iso()
        strategy_dict.setdefault('step_scale_enabled', 0)
        strategy_dict.setdefault('limit_buy_fee_percent', 0.15)
        strategy_dict.setdefault('limit_sell_fee_percent', 0.15)
        strategy_dict.setdefault('market_buy_fee_percent', 0.30)
        strategy_dict.setdefault('market_sell_fee_percent', 0.30)
        strategy_dict.setdefault('initial_entry_mode', 'MARKET')
        strategy_dict['created_at'] = now
        strategy_dict['updated_at'] = now
        cursor.execute("""
            INSERT INTO strategies (id, name, base_order_amount, safety_order_amount,
                max_safety_orders, price_deviation, deviation_scale, volume_scale,
                take_profit_percent, stop_loss_percent, max_position_amount,
                cooldown_seconds, martingale_enabled, rsi_period, rsi_oversold,
                rsi_overbought, step_scale_enabled, limit_buy_fee_percent,
                limit_sell_fee_percent, market_buy_fee_percent, market_sell_fee_percent,
                initial_entry_mode, enabled, created_at, updated_at)
            VALUES (:id, :name, :base_order_amount, :safety_order_amount,
                :max_safety_orders, :price_deviation, :deviation_scale, :volume_scale,
                :take_profit_percent, :stop_loss_percent, :max_position_amount,
                :cooldown_seconds, :martingale_enabled, :rsi_period, :rsi_oversold,
                :rsi_overbought, :step_scale_enabled, :limit_buy_fee_percent,
                :limit_sell_fee_percent, :market_buy_fee_percent, :market_sell_fee_percent,
                :initial_entry_mode, :enabled, :created_at, :updated_at)
        """, strategy_dict)
        self.connection.commit()
        return strategy_dict['id']

    def get_strategy(self, strategy_id: str) -> Optional[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM strategies WHERE id=?", (strategy_id,))
        row = cursor.fetchone()
        if row:
            res = dict(row)
            res.setdefault('initial_entry_mode', 'MARKET')
            return res
        return None

    def get_all_strategies(self) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM strategies ORDER BY name")
        result = []
        for row in cursor.fetchall():
            res = dict(row)
            res.setdefault('initial_entry_mode', 'MARKET')
            result.append(res)
        return result

    def update_strategy(self, strategy_dict: dict):
        cursor = self.connection.cursor()
        strategy_dict.setdefault('step_scale_enabled', 0)
        strategy_dict.setdefault('limit_buy_fee_percent', 0.15)
        strategy_dict.setdefault('limit_sell_fee_percent', 0.15)
        strategy_dict.setdefault('market_buy_fee_percent', 0.30)
        strategy_dict.setdefault('market_sell_fee_percent', 0.30)
        strategy_dict.setdefault('initial_entry_mode', 'MARKET')
        strategy_dict['updated_at'] = utc_now_iso()
        cursor.execute("""
            UPDATE strategies SET name=:name, base_order_amount=:base_order_amount,
                safety_order_amount=:safety_order_amount,
                max_safety_orders=:max_safety_orders,
                price_deviation=:price_deviation, deviation_scale=:deviation_scale,
                volume_scale=:volume_scale,
                take_profit_percent=:take_profit_percent,
                stop_loss_percent=:stop_loss_percent,
                max_position_amount=:max_position_amount,
                cooldown_seconds=:cooldown_seconds,
                martingale_enabled=:martingale_enabled,
                rsi_period=:rsi_period, rsi_oversold=:rsi_oversold,
                rsi_overbought=:rsi_overbought,
                step_scale_enabled=:step_scale_enabled,
                limit_buy_fee_percent=:limit_buy_fee_percent,
                limit_sell_fee_percent=:limit_sell_fee_percent,
                market_buy_fee_percent=:market_buy_fee_percent,
                market_sell_fee_percent=:market_sell_fee_percent,
                initial_entry_mode=:initial_entry_mode,
                enabled=:enabled, updated_at=:updated_at
            WHERE id=:id
        """, strategy_dict)
        self.connection.commit()

    def get_user(self, user_id: str) -> Optional[dict]:
        """Get user by ID"""
        if not self.connection:
            return None
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_strategy(self, strategy_id: str):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM strategies WHERE id=?", (strategy_id,))
        self.connection.commit()

    # ============================================================
    # Position CRUD
    # ============================================================
    def save_position(self, position_dict: dict):
        cursor = self.connection.cursor()
        now = utc_now_iso()
        position_dict['updated_at'] = now
        if not position_dict.get('created_at'):
            position_dict['created_at'] = now

        # Serialize a copy so the worker keeps mutable lists while it places
        # TP/SO orders and persists after every successful step.
        stored_position = dict(position_dict)
        stored_position.setdefault('sold_amount', 0)
        stored_position.setdefault('reserved_capital', 0)
        stored_position.setdefault('exit_order_id', None)
        stored_position.setdefault('exit_reason', '')

        # Convert lists to JSON
        if isinstance(stored_position.get('so_entries'), list):
            stored_position['so_entries'] = json.dumps(stored_position['so_entries'])
        if isinstance(stored_position.get('open_orders'), list):
            stored_position['open_orders'] = json.dumps(stored_position['open_orders'])

        cursor.execute("""
            INSERT OR REPLACE INTO positions
                (id, bot_id, status, base_price, average_entry_price, base_amount,
                 total_amount, sold_amount, total_invested, reserved_capital,
                 take_profit_price, stop_loss_price,
                 current_price, so_entries, tp_order_id, exit_order_id, exit_reason, open_orders,
                 created_at, updated_at)
            VALUES (:id, :bot_id, :status, :base_price, :average_entry_price, :base_amount,
                    :total_amount, :sold_amount, :total_invested, :reserved_capital,
                    :take_profit_price, :stop_loss_price,
                    :current_price, :so_entries, :tp_order_id, :exit_order_id, :exit_reason, :open_orders,
                    :created_at, :updated_at)
        """, stored_position)
        self.connection.commit()

    def get_account_exposure(self, account_id: str) -> float:
        """Return conservative live exposure reserved by active DCA cycles."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(
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
              AND p.status IN ('OPEN', 'PENDING_BASE')
        """, (str(account_id),))
        row = cursor.fetchone()
        return float(row['exposure'] or 0) if row else 0.0

    def get_position(self, bot_id: str) -> Optional[dict]:
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT * FROM positions WHERE bot_id=? "
            "AND status IN ('OPEN', 'PENDING_BASE') "
            "ORDER BY created_at DESC LIMIT 1", (bot_id,))
        row = cursor.fetchone()
        if row:
            position = dict(row)
            if isinstance(position.get('so_entries'), str):
                position['so_entries'] = json.loads(position['so_entries'])
            if isinstance(position.get('open_orders'), str):
                position['open_orders'] = json.loads(position['open_orders'])
            return position
        return None

    def close_position(self, bot_id: str, status: str = 'CLOSED'):
        cursor = self.connection.cursor()
        now = utc_now_iso()
        cursor.execute("""
            UPDATE positions SET status=?, updated_at=? WHERE bot_id=? AND status IN ('OPEN', 'PENDING_BASE')
        """, (status, now, bot_id))
        cursor.execute("""
            UPDATE dca_cycles SET status='CLOSED', close_reason=?, closed_at=?, updated_at=? WHERE bot_id=? AND status='OPEN'
        """, (status, now, now, bot_id))
        cursor.execute("""
            UPDATE orders SET status='CANCELLED', updated_at=? WHERE bot_id=?
            AND status IN ('REQUESTED', 'SUBMISSION_UNKNOWN', 'OPEN',
                           'PENDING', 'PARTIALLY_FILLED')
        """, (now, bot_id))
        self.connection.commit()

    # ============================================================
    # Order CRUD
    # ============================================================
    def add_order(self, order_dict: dict) -> str:
        cursor = self.connection.cursor()
        now = utc_now_iso()
        order = {
            'id': order_dict['id'],
            'bot_id': order_dict['bot_id'],
            'account_id': order_dict['account_id'],
            'position_id': str(order_dict.get('position_id', '')),
            'exchange_order_id': str(order_dict.get('exchange_order_id', '')),
            'client_order_id': str(order_dict.get('client_order_id', '')),
            'order_type': order_dict.get('order_type', 'limit'),
            'side': order_dict.get('side', ''),
            'pair': order_dict.get('pair', 'btcidr'),
            'price': float(order_dict.get('price', 0) or 0),
            'amount': float(order_dict.get('amount', 0) or 0),
            'amount_quote': float(order_dict.get('amount_quote', 0) or 0),
            'status': order_dict.get('status', 'OPEN'),
            'is_dca': 1 if order_dict.get('is_dca', True) else 0,
            'dca_level': int(order_dict.get('dca_level', 0) or 0),
            'so_number': int(order_dict.get('so_number', 0) or 0),
            'created_at': order_dict.get('created_at') or now,
            'updated_at': now,
        }
        cursor.execute("""
            INSERT OR IGNORE INTO orders (id, bot_id, account_id, position_id,
                exchange_order_id, client_order_id, order_type,
                side, pair, price, amount, amount_quote, status, is_dca, dca_level,
                so_number, created_at, updated_at)
            VALUES (:id, :bot_id, :account_id, :position_id, :exchange_order_id,
                :client_order_id, :order_type,
                :side, :pair, :price, :amount, :amount_quote, :status, :is_dca,
                :dca_level, :so_number, :created_at, :updated_at)
        """, order)
        self.connection.commit()
        return str(order['id'])

    def update_order_status(self, order_id: str, status: str):
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE orders SET status=?, updated_at=? WHERE id=?
        """, (status, utc_now_iso(), order_id))
        self.connection.commit()

    def update_order_status_by_exchange_id(self, exchange_order_id: str, status: str):
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE orders SET status=?, updated_at=? WHERE exchange_order_id=?
        """, (status, utc_now_iso(), str(exchange_order_id)))
        self.connection.commit()

    def update_order_exchange_id(self, order_id: str, exchange_order_id: str):
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE orders SET exchange_order_id=?, updated_at=? WHERE id=?
        """, (exchange_order_id, utc_now_iso(), order_id))
        self.connection.commit()

    def get_open_orders(self, bot_id: str) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM orders WHERE bot_id=?
            AND status IN ('REQUESTED', 'SUBMISSION_UNKNOWN', 'OPEN',
                           'PENDING', 'PARTIALLY_FILLED')
            ORDER BY created_at
        """, (bot_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_bot_orders(self, bot_id: str, limit: int = 50) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM orders WHERE bot_id=? ORDER BY created_at DESC LIMIT ?
        """, (bot_id, limit))
        return [dict(row) for row in cursor.fetchall()]

    def order_exists_by_exchange_id(self, exchange_order_id: str) -> bool:
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM orders WHERE exchange_order_id=?", (exchange_order_id,))
        return cursor.fetchone()[0] > 0

    def get_order_trade_totals(self, position_id: str, order_id: str,
                               side: str) -> dict:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS amount,
                   COALESCE(SUM(amount_quote), 0) AS amount_quote,
                   COALESCE(SUM(fee), 0) AS fee,
                   COALESCE(SUM(cost_basis), 0) AS cost_basis,
                   COALESCE(SUM(realized_profit), 0) AS realized_profit
            FROM trades WHERE position_id=? AND order_id=? AND side=?
        """, (position_id, str(order_id), side))
        return dict(cursor.fetchone())

    def sync_cycle_safety_order_count(self, position_id: str, count: int):
        """Set the finalized SO count without incrementing it on retries."""
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE dca_cycles SET safety_orders_filled=?, updated_at=? WHERE id=?
        """, (max(int(count), 0), utc_now_iso(), position_id))
        self.connection.commit()

    def update_order_submission(self, order_id: str, exchange_order_id: str,
                                status: str):
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE orders SET exchange_order_id=?, status=?, updated_at=? WHERE id=?
        """, (str(exchange_order_id), status, utc_now_iso(), order_id))
        self.connection.commit()

    def get_order_by_client_id(self, client_order_id: str) -> Optional[dict]:
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT * FROM orders WHERE client_order_id=? LIMIT 1",
            (str(client_order_id),),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_pending_base_order(self, bot_id: str,
                               position_id: str = '') -> Optional[dict]:
        cursor = self.connection.cursor()
        sql = ("SELECT * FROM orders WHERE bot_id=? "
               "AND order_type IN ('base_limit', 'base_market') "
               "AND status IN ('REQUESTED', 'SUBMISSION_UNKNOWN', 'OPEN', "
               "'PARTIALLY_FILLED', 'CANCELLED', 'CANCELED')")
        params: list[Any] = [bot_id]
        if position_id:
            sql += " AND position_id=?"
            params.append(position_id)
        sql += " ORDER BY created_at DESC LIMIT 1"
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_recoverable_order(self, position_id: str, order_type: str,
                              so_number: int = 0) -> Optional[dict]:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM orders
            WHERE position_id=? AND order_type=? AND so_number=?
              AND status IN ('REQUESTED', 'SUBMISSION_UNKNOWN', 'OPEN',
                             'PARTIALLY_FILLED')
            ORDER BY created_at DESC LIMIT 1
        """, (str(position_id), str(order_type), int(so_number)))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_recoverable_child_orders(self, position_id: str) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM orders
            WHERE position_id=?
              AND (order_type='take_profit' OR order_type LIKE 'so_%')
              AND status IN ('REQUESTED', 'SUBMISSION_UNKNOWN', 'OPEN',
                             'PARTIALLY_FILLED')
            ORDER BY created_at
        """, (str(position_id),))
        return [dict(row) for row in cursor.fetchall()]

    def get_child_orders_for_reconciliation(self,
                                            position_id: str) -> list[dict]:
        """Include terminal children that may have committed before inventory."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM orders
            WHERE position_id=?
              AND (order_type='take_profit' OR order_type LIKE 'so_%')
              AND status IN ('REQUESTED', 'SUBMISSION_UNKNOWN', 'OPEN',
                             'PARTIALLY_FILLED', 'FILLED', 'CANCELLED',
                             'CANCELED')
            ORDER BY created_at
        """, (str(position_id),))
        return [dict(row) for row in cursor.fetchall()]

    # ============================================================
    # Trade ledger
    # ============================================================
    def add_trade(self, trade_dict: dict) -> str:
        """Persist one filled execution. Placed/cancelled orders do not belong here."""
        import uuid

        now = utc_now_iso()
        trade = {
            'id': trade_dict.get('id') or f"trade_{uuid.uuid4().hex}",
            'account_id': trade_dict['account_id'],
            'bot_id': trade_dict['bot_id'],
            'position_id': trade_dict.get('position_id'),
            'order_id': trade_dict.get('order_id'),
            'exchange_trade_id': trade_dict.get('exchange_trade_id'),
            'pair': trade_dict.get('pair', 'btcidr'),
            'side': trade_dict.get('side', ''),
            'trade_type': trade_dict.get('trade_type', ''),
            'price': float(trade_dict.get('price', 0) or 0),
            'amount': float(trade_dict.get('amount', 0) or 0),
            'amount_quote': float(trade_dict.get('amount_quote', 0) or 0),
            'fee': float(trade_dict.get('fee', 0) or 0),
            'fee_currency': trade_dict.get('fee_currency', 'IDR'),
            'cost_basis': float(trade_dict.get('cost_basis', 0) or 0),
            'realized_profit': float(trade_dict.get('realized_profit', 0) or 0),
            'realized_profit_percent': float(
                trade_dict.get('realized_profit_percent', 0) or 0),
            'close_reason': trade_dict.get('close_reason', ''),
            'dry_run': 1 if trade_dict.get('dry_run', True) else 0,
            'executed_at': trade_dict.get('executed_at') or now,
            'created_at': trade_dict.get('created_at') or now,
        }
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO trades
                (id, account_id, bot_id, position_id, order_id, exchange_trade_id,
                 pair, side, trade_type, price, amount, amount_quote, fee,
                 fee_currency, cost_basis, realized_profit,
                 realized_profit_percent, close_reason, dry_run, executed_at,
                 created_at)
            VALUES
                (:id, :account_id, :bot_id, :position_id, :order_id,
                 :exchange_trade_id, :pair, :side, :trade_type, :price,
                 :amount, :amount_quote, :fee, :fee_currency, :cost_basis,
                 :realized_profit, :realized_profit_percent, :close_reason,
                 :dry_run, :executed_at, :created_at)
        """, trade)
        trade_inserted = cursor.rowcount > 0
        if trade_inserted and trade['position_id']:
            cursor.execute("""
                INSERT OR IGNORE INTO dca_cycles
                    (id, account_id, bot_id, pair, status, dry_run, base_price,
                     started_at, updated_at)
                VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
            """, (
                trade['position_id'], trade['account_id'], trade['bot_id'],
                trade['pair'], trade['dry_run'],
                trade['price'] if trade['trade_type'].startswith('base') else 0,
                trade['executed_at'], trade['executed_at'],
            ))
            if trade['side'] == 'buy':
                cursor.execute("""
                    UPDATE dca_cycles
                    SET base_price=CASE WHEN ? LIKE 'base%' THEN ? ELSE base_price END,
                        total_invested=total_invested+?,
                        total_amount=total_amount+?,
                        total_fees=total_fees+?,
                        safety_orders_filled=safety_orders_filled+
                            CASE WHEN ? LIKE 'so_%' THEN 1 ELSE 0 END,
                        average_entry_price=
                            (total_invested+?)/NULLIF(total_amount+?, 0),
                        updated_at=?
                    WHERE id=?
                """, (
                    trade['trade_type'], trade['price'], trade['amount_quote'],
                    trade['amount'], trade['fee'], trade['trade_type'],
                    trade['amount_quote'], trade['amount'],
                    trade['executed_at'], trade['position_id'],
                ))
            elif trade['side'] == 'sell':
                gross_exit = float(trade['amount_quote']) + float(trade['fee'])
                cursor.execute("""
                    UPDATE dca_cycles
                    SET status=CASE WHEN ?<>'' THEN 'CLOSED' ELSE status END,
                        exit_price=?,
                        gross_exit_value=gross_exit_value+?,
                        net_exit_value=net_exit_value+?,
                        total_fees=total_fees+?,
                        realized_profit=realized_profit+?,
                        realized_profit_percent=CASE WHEN ?<>''
                            THEN COALESCE((realized_profit+?)/NULLIF(total_invested, 0)*100, 0)
                            ELSE realized_profit_percent END,
                        close_reason=CASE WHEN ?<>'' THEN ? ELSE close_reason END,
                        closed_at=CASE WHEN ?<>'' THEN ? ELSE closed_at END,
                        updated_at=?
                    WHERE id=?
                """, (
                    trade['close_reason'], trade['price'], gross_exit,
                    trade['amount_quote'], trade['fee'], trade['realized_profit'],
                    trade['close_reason'], trade['realized_profit'],
                    trade['close_reason'], trade['close_reason'],
                    trade['close_reason'], trade['executed_at'], trade['executed_at'],
                    trade['position_id'],
                ))
        self.connection.commit()
        return str(trade['id'])

    def get_bot_trades(self, bot_id: str, limit: int = 200) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT t.*, p.status AS position_status
            FROM trades t
            LEFT JOIN positions p ON p.id=t.position_id
            WHERE t.bot_id=?
            ORDER BY COALESCE(t.executed_at, t.created_at) DESC LIMIT ?
        """, (bot_id, limit))
        return [dict(row) for row in cursor.fetchall()]

    def get_bot_trade_stats(self, bot_id: str) -> dict:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT COUNT(*) AS total_trades,
                   COALESCE(SUM(CASE WHEN side='sell' THEN realized_profit ELSE 0 END), 0)
                       AS realized_profit,
                   (SELECT COUNT(*) FROM dca_cycles WHERE bot_id=? AND status='CLOSED')
                       AS completed_cycles
            FROM trades WHERE bot_id=?
        """, (bot_id, bot_id))
        row = cursor.fetchone()
        return dict(row) if row else {'total_trades': 0, 'realized_profit': 0, 'completed_cycles': 0}

    def get_bot_cycles(self, bot_id: str, limit: int = 100) -> list[dict]:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM dca_cycles WHERE bot_id=?
            ORDER BY started_at DESC LIMIT ?
        """, (bot_id, limit))
        return [dict(row) for row in cursor.fetchall()]

    def get_cycle(self, cycle_id: str) -> Optional[dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM dca_cycles WHERE id=?", (str(cycle_id),))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_bot_cycle_stats(self, bot_id: str) -> dict:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT COUNT(*) AS total_cycles,
                   SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) AS closed_cycles,
                   SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_cycles,
                   SUM(CASE WHEN status='CLOSED' AND realized_profit>0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN status='CLOSED' AND realized_profit<0 THEN 1 ELSE 0 END) AS losses,
                   COALESCE(SUM(realized_profit), 0) AS realized_profit,
                   COALESCE(SUM(total_fees), 0) AS total_fees,
                   COALESCE(AVG(CASE WHEN status='CLOSED' THEN realized_profit_percent END), 0)
                       AS average_profit_percent,
                   COALESCE(MAX(total_invested), 0) AS max_capital_used,
                   COALESCE(AVG(safety_orders_filled), 0) AS average_safety_orders
            FROM dca_cycles WHERE bot_id=?
        """, (bot_id,))
        row = cursor.fetchone()
        if not row:
            return {
                'total_cycles': 0, 'closed_cycles': 0, 'open_cycles': 0,
                'wins': 0, 'losses': 0, 'realized_profit': 0, 'total_fees': 0,
                'average_profit_percent': 0, 'max_capital_used': 0,
                'average_safety_orders': 0, 'win_rate': 0
            }
        stats = dict(row)
        closed = float(stats.get('closed_cycles') or 0)
        wins = float(stats.get('wins') or 0)
        stats['win_rate'] = (wins / closed * 100) if closed > 0 else 0
        return stats

    def get_completed_dry_run_cycle_count(self, bot_id: str) -> int:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT COUNT(*) AS count FROM dca_cycles
            WHERE bot_id=? AND dry_run=1 AND status='CLOSED'
              AND close_reason IN ('TAKE_PROFIT', 'STOP_LOSS')
              AND closed_at IS NOT NULL
              AND exit_price>0 AND total_amount>0
        """, (bot_id,))
        return int(cursor.fetchone()['count'])

    # ============================================================
    # Log CRUD
    # ============================================================
    def add_log(self, account_id: str, level: str, event: str, message: str,
                bot_id: Optional[str] = None, metadata: Optional[str] = None):
        cursor = self.connection.cursor()
        safe_message = redact_sensitive(message)
        safe_metadata = (redact_sensitive(metadata)
                         if metadata is not None else None)
        cursor.execute("""
            INSERT INTO bot_logs (account_id, bot_id, level, event, message, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (account_id, bot_id, level, event, safe_message, safe_metadata,
              utc_now_iso()))
        self.connection.commit()

    def get_logs(self, account_id: Optional[str] = None, bot_id: Optional[str] = None,
                 level: Optional[str] = None, limit: int = 100) -> list[dict]:
        cursor = self.connection.cursor()
        query = "SELECT * FROM bot_logs WHERE 1=1"
        params = []
        if account_id:
            query += " AND account_id=?"
            params.append(account_id)
        if bot_id:
            query += " AND bot_id=?"
            params.append(bot_id)
        if level:
            query += " AND level=?"
            params.append(level)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def add_system_log(self, level: str, component: str, message: str):
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO system_logs (level, component, message, created_at)
            VALUES (?, ?, ?, ?)
        """, (level, component, redact_sensitive(message), utc_now_iso()))
        self.connection.commit()

    # ============================================================
    # Operational alerts
    # ============================================================
    def raise_alert(self, kind: str, dedupe_key: str, message: str,
                    severity: str = 'WARNING', account_id: Optional[str] = None,
                    bot_id: Optional[str] = None,
                    metadata: Optional[Any] = None) -> int:
        """Create or reopen one alert without producing notification floods."""
        now = utc_now_iso()
        safe_message = redact_sensitive(str(message))
        if metadata is None:
            safe_metadata = None
        elif isinstance(metadata, str):
            safe_metadata = redact_sensitive(metadata)
        else:
            safe_metadata = redact_sensitive(json.dumps(metadata, default=str))
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO alerts
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
                acknowledged_by=NULL
        """, (account_id or None, bot_id or None, str(severity).upper(),
              str(kind).upper(), str(dedupe_key), safe_message, safe_metadata,
              now, now))
        cursor.execute("SELECT id FROM alerts WHERE dedupe_key=?", (dedupe_key,))
        alert_id = int(cursor.fetchone()['id'])
        self.connection.commit()
        return alert_id

    def resolve_alert(self, dedupe_key: str) -> bool:
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE alerts SET status='RESOLVED', last_seen_at=?
            WHERE dedupe_key=? AND status='OPEN'
        """, (utc_now_iso(), dedupe_key))
        changed = cursor.rowcount > 0
        self.connection.commit()
        return changed

    def record_runtime_start(self, component: str, window_seconds: int = 300,
                             threshold: int = 3) -> int:
        """Record a real process start and alert when starts cluster in a window."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat().replace('+00:00', 'Z')
        cutoff = (now_dt - timedelta(seconds=max(1, window_seconds))) \
            .isoformat().replace('+00:00', 'Z')
        retention = (now_dt - timedelta(days=1)).isoformat().replace('+00:00', 'Z')
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO runtime_starts (component, started_at) VALUES (?, ?)",
            (component, now))
        cursor.execute("DELETE FROM runtime_starts WHERE started_at<?", (retention,))
        cursor.execute("""
            SELECT COUNT(*) AS count FROM runtime_starts
            WHERE component=? AND started_at>=?
        """, (component, cutoff))
        count = int(cursor.fetchone()['count'])
        self.connection.commit()
        if count >= max(2, threshold):
            self.raise_alert(
                kind='RESTART_LOOP',
                dedupe_key=f'restart:{component}',
                severity='CRITICAL',
                message=(f'{component} started {count} times within '
                         f'{window_seconds} seconds'),
                metadata={'component': component, 'starts_in_window': count,
                          'window_seconds': window_seconds},
            )
        return count
