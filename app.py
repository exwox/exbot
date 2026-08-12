"""
Exbot DCA Bot - Multi-Account Version
Entry point utama untuk sistem multi-account DCA bot

Penggunaan:
    python app.py          # Start Python Bot Manager saja
    python app.py --setup  # Initial setup (create tables, default strategy)
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import argparse
import time
from pathlib import Path

from database.database import DatabaseManager
from services.encryption_service import EncryptionService
from services.account_service import AccountService
from services.auth_service import AuthService
from core.bot_manager import BotManager
from config.settings import (
    DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_DEBUG,
    DATABASE_PATH, LOG_DIR, LOG_LEVEL
)


def setup_logging():
    """Setup logging configuration"""
    os.makedirs(LOG_DIR, exist_ok=True)

    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.insert(0, RotatingFileHandler(
            os.path.join(LOG_DIR, 'dca_bot.log'),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8',
        ))
    except OSError as error:
        print(f"[WARN] File log tidak dapat ditulis ({error}); menggunakan stdout saja.")
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format=log_format,
        handlers=handlers,
    )


def setup_database(db: DatabaseManager):
    """Run initial database setup"""
    db.connect()
    print("[OK] Database initialized successfully")

    # Check if we need to create a default strategy
    strategies = db.get_all_strategies()
    if not strategies:
        from models.strategy import Strategy
        default_strategy = Strategy(name="Default", rsi_overbought=70)
        db.add_strategy(default_strategy.to_dict())
        print("[OK] Default strategy created")

        conservative = Strategy(
            name="Conservative",
            base_order_amount=10000,
            safety_order_amount=10000,
            max_safety_orders=3,
            price_deviation=1.0,
            deviation_scale=1.2,
            volume_scale=1.5,
            take_profit_percent=2.0,
            stop_loss_percent=0,
            rsi_overbought=70,
        )
        db.add_strategy(conservative.to_dict())

        aggressive = Strategy(
            name="Aggressive",
            base_order_amount=20000,
            safety_order_amount=20000,
            max_safety_orders=8,
            price_deviation=1.5,
            deviation_scale=1.8,
            volume_scale=2.0,
            take_profit_percent=0.8,
            stop_loss_percent=5.0,
            rsi_overbought=70,
        )
        db.add_strategy(aggressive.to_dict())
        print("[OK] Conservative & Aggressive strategies created")

    print("[OK] Setup complete!")
    db.close()


def check_encryption_key():
    """Check if encryption key is set"""
    from config.settings import ENCRYPTION_KEY
    if not ENCRYPTION_KEY:
        print("[WARN] ENCRYPTION_KEY tidak ditemukan di .env file!")
        print("   Generate key: node -e \"console.log(require('crypto').randomBytes(32).toString('hex'))\"")
        print("   Lalu tambahkan ke file .env: ENCRYPTION_KEY=<generated_key>")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description='Exbot DCA Bot - Multi Account')
    parser.add_argument('--setup', action='store_true', help='Run initial database setup')
    parser.add_argument('--no-dashboard', action='store_true', help='Deprecated; Python always runs the bot manager only')
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("Main")

    print("=" * 60)
    print("[ROBOT] EXBOT DCA BOT - MULTI ACCOUNT VERSION")
    print("=" * 60)

    # Check encryption key
    if not check_encryption_key():
        return 1

    # Initialize database
    db = DatabaseManager(DATABASE_PATH)

    # Setup mode
    if args.setup:
        setup_database(db)
        return 0

    # Normal mode: connect database and start bot manager
    try:
        db.connect()
        db.record_runtime_start('python-manager')
        logger.info("Database connected successfully")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return 1

    # Initialize encryption
    try:
        encryption = EncryptionService()
        logger.info("Encryption service initialized")
    except ValueError as e:
        logger.error(str(e))
        db.close()
        return 1

    # Initialize account service
    account_service = AccountService(db, encryption)

    # Initialize auth service
    auth_service = AuthService(db)

    # Initialize bot manager
    bot_manager = BotManager(db, encryption)
    try:
        bot_manager.initialize()
        logger.info("Bot Manager initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Bot Manager: {e}")
        db.close()
        raise

    # The supported architecture uses Node for the dashboard/API and Python
    # only for worker management. docker-entrypoint.sh supervises both.
    if not args.no_dashboard:
        logger.info("Python menjalankan Bot Manager saja; jalankan `node dashboard.js` untuk dashboard")
    print("[BOT] Bot Manager running")
    heartbeat_path = Path(os.environ.get(
        'MANAGER_HEARTBEAT_PATH', '/tmp/xbot-manager-heartbeat'))
    try:
        last_health_log = 0.0
        while True:
            bot_manager.reconcile_workers()
            heartbeat_path.touch(exist_ok=True)
            time.sleep(2)
            if time.monotonic() - last_health_log >= 60:
                health = bot_manager.get_health_status()
                logger.info(f"Health: {health}")
                last_health_log = time.monotonic()
    except KeyboardInterrupt:
        print("\n[STOP] Shutting down...")
    finally:
        try:
            heartbeat_path.unlink(missing_ok=True)
        except OSError:
            pass
        bot_manager.shutdown_all()
        db.close()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
