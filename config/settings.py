"""
Konfigurasi Multi-Account DCA Bot
Menggantikan config.py sebagai sumber konfigurasi utama
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# Encryption Key for API Credentials
# ============================================================
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '')

# ============================================================
# Database
# ============================================================
DATABASE_PATH = os.getenv('DATABASE_PATH', 'data/dca_bot.db')
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# ============================================================
# Dashboard
# ============================================================
DASHBOARD_HOST = os.getenv('DASHBOARD_HOST', '0.0.0.0')
DASHBOARD_PORT = int(os.getenv('DASHBOARD_PORT', '5000'))
DASHBOARD_DEBUG = os.getenv('DASHBOARD_DEBUG', 'false').lower() == 'true'

# ============================================================
# Bot Default Settings
# ============================================================
DEFAULT_BASE_ORDER_IDR = 15000
DEFAULT_SAFETY_ORDER_IDR = 15000
DEFAULT_MAX_SAFETY_ORDERS = 6
DEFAULT_SAFETY_ORDER_DISTANCE = 1.2
DEFAULT_TAKE_PROFIT_PERCENT = 0.5
DEFAULT_STOP_LOSS_PERCENT = 0.0
DEFAULT_MARTINGALE_ENABLED = False
DEFAULT_VOLUME_SCALE = 1.5
DEFAULT_STEP_SCALE = 1.5
DEFAULT_RSI_PERIOD = 14
DEFAULT_RSI_OVERSOLD = 45
DEFAULT_RSI_OVERBOUGHT = 70
DEFAULT_DRY_RUN = True

# ============================================================
# Bot Loop
# ============================================================
BOT_CHECK_INTERVAL = 10  # seconds between each check cycle
ORDER_SYNC_INTERVAL = 60  # seconds between exchange order sync
RECONCILIATION_INTERVAL = 300  # seconds between full reconciliation

# ============================================================
# Rate Limiting
# ============================================================
MAX_API_RETRIES = 3
API_RETRY_DELAY = 2  # seconds
API_TIMEOUT = 30  # seconds
RATE_LIMIT_CALLS_PER_SECOND = 2
API_CIRCUIT_FAILURE_THRESHOLD = max(
    1, int(os.getenv('API_CIRCUIT_FAILURE_THRESHOLD', '5')))
API_CIRCUIT_COOLDOWN_SECONDS = max(
    10, int(os.getenv('API_CIRCUIT_COOLDOWN_SECONDS', '120')))
MAX_ACCOUNT_EXPOSURE_IDR = max(
    0.0, float(os.getenv('MAX_ACCOUNT_EXPOSURE_IDR', '0')))
TELEGRAM_PRICE_CHANGE_PERCENT = max(
    0.1, float(os.getenv('TELEGRAM_PRICE_CHANGE_PERCENT', '5')))
LIVE_TRADING_ENABLED = os.getenv(
    'LIVE_TRADING_ENABLED', 'false').strip().lower() == 'true'
LIVE_TRADING_CONFIRMATION = os.getenv('LIVE_TRADING_CONFIRMATION', '')
LIVE_TRADING_BOT_IDS = frozenset(
    value.strip() for value in os.getenv('LIVE_TRADING_BOT_IDS', '').split(',')
    if value.strip()
)
LIVE_MIN_DRY_RUN_CYCLES = min(
    100, max(1, int(os.getenv('LIVE_MIN_DRY_RUN_CYCLES', '1'))))


def _planned_strategy_capital(strategy: dict | None) -> float:
    if not strategy:
        return 0.0
    base = max(float(strategy.get('base_order_amount', 0) or 0), 0)
    safety = max(float(strategy.get('safety_order_amount', 0) or 0), 0)
    maximum = max(int(strategy.get('max_safety_orders', 0) or 0), 0)
    martingale = bool(strategy.get('martingale_enabled', False))
    scale = max(float(strategy.get('volume_scale', 1) or 1), 0)
    return base + sum(
        safety * (scale ** (level - 1) if martingale else 1)
        for level in range(1, maximum + 1)
    )


def live_trading_allowed_for(bot_id: str,
                             completed_dry_run_cycles: int = 0,
                             strategy: dict | None = None) -> bool:
    planned_capital = _planned_strategy_capital(strategy)
    stop_loss = float(strategy.get('stop_loss_percent', 0) or 0) \
        if strategy else 0
    max_position = float(strategy.get('max_position_amount', 0) or 0) \
        if strategy else 0
    return (
        LIVE_TRADING_ENABLED
        and LIVE_TRADING_CONFIRMATION == 'I_ACCEPT_LIVE_TRADING_RISK'
        and planned_capital > 0
        and stop_loss > 0
        and max_position >= planned_capital
        and MAX_ACCOUNT_EXPOSURE_IDR >= planned_capital
        and str(bot_id) in LIVE_TRADING_BOT_IDS
        and completed_dry_run_cycles >= LIVE_MIN_DRY_RUN_CYCLES
    )

# ============================================================
# Logging
# ============================================================
LOG_DIR = 'logs'
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_MAX_BYTES = max(
    1024, int(os.getenv('PYTHON_LOG_MAX_BYTES', str(1024 * 1024))))
