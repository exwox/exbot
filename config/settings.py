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
TELEGRAM_PRICE_CHANGE_PERCENT = max(
    0.1, float(os.getenv('TELEGRAM_PRICE_CHANGE_PERCENT', '5')))
# Mode real ditentukan oleh bots.dry_run. Variabel gate rollout lama dan
# MAX_ACCOUNT_EXPOSURE_IDR diabaikan, termasuk pada instalasi yang diupgrade.

# ============================================================
# Logging
# ============================================================
LOG_DIR = 'logs'
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_MAX_BYTES = max(
    1024, int(os.getenv('PYTHON_LOG_MAX_BYTES', str(1024 * 1024))))
