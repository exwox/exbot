"""
Constants for Multi-Account DCA Bot
"""
from enum import Enum
from typing import Final

# ============================================================
# Bot Status Constants
# ============================================================
class BotStatus(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"
    STOPPED = "STOPPED"
    DISCONNECTED = "DISCONNECTED"
    STARTING = "STARTING"
    RECOVERING = "RECOVERING"

class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"

class OrderStatus(str, Enum):
    REQUESTED = "REQUESTED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    # Retained for legacy rows; new order intents use REQUESTED.
    PENDING = "PENDING"

class OrderType(str, Enum):
    BUY = "buy"
    SELL = "sell"

class OrderSide(str, Enum):
    BASE = "base"
    SO = "so"
    TP = "tp"
    SL = "sl"

class PositionStatus(str, Enum):
    PENDING_BASE = "PENDING_BASE"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIAL = "PARTIAL"

class LogLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    DEBUG = "DEBUG"
    CRITICAL = "CRITICAL"

class LogEvent(str, Enum):
    BOT_START = "BOT_START"
    BOT_STOP = "BOT_STOP"
    BOT_PAUSE = "BOT_PAUSE"
    BOT_ERROR = "BOT_ERROR"
    BASE_ORDER = "BASE_ORDER"
    DCA_ENTRY = "DCA_ENTRY"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_FAILED = "ORDER_FAILED"
    RECONCILIATION = "RECONCILIATION"
    RECOVERY = "RECOVERY"
    API_ERROR = "API_ERROR"
    CONFIG_CHANGE = "CONFIG_CHANGE"

# ============================================================
# Strategy Types
# ============================================================
STRATEGY_DCA: Final[str] = "DCA"
STRATEGY_DCA_MARTINGALE: Final[str] = "DCA_MARTINGALE"

# ============================================================
# Default Exchange
# ============================================================
DEFAULT_EXCHANGE: Final[str] = "Indodax"
SUPPORTED_EXCHANGES: Final[list[str]] = ["Indodax"]

# ============================================================
# Indodax Specific
# ============================================================
INDODAX_MIN_ORDER_IDR: Final[float] = 10000.0
INDODAX_SAFE_MIN_ORDER_IDR: Final[float] = 10100.0

# ============================================================
# Data Directory
# ============================================================
DATA_DIR: Final[str] = "data"
MIGRATIONS_DIR: Final[str] = "database/migrations"
