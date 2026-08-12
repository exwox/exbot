"""
Bot Worker - Setiap bot memiliki worker sendiri
Worker terisolasi, tidak berbagi mutable state dengan worker lain
"""
import time
import logging
import threading
import uuid
import hashlib
import json
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
from typing import Optional

from exchanges.indodax_client import IndodaxClient
from core.strategy_engine import StrategyEngine, DCADecision
from database.database import DatabaseManager
from config.constants import BotStatus, LogEvent
from config.settings import (
    BOT_CHECK_INTERVAL,
    API_CIRCUIT_FAILURE_THRESHOLD,
    API_CIRCUIT_COOLDOWN_SECONDS,
    MAX_ACCOUNT_EXPOSURE_IDR,
)
from utils.redaction import redact_sensitive


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class BotWorker:
    """
    Bot Worker independen untuk satu kombinasi Account + Pair.
    Setiap worker memiliki:
    - account_id, bot_id
    - client exchange sendiri (tidak shared)
    - strategy engine sendiri
    - state sendiri (dari database)
    """
    _exposure_locks: dict[str, threading.Lock] = {}
    _exposure_locks_guard = threading.Lock()

    @classmethod
    def _account_exposure_lock(cls, account_id: str) -> threading.Lock:
        with cls._exposure_locks_guard:
            return cls._exposure_locks.setdefault(
                str(account_id), threading.Lock())

    def __init__(self, account_id: str, bot_id: str, pair: str,
                 client: IndodaxClient, strategy_config: dict,
                 db: DatabaseManager, dry_run: bool = True):
        self.account_id = account_id
        self.bot_id = bot_id
        self.pair = pair
        self.client = client
        self.strategy = StrategyEngine(strategy_config)
        self.db = db
        self.dry_run = dry_run

        self.status = BotStatus.STOPPED
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Set by stop() before an in-flight tick finishes.  The worker then
        # performs its own final cancellation, so a stale tick can never save
        # its old order list after Stop has cleared it.
        self._cancel_orders_on_stop = False
        self._force_base_order_on_start = False
        self._rebuild_active_position_on_start = False
        self._api_failure_counts: dict[str, int] = {}
        self._circuit_open_until = 0.0
        self._circuit_reason = ''
        self._circuit_wait_logged = False
        self._circuit_alert_keys: set[str] = set()
        self._current_tick_id = ''
        self._current_cycle_id = ''
        self._current_order_client_id = ''
        self._logger = logging.getLogger(f"Worker-{bot_id}")

    def update_strategy_config(self, strategy_config: dict):
        """Apply a saved DCA strategy without restarting this worker."""
        self.strategy = StrategyEngine(strategy_config)

    def start(self):
        """Start the bot worker in a separate thread"""
        if self._thread and self._thread.is_alive():
            self._logger.warning(f"Worker {self.bot_id} already running")
            return

        # Always reload the latest strategy configuration from database on start
        bot_data = self.db.get_bot(self.bot_id)
        if bot_data and bot_data.get('strategy_id'):
            latest_strategy = self.db.get_strategy(bot_data['strategy_id'])
            if latest_strategy:
                self.update_strategy_config(latest_strategy)

        # Check initial entry mode setting (MARKET/LIMIT = force immediate entry, RSI/RSI_LIMIT = wait for RSI oversold)
        existing_position = self.db.get_position(self.bot_id)
        if getattr(self.strategy, 'initial_entry_mode', 'MARKET') in ('RSI', 'RSI_LIMIT'):
            self._force_base_order_on_start = False
        else:
            self._force_base_order_on_start = existing_position is None

        # Rebuild TP & SO orders using the latest strategy when starting with an active position
        self._rebuild_active_position_on_start = bool(
            existing_position and existing_position.get('status') == 'OPEN'
        )
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.status = BotStatus.RUNNING
        self._log(LogEvent.BOT_START, f"Bot started for {self.pair}")
        self._update_bot_status(BotStatus.RUNNING)

    def stop(self):
        """Stop the bot worker gracefully"""
        self._cancel_orders_on_stop = True
        self._stop_event.set()
        # Wait for an in-flight tick to finish before changing the persisted
        # order state. Without this, a tick can write its stale TP/SO list
        # back to SQLite after Stop has cleared it, preventing the next Start
        # from rebuilding the strategy orders.
        if (self._thread and self._thread.is_alive() and
                threading.current_thread() is not self._thread):
            self._thread.join(timeout=35)
        if self._thread and self._thread.is_alive():
            # A network request can exceed the join timeout.  Keep the
            # database state stopped; _run_loop will cancel the saved order
            # ids immediately after that request returns.
            self.status = BotStatus.STOPPED
            self._log(LogEvent.API_ERROR,
                      "Worker is still completing its current tick; order cancellation deferred",
                      level="WARNING")
            self._update_bot_status(BotStatus.STOPPED)
            return
        # A Stop command must not leave the bot's TP/SO orders working on the
        # exchange. _cancel_all_orders is scoped to this bot's stored ids.
        # The exiting worker may already have performed this cancellation.
        if self._cancel_orders_on_stop:
            self._cancel_all_orders()
            self._cancel_orders_on_stop = False
        self.status = BotStatus.STOPPED
        self._log(LogEvent.BOT_STOP, f"Bot stopped for {self.pair}")
        self._update_bot_status(BotStatus.STOPPED)

    def pause(self):
        """Pause the bot worker"""
        self.status = BotStatus.PAUSED
        self._log(LogEvent.BOT_PAUSE, f"Bot paused for {self.pair}")
        self._update_bot_status(BotStatus.PAUSED)

    def resume(self):
        """Resume the bot worker"""
        if self.status == BotStatus.PAUSED:
            self.status = BotStatus.RUNNING
            self._update_bot_status(BotStatus.RUNNING)

    def reset_active_position(self):
        """Manually clear/reset active position and cancel open orders"""
        try:
            self._cancel_all_orders()
        except Exception as e:
            self._logger.warning(f"Error cancelling orders during manual reset: {e}")

        self.db.close_position(self.bot_id, 'MANUALLY_RESET')
        self._force_base_order_on_start = False
        self._log(LogEvent.BOT_STOP, "Siklus DCA dan sisa posisi berhasil dibersihkan secara manual")

    def _update_bot_status(self, status: BotStatus):
        """Update bot status in database"""
        bot_data = self.db.get_bot(self.bot_id)
        if bot_data:
            bot_data['status'] = status.value
            self.db.update_bot(bot_data)

    def _log(self, event: LogEvent, message: str, level: str = "INFO"):
        """Add structured log entry"""
        safe_message = redact_sensitive(message)
        correlation = {
            key: value for key, value in {
                'tick_id': self._current_tick_id,
                'cycle_id': self._current_cycle_id,
                'client_order_id': self._current_order_client_id,
            }.items() if value
        }
        self.db.add_log(
            account_id=self.account_id,
            bot_id=self.bot_id,
            level=level,
            event=event.value,
            message=safe_message,
            metadata=json.dumps(correlation) if correlation else None,
        )
        if level == "ERROR":
            self._logger.error(f"[{event.value}] {safe_message}")
        else:
            self._logger.info(f"[{event.value}] {safe_message}")

    def _record_trade(self, position: dict, side: str, trade_type: str,
                      price: float, amount: float, amount_quote: float,
                      order_id: str = '', fee: float = 0,
                      cost_basis: float = 0, realized_profit: float = 0,
                      realized_profit_percent: float = 0,
                      close_reason: str = '', executed_at: Optional[str] = None,
                      trade_id: Optional[str] = None):
        """Write a filled execution to the durable trade ledger."""
        self.db.add_trade({
            'id': trade_id,
            'account_id': self.account_id,
            'bot_id': self.bot_id,
            'position_id': position.get('id'),
            'order_id': str(order_id or ''),
            'exchange_trade_id': str(order_id or ''),
            'pair': self.pair,
            'side': side,
            'trade_type': trade_type,
            'price': price,
            'amount': amount,
            'amount_quote': amount_quote,
            'fee': fee,
            'fee_currency': 'IDR',
            'cost_basis': cost_basis,
            'realized_profit': realized_profit,
            'realized_profit_percent': realized_profit_percent,
            'close_reason': close_reason,
            'dry_run': self.dry_run,
            'executed_at': executed_at or utc_now_iso(),
        })

    def _record_api_success(self, operation: str):
        self._api_failure_counts.pop(operation, None)

    def _record_api_failure(self, operation: str, error: str,
                            force_open: bool = False):
        """Open a per-worker safety circuit after repeated exchange errors."""
        count = self._api_failure_counts.get(operation, 0) + 1
        self._api_failure_counts[operation] = count
        error_text = str(error or 'unknown exchange error')
        clock_error = any(marker in error_text.lower() for marker in (
            'timestamp', 'nonce', 'clock', 'time drift'))
        if (force_open or clock_error or
                count >= API_CIRCUIT_FAILURE_THRESHOLD):
            self._circuit_open_until = max(
                self._circuit_open_until,
                time.monotonic() + API_CIRCUIT_COOLDOWN_SECONDS)
            self._circuit_reason = f"{operation}: {error_text}"
            self._circuit_wait_logged = False
            self._log(
                LogEvent.API_ERROR,
                f"Exchange circuit opened for {API_CIRCUIT_COOLDOWN_SECONDS}s "
                f"after {count} failure(s): {self._circuit_reason}",
                level="ERROR",
            )
            kind = ('RECONCILIATION_MISMATCH'
                    if operation == 'state_reconciliation'
                    else 'EXCHANGE_CIRCUIT_OPEN')
            alert_key = f'circuit:{self.bot_id}:{operation}'
            self._circuit_alert_keys.add(alert_key)
            try:
                self.db.raise_alert(
                    kind=kind,
                    dedupe_key=alert_key,
                    severity=('CRITICAL' if force_open or clock_error else 'ERROR'),
                    account_id=self.account_id,
                    bot_id=self.bot_id,
                    message=(f'Trading paused after {count} failure(s) during '
                             f'{operation}: {error_text}'),
                    metadata={
                        'operation': operation,
                        'failure_count': count,
                        'cooldown_seconds': API_CIRCUIT_COOLDOWN_SECONDS,
                    },
                )
            except Exception as alert_error:
                self._logger.error(
                    "Failed to persist circuit alert: %s",
                    redact_sensitive(alert_error))

    def _circuit_is_open(self) -> bool:
        if self._circuit_open_until <= 0:
            return False
        remaining = self._circuit_open_until - time.monotonic()
        if remaining > 0:
            if not self._circuit_wait_logged:
                self._logger.warning(
                    "Exchange circuit active; trading paused for %.0fs (%s)",
                    remaining, self._circuit_reason)
                self._circuit_wait_logged = True
            return True
        self._circuit_open_until = 0
        self._circuit_reason = ''
        self._circuit_wait_logged = False
        self._api_failure_counts.clear()
        for alert_key in self._circuit_alert_keys:
            try:
                self.db.resolve_alert(alert_key)
            except Exception as alert_error:
                self._logger.error(
                    "Failed to resolve circuit alert: %s",
                    redact_sensitive(alert_error))
        self._circuit_alert_keys.clear()
        self._log(LogEvent.BOT_START,
                  "Exchange circuit cooldown ended; guarded retry enabled")
        return False

    def _persist_order(self, exchange_order_id: str, side: str, price: float,
                       amount: float, amount_quote: float,
                       so_number: int = 0, order_type: str = 'limit',
                       position_id: str = '', client_order_id: str = '',
                       status: str = 'OPEN'):
        """Write a placed order to the durable order ledger idempotently."""
        if not exchange_order_id:
            return
        safe_exchange_id = str(exchange_order_id)
        existing = (self.db.get_order_by_client_id(client_order_id)
                    if client_order_id else None)
        if existing:
            self.db.update_order_submission(
                existing['id'], safe_exchange_id, status)
            return
        self.db.add_order({
            'id': f"order_{self.bot_id}_{safe_exchange_id}",
            'bot_id': self.bot_id,
            'account_id': self.account_id,
            'position_id': position_id,
            'exchange_order_id': safe_exchange_id,
            'client_order_id': client_order_id,
            'order_type': order_type,
            'side': side,
            'pair': self.pair,
            'price': price,
            'amount': amount,
            'amount_quote': amount_quote,
            'status': status,
            'is_dca': True,
            'dca_level': so_number,
            'so_number': so_number,
        })

    def _create_order_intent(self, position: dict, role: str, side: str,
                             price: float, amount: float, amount_quote: float,
                             so_number: int = 0) -> dict:
        """Commit a unique client order id before any exchange mutation."""
        role_slug = ''.join(char for char in role.lower() if char.isalnum())[:6]
        client_order_id = f"xb_{role_slug}_{uuid.uuid4().hex[:24]}"[:36]
        self._current_order_client_id = client_order_id
        order_id = f"intent_{client_order_id}"
        self.db.add_order({
            'id': order_id,
            'bot_id': self.bot_id,
            'account_id': self.account_id,
            'position_id': position.get('id', ''),
            'exchange_order_id': '',
            'client_order_id': client_order_id,
            'order_type': role,
            'side': side,
            'pair': self.pair,
            'price': price,
            'amount': amount,
            'amount_quote': amount_quote,
            'status': 'REQUESTED',
            'is_dca': True,
            'dca_level': so_number,
            'so_number': so_number,
        })
        return self.db.get_order_by_client_id(client_order_id)

    def _recover_order_intent(self, intent: dict) -> Optional[dict]:
        """Resolve a durable intent without creating a new exchange order."""
        self._current_order_client_id = str(
            intent.get('client_order_id') or '')
        exchange_order_id = str(intent.get('exchange_order_id') or '')
        if exchange_order_id:
            remote = self.client.get_order_status(self.pair, exchange_order_id)
        elif intent.get('client_order_id'):
            remote = self.client.get_order_by_client_id(
                self.pair, intent.get('client_order_id', ''))
        else:
            remote = self._recover_legacy_order_from_trade_history(intent)
        if not isinstance(remote, dict) or remote.get('error'):
            return None
        recovered_id = str(remote.get('order_id') or exchange_order_id)
        if recovered_id:
            self.db.update_order_submission(
                intent['id'], recovered_id,
                str(remote.get('status', 'OPEN')).upper())
        return remote

    def _recover_legacy_order_from_trade_history(
            self, intent: dict) -> Optional[dict]:
        """Find one legacy order ID, then verify it through getOrder.

        Trade history is only an identifier-discovery fallback for records
        created before durable exchange/client IDs existed. It never infers a
        terminal status and refuses ambiguous matches.
        """
        history = self.client.get_trade_history(self.pair, limit=100)
        if not isinstance(history, list):
            return None

        expected_side = str(intent.get('side') or '').lower()
        expected_price = float(intent.get('price') or 0)
        expected_amount = float(intent.get('amount') or 0)
        # Side-only matching could attach an unrelated account trade. Legacy
        # market intents without a price/quantity fingerprint remain manual.
        if not expected_side or expected_price <= 0 or expected_amount <= 0:
            return None
        candidate_ids = set()
        for trade in history:
            if not isinstance(trade, dict):
                continue
            order_id = str(trade.get('order_id') or '')
            side = str(trade.get('type') or trade.get('side') or '').lower()
            if not order_id or side != expected_side:
                continue
            price = float(trade.get('price') or 0)
            amount = float(
                trade.get('amount') or trade.get('amount_crypto') or 0)
            if expected_price > 0 and (
                    price <= 0 or
                    abs(price - expected_price) / expected_price > 0.005):
                continue
            if expected_amount > 0 and (
                    amount <= 0 or amount > expected_amount * 1.000001):
                continue
            candidate_ids.add(order_id)

        if len(candidate_ids) != 1:
            return None
        order_id = next(iter(candidate_ids))
        remote = self.client.get_order_status(self.pair, order_id)
        if not isinstance(remote, dict) or remote.get('error'):
            return None
        remote.setdefault('order_id', order_id)
        return remote

    def _run_loop(self):
        """Main worker loop - independent lifecycle"""
        self._logger.info(f"Worker loop started for {self.pair}")

        while not self._stop_event.is_set():
            try:
                if self.status == BotStatus.PAUSED:
                    time.sleep(1)
                    continue

                if self.status != BotStatus.RUNNING:
                    time.sleep(1)
                    continue

                self._tick()

            except Exception as e:
                self._logger.error(f"Worker error: {e}")
                self.status = BotStatus.ERROR
                self._log(LogEvent.BOT_ERROR, str(e), level="ERROR")
                self._update_bot_status(BotStatus.ERROR)
                time.sleep(30)  # Backoff before retry
                # Reset status to RUNNING after backoff only if the bot was not stopped
                if not self._stop_event.is_set():
                    bot_record = self.db.get_bot(self.bot_id)
                    current_db_status = bot_record.get('status', '') if bot_record else ''
                    if current_db_status != 'STOPPED':
                        self.status = BotStatus.RUNNING
                        self._update_bot_status(BotStatus.RUNNING)
                        self._log(LogEvent.BOT_START,
                                  "Bot recovered from error, resuming operations",
                                  level="INFO")

            time.sleep(BOT_CHECK_INTERVAL)

        # If Stop arrived while the worker was inside _tick(), cancel only
        # after that tick has finished saving state.  This leaves an explicit
        # empty TP/SO state for Start to rebuild from the newest strategy.
        if self._cancel_orders_on_stop:
            try:
                self._cancel_all_orders()
                self._cancel_orders_on_stop = False
            except Exception as e:
                self._logger.error(f"Final order cancellation failed: {e}")

        self._logger.info(f"Worker loop ended for {self.pair}")

    def _tick(self):
        """Run one correlated tick and clear its context afterwards."""
        self._current_tick_id = f"tick_{uuid.uuid4().hex[:16]}"
        self._current_cycle_id = ''
        self._current_order_client_id = ''
        try:
            return self._tick_once()
        finally:
            self._current_tick_id = ''
            self._current_cycle_id = ''
            self._current_order_client_id = ''

    def _tick_once(self):
        """Single tick of the bot worker."""
        if self._circuit_is_open():
            return
        # Load state from database
        position = self.db.get_position(self.bot_id)
        self._current_cycle_id = str(position.get('id') or '') \
            if position else ''
        if position and self._is_simulated_position(position) != self.dry_run:
            # Never let a simulated position become a live trade. When moving
            # to live mode, archive the simulation; when moving to dry run,
            # leave a real position untouched and wait for the user to manage
            # it in live mode.
            if self.dry_run:
                self._log(LogEvent.API_ERROR,
                          "Dry run is blocked while this bot has an open live position",
                          level="WARNING")
                return
            self.db.close_position(self.bot_id, 'SIMULATION_CLOSED')
            self._log(LogEvent.BOT_STOP, "Previous dry-run position archived before live trading")
            position = None
        if position and self.dry_run:
            # Older dry-run positions could contain every SO at the same
            # price when Step Scale was off.  These orders are local-only, so
            # repair them safely without touching an exchange order.
            self._repair_simulated_order_ledger(position)
            self._normalize_simulated_safety_orders(position)
        if position and position.get('status') == 'PENDING_BASE':
            # A base intent is persisted before submission. Recover it by
            # exchange order id or client_order_id; never create a second BO.
            self._reconcile_pending_base(position)
            return
        if position and (position.get('exit_order_id') or
                         self.db.get_recoverable_order(
                             position.get('id', ''), 'stop_loss', 0)):
            self._reconcile_exit_order(position)
            return
        current_price = None
        if position and self._rebuild_active_position_on_start:
            # Orders may fill while the process is down. Restore durable child
            # intents into the position snapshot and apply their cumulative
            # fills before cancelling/rebuilding strategy orders.
            self._restore_recoverable_child_orders(position)
            position = self.db.get_position(self.bot_id)
            current_price = self._get_current_price()
            if not current_price or current_price <= 0:
                return
            self._sync_and_manage_orders(
                self._build_state(position), current_price,
                replace_missing=False)
            position = self.db.get_position(self.bot_id)
            if not position:
                self._rebuild_active_position_on_start = False
                return
            self._rebuild_active_position_orders(position)
            self._rebuild_active_position_on_start = False
            position = self.db.get_position(self.bot_id)

        state = self._build_state(position)
        if self._force_base_order_on_start and not state.get('active_position'):
            state['pending_new_entry'] = False
            # NOTE: _force_base_order_on_start is NOT cleared here.
            # It is cleared in _execute_start_bot() ONLY after the position
            # is successfully saved.  If the base order fails, the flag stays
            # True so the next tick retries immediately without the RSI gate.

        # Get current price
        current_price = current_price or self._get_current_price()
        if not current_price or current_price <= 0:
            return

        # Get RSI
        rsi = self._calculate_rsi()
        if self._circuit_is_open():
            return

        # Evaluate strategy
        decision = self.strategy.evaluate(state, current_price, rsi)

        # Execute decision
        self._execute_decision(decision, state, current_price)

    def _is_simulated_position(self, position: dict) -> bool:
        """Use the durable cycle mode before legacy pseudo-order markers."""
        cycle = self.db.get_cycle(str(position.get('id') or ''))
        if cycle is not None:
            return bool(cycle.get('dry_run'))
        if str(position.get('tp_order_id') or '').startswith('DRY_'):
            return True
        return any(str(order.get('order_id') or '').startswith('DRY_')
                   for order in position.get('open_orders', []))

    def _build_state(self, position: Optional[dict]) -> dict:
        """Build state dict from position data"""
        if not position:
            return {
                'active_position': False,
                'base_price': 0,
                'base_amount_crypto': 0,
                'so_entries': [],
                'total_invested': 0,
                'total_crypto_bought': 0,
                'tp_price': 0,
                'sl_price': 0,
                'pending_new_entry': True,
                'open_orders': [],
            }
        total_amount = float(position.get('total_amount', 0) or 0)
        sold_amount = min(float(position.get('sold_amount', 0) or 0), total_amount)
        remaining_ratio = ((total_amount - sold_amount) / total_amount
                           if total_amount > 0 else 0)
        return {
            'active_position': position.get('status') == 'OPEN',
            'base_price': position.get('base_price', 0),
            'base_amount_crypto': position.get('base_amount', 0),
            'so_entries': position.get('so_entries', []),
            # After a partial TP, SL/strategy decisions must only use the
            # proportional cost basis of the remaining inventory.
            'total_invested': float(position.get('total_invested', 0) or 0) * remaining_ratio,
            'total_crypto_bought': max(
                total_amount - sold_amount, 0),
            'tp_price': position.get('take_profit_price', 0),
            'sl_price': position.get('stop_loss_price', 0),
            'pending_new_entry': position.get('status') != 'OPEN',
            'open_orders': position.get('open_orders', []),
        }

    def _get_current_price(self) -> Optional[float]:
        """Get current market price"""
        ticker = self.client.get_ticker(self.pair)
        if isinstance(ticker, dict) and 'error' not in ticker:
            try:
                price = float(ticker.get('last', 0))
            except (TypeError, ValueError):
                price = 0
            if price > 0:
                self._record_api_success('ticker')
                return price
        error = ticker.get('error', 'invalid ticker response') \
            if isinstance(ticker, dict) else 'invalid ticker response'
        self._record_api_failure('ticker', error)
        return None

    def _calculate_rsi(self) -> Optional[float]:
        """Calculate RSI from OHLC data"""
        candles = self.client.get_ohlc(self.pair, '1h', self.strategy.rsi_period + 10)
        if isinstance(candles, list) and len(candles) > 0:
            self._record_api_success('ohlc')
            closes = [float(c['close']) for c in candles]
            return self.strategy.calculate_rsi(closes)
        error = candles.get('error', 'empty OHLC response') \
            if isinstance(candles, dict) else 'empty OHLC response'
        self._record_api_failure('ohlc', error)
        return None

    def _execute_decision(self, decision: DCADecision, state: dict, current_price: float):
        """Execute the strategy decision"""
        if decision.action == 'START_BOT':
            self._execute_start_bot(current_price)
        elif decision.action == 'RE_ENTER':
            self._execute_start_bot(current_price)
        elif decision.action == 'TP':
            # Rely on Limit Sell TP active on exchange; do NOT call market sell.
            self._sync_and_manage_orders(state, current_price)
        elif decision.action == 'SL':
            self._execute_stop_loss(state, current_price)
        elif decision.action == 'ACTIVE':
            self._sync_and_manage_orders(state, current_price)

    def _execute_start_bot(self, current_price: float):
        """Execute base order and place all safety orders + TP"""
        planned_capital = self.strategy.planned_capital()
        if (self.strategy.max_position_amount > 0 and
                planned_capital > self.strategy.max_position_amount):
            self._log(
                LogEvent.ORDER_FAILED,
                f"Planned capital Rp {planned_capital:,.0f} exceeds max position Rp {self.strategy.max_position_amount:,.0f}",
                level="ERROR",
            )
            return
        if not self.dry_run:
            balance = self.client.get_balance()
            if not isinstance(balance, dict) or balance.get('error'):
                error = balance.get('error', 'invalid balance response') \
                    if isinstance(balance, dict) else 'invalid balance response'
                self._record_api_failure('balance', error)
                self._log(LogEvent.API_ERROR,
                          f"Base entry blocked: {error}", level="WARNING")
                return
            self._record_api_success('balance')
            available_idr = float(
                balance.get('balance', {}).get('idr', 0) or 0)
            if available_idr < planned_capital:
                self._log(
                    LogEvent.ORDER_FAILED,
                    f"Saldo IDR tidak cukup untuk satu siklus: perlu Rp {planned_capital:,.0f}, tersedia Rp {available_idr:,.0f}",
                    level="ERROR",
                )
                return
        is_limit_entry = getattr(self.strategy, 'initial_entry_mode', 'MARKET') in ('LIMIT', 'RSI_LIMIT')
        entry_label = "Limit Buy" if is_limit_entry else "Market Buy"
        fee_pct = self.strategy.limit_buy_fee_percent if is_limit_entry else self.strategy.market_buy_fee_percent
        trade_type = 'base_limit' if is_limit_entry else 'base_market'

        self._log(LogEvent.BASE_ORDER, f"Executing base order ({entry_label}) at Rp {current_price:,.0f}")

        # Create position
        position = {
            'id': f"pos_{uuid.uuid4().hex[:12]}",
            'bot_id': self.bot_id,
            'status': 'OPEN' if self.dry_run else 'PENDING_BASE',
            'base_price': current_price,
            'average_entry_price': 0,
            'base_amount': 0,
            'total_amount': 0,
            'sold_amount': 0,
            'total_invested': 0,
            'reserved_capital': planned_capital,
            'take_profit_price': 0,
            'stop_loss_price': 0,
            'current_price': current_price,
            'so_entries': [],
            'tp_order_id': None,
            'exit_order_id': None,
            'exit_reason': '',
            'open_orders': [],
        }
        self._current_cycle_id = position['id']

        # Place base order
        if self.dry_run:
            crypto_amount = (self.strategy.base_order_amount / current_price) * \
                (1 - fee_pct / 100)
            position['base_amount'] = crypto_amount
            position['total_amount'] = crypto_amount
            position['total_invested'] = self.strategy.base_order_amount
            base_order_id = f"DRY_BASE_{uuid.uuid4().hex[:16]}"
            self._log(LogEvent.ORDER_PLACED, f"[DRY RUN] Base order ({entry_label}): {crypto_amount:.8f} @ Rp {current_price:,.0f}")
        else:
            gross_crypto = self.strategy.base_order_amount / current_price
            role = 'base_limit' if is_limit_entry else 'base_market'
            # PENDING_BASE + REQUESTED are committed before the private API
            # call. A crash at any later instruction is recoverable by the
            # same client_order_id.
            # The supported runtime has one Python manager. This per-account
            # lock makes the exposure check + reservation atomic across its
            # worker threads before any exchange mutation is allowed.
            with self._account_exposure_lock(self.account_id):
                current_exposure = self.db.get_account_exposure(
                    self.account_id)
                projected_exposure = current_exposure + planned_capital
                if (MAX_ACCOUNT_EXPOSURE_IDR > 0 and
                        projected_exposure > MAX_ACCOUNT_EXPOSURE_IDR):
                    self._log(
                        LogEvent.ORDER_FAILED,
                        f"Account exposure limit blocks entry: current "
                        f"Rp {current_exposure:,.0f} + planned "
                        f"Rp {planned_capital:,.0f} exceeds "
                        f"Rp {MAX_ACCOUNT_EXPOSURE_IDR:,.0f}",
                        level="ERROR")
                    return
                self.db.save_position(position)
            intent = self._create_order_intent(
                position, role, 'buy', current_price, gross_crypto,
                self.strategy.base_order_amount)
            self._force_base_order_on_start = False
            self._submit_or_recover_base(position, intent)
            return

        self._activate_base_position(
            position, base_order_id, crypto_amount,
            self.strategy.base_order_amount, current_price, fee_pct, trade_type)

    @staticmethod
    def _submission_is_ambiguous(error: str) -> bool:
        text = str(error or '').lower()
        return any(marker in text for marker in (
            'timeout', 'connection', 'max retries', 'failed to parse',
            'temporarily unavailable', 'bad gateway', 'gateway timeout'))

    def _submit_or_recover_base(self, position: dict, intent: dict):
        """Submit one durable base intent, recovering duplicate/unknown ACKs."""
        client_order_id = str(intent.get('client_order_id') or '')
        is_limit = intent.get('order_type') == 'base_limit'
        if is_limit:
            result = self.client.buy(
                self.pair, float(intent.get('price', 0)),
                float(intent.get('amount', 0)), client_order_id)
        else:
            result = self.client.buy_market(
                self.pair, float(intent.get('amount_quote', 0)), client_order_id)

        if isinstance(result, dict) and result.get('error'):
            self._record_api_failure('base_submit', result['error'])
            # A duplicate client id proves that an earlier ambiguous request
            # reached Indodax. getOrderByClientOrderId is then authoritative.
            recovered = self.client.get_order_by_client_id(
                self.pair, client_order_id)
            if isinstance(recovered, dict) and not recovered.get('error'):
                result = recovered
                self._record_api_success('base_submit')
            elif self._submission_is_ambiguous(result.get('error')):
                self.db.update_order_status(intent['id'], 'SUBMISSION_UNKNOWN')
                self._log(
                    LogEvent.API_ERROR,
                    f"Base submission ACK unknown; intent {client_order_id} retained for reconciliation",
                    level="WARNING",
                )
                return
            else:
                self.db.update_order_status(intent['id'], 'FAILED')
                self.db.close_position(self.bot_id, 'BASE_FAILED')
                self._force_base_order_on_start = (
                    getattr(self.strategy, 'initial_entry_mode', 'MARKET')
                    not in ('RSI', 'RSI_LIMIT'))
                self._log(LogEvent.ORDER_FAILED,
                          f"Base order failed: {result['error']}", level="ERROR")
                return
        else:
            self._record_api_success('base_submit')

        exchange_order_id = str(result.get('order_id', '') or
                                intent.get('exchange_order_id', ''))
        if not exchange_order_id and client_order_id:
            recovered = self.client.get_order_by_client_id(
                self.pair, client_order_id)
            if isinstance(recovered, dict) and not recovered.get('error'):
                result = recovered
                exchange_order_id = str(result.get('order_id', ''))
        if not exchange_order_id:
            self.db.update_order_status(intent['id'], 'SUBMISSION_UNKNOWN')
            self._log(LogEvent.API_ERROR,
                      "Base submission returned no exchange order id; reconciliation required",
                      level="WARNING")
            return

        status_payload = result
        coin = self.pair.lower().replace('_', '').replace('idr', '')
        immediate_receive = float(result.get(f'receive_{coin}', 0) or
                                  result.get('receive', 0) or 0)
        if immediate_receive <= 0:
            fetched = self.client.get_order_status(self.pair, exchange_order_id)
            if isinstance(fetched, dict) and not fetched.get('error'):
                status_payload = fetched
        self.db.update_order_submission(
            intent['id'], exchange_order_id,
            str(status_payload.get('status', 'OPEN')).upper())
        self._handle_base_status(position, intent, status_payload,
                                 exchange_order_id, immediate_receive)

    def _reconcile_pending_base(self, position: dict):
        intent = self.db.get_pending_base_order(self.bot_id, position.get('id', ''))
        if not intent:
            self._record_api_failure(
                'state_reconciliation',
                'PENDING_BASE has no durable order intent',
                force_open=True)
            self._log(LogEvent.BOT_ERROR,
                      "PENDING_BASE has no durable order intent; manual recovery required",
                      level="ERROR")
            return
        exchange_order_id = str(intent.get('exchange_order_id') or '')
        status = self._recover_order_intent(intent)
        if isinstance(status, dict) and not status.get('error'):
            recovered_id = str(status.get('order_id') or exchange_order_id)
            if recovered_id:
                self.db.update_order_submission(
                    intent['id'], recovered_id,
                    str(status.get('status', 'OPEN')).upper())
            self._handle_base_status(position, intent, status, recovered_id, 0)
            return
        if intent.get('status') in ('REQUESTED', 'SUBMISSION_UNKNOWN'):
            # Resubmitting the exact persisted client id is safe: Indodax
            # rejects duplicates, after which the lookup path above recovers.
            self._submit_or_recover_base(position, intent)
            return
        self._log(LogEvent.API_ERROR,
                  f"Unable to reconcile pending base order: {status.get('error', 'unknown response') if isinstance(status, dict) else status}",
                  level="WARNING")

    def _handle_base_status(self, position: dict, intent: dict, payload: dict,
                            exchange_order_id: str, immediate_receive: float):
        status = str(payload.get('status', '')).lower()
        terminal_cancel = status in (
            'cancelled', 'canceled', 'rejected', 'expired')
        fee_pct = (self.strategy.limit_buy_fee_percent
                   if intent.get('order_type') == 'base_limit'
                   else self.strategy.market_buy_fee_percent)
        filled_gross = float(payload.get('filled_amount', 0) or 0)
        filled_quote = float(payload.get('filled_quote', 0) or
                             payload.get('spend_rp', 0) or 0)
        crypto_amount = immediate_receive
        if crypto_amount <= 0 and filled_gross > 0:
            crypto_amount = filled_gross * (1 - fee_pct / 100)
        if filled_quote <= 0 and crypto_amount > 0:
            filled_quote = float(intent.get('amount_quote', 0) or 0)

        # A cancelled order may still contain a legitimate partial fill. This
        # commonly appears after STOP cancels a PENDING_BASE and the worker is
        # restarted. The remainder is already terminal, so do not cancel it a
        # second time; promote only the measured fill into protected inventory.
        needs_partial_cancel = status in ('partial', 'partially_filled') or (
            crypto_amount > 0 and status in ('open', 'pending'))
        terminal_partial = terminal_cancel and crypto_amount > 0
        is_filled = status in ('filled', 'done', 'closed') or (
            immediate_receive > 0 and status not in ('open', 'partial', 'partially_filled'))
        if needs_partial_cancel:
            # Do not leave acquired base inventory without TP protection.
            cancel_result = self.client.cancel_order(
                self.pair, exchange_order_id, 'buy')
            if isinstance(cancel_result, dict) and cancel_result.get('error'):
                self._log(
                    LogEvent.API_ERROR,
                    f"Partial base fill detected but remainder cancellation failed: {cancel_result['error']}",
                    level="ERROR",
                )
                return
            self.db.update_order_submission(intent['id'], exchange_order_id,
                                            'PARTIALLY_FILLED')
            is_filled = True
        elif terminal_partial:
            self.db.update_order_submission(intent['id'], exchange_order_id,
                                            'PARTIALLY_FILLED')
            is_filled = True
        if not is_filled:
            if terminal_cancel:
                self.db.update_order_submission(intent['id'], exchange_order_id,
                                                status.upper())
                self.db.close_position(self.bot_id, 'BASE_CANCELLED')
            return
        if crypto_amount <= 0:
            self._record_api_failure(
                'state_reconciliation',
                'base order is filled without measurable quantity',
                force_open=True)
            self._log(LogEvent.API_ERROR,
                      "Base order reports filled but has no measurable quantity; manual reconciliation required",
                      level="ERROR")
            return

        price = float(payload.get('price') or intent.get('price') or 0)
        if price <= 0 and crypto_amount > 0:
            price = filled_quote / crypto_amount
        trade_type = intent.get('order_type', 'base_market')
        self._activate_base_position(
            position, exchange_order_id, crypto_amount, filled_quote,
            price, fee_pct, trade_type, intent)

    def _activate_base_position(self, position: dict, base_order_id: str,
                                crypto_amount: float, invested: float,
                                price: float, fee_pct: float, trade_type: str,
                                intent: Optional[dict] = None):
        """Atomically promote a confirmed BO, then create its child orders."""
        position['status'] = 'OPEN'
        position['base_price'] = price
        position['current_price'] = price
        position['base_amount'] = crypto_amount
        position['total_amount'] = crypto_amount
        position['total_invested'] = invested
        avg_entry = self.strategy.calculate_average_entry(price, crypto_amount, [])
        position['average_entry_price'] = avg_entry
        position['take_profit_price'] = self.strategy.get_tp_price(avg_entry)
        position['stop_loss_price'] = (
            self.strategy.get_sl_price(avg_entry)
            if self.strategy.stop_loss_percent > 0 else 0)
        self.db.save_position(position)
        self._force_base_order_on_start = False
        self._record_trade(
            position, 'buy', trade_type, price, crypto_amount, invested,
            base_order_id, invested * fee_pct / 100,
            trade_id=f"trade_base_{position.get('id', '')}")
        if intent:
            self.db.update_order_submission(intent['id'], base_order_id, 'FILLED')
        else:
            self._persist_order(
                base_order_id, 'buy', price, crypto_amount, invested,
                order_type=trade_type, position_id=position.get('id', ''),
                status='FILLED')
        self._log(LogEvent.ORDER_FILLED,
                  f"Base order confirmed: {crypto_amount:.8f} @ Rp {price:,.0f} (order_id={base_order_id})")
        self._place_tp_order(position)
        self.db.save_position(position)
        for so_num in range(1, self.strategy.max_safety_orders + 1):
            self._place_so_order(position, so_num)
            self.db.save_position(position)

    def _place_tp_order(self, position: dict):
        """Place take profit limit sell order"""
        tp_price = self.strategy.get_tp_price(position.get('average_entry_price', 0))
        total_crypto = max(
            float(position.get('total_amount', 0) or 0) -
            float(position.get('sold_amount', 0) or 0), 0)

        if total_crypto <= 0 or tp_price <= 0:
            return

        if self.dry_run:
            previous_tp_id = str(position.get('tp_order_id') or '')
            if previous_tp_id:
                self.db.update_order_status_by_exchange_id(
                    previous_tp_id, 'CANCELLED')
            position['tp_order_id'] = f"DRY_TP_{uuid.uuid4().hex[:16]}"
            self._persist_order(
                position['tp_order_id'], 'sell', tp_price, total_crypto,
                total_crypto * tp_price, order_type='take_profit',
                position_id=position.get('id', ''))
            self._log(LogEvent.ORDER_PLACED, f"[DRY RUN] TP order: {total_crypto:.8f} @ Rp {tp_price:,.0f}")
        else:
            intent = None
            existing = self.db.get_recoverable_order(
                position.get('id', ''), 'take_profit', 0)
            if existing:
                remote = self._recover_order_intent(existing)
                if remote:
                    remote_status = str(remote.get('status', 'open')).lower()
                    if remote_status not in ('cancelled', 'canceled', 'rejected', 'expired'):
                        position['tp_order_id'] = str(
                            remote.get('order_id') or existing.get('exchange_order_id') or '')
                        return
                    self.db.update_order_status(existing['id'], remote_status.upper())
                else:
                    intent = existing

            # The recorded DCA amount is an accounting estimate.  The actual
            # exchange balance can be lower because of the exchange's exact
            # fill/fee rounding, or briefly be locked while a previous TP is
            # being cancelled.  Never submit more than this bot's available
            # spot balance; doing so makes Indodax reject the TP with
            # "Insufficient balance".
            available = self._get_available_crypto_balance()
            if available is None:
                self._log(LogEvent.API_ERROR,
                          "TP not placed: unable to verify available crypto balance",
                          level="WARNING")
                return
            sell_amount = self._safe_sell_amount(total_crypto, available)
            if sell_amount <= 0:
                self._log(LogEvent.ORDER_FAILED,
                          "TP pending: crypto balance is still locked or unavailable after order cancellation",
                          level="WARNING")
                return

            if sell_amount < float(total_crypto):
                self._log(LogEvent.API_ERROR,
                          f"TP amount adjusted to available balance: {sell_amount:.8f} of {float(total_crypto):.8f}",
                          level="WARNING")

            if intent:
                tp_price = float(intent.get('price', tp_price) or tp_price)
                sell_amount = float(intent.get('amount', sell_amount) or sell_amount)
            else:
                intent = self._create_order_intent(
                    position, 'take_profit', 'sell', tp_price, sell_amount,
                    sell_amount * tp_price)
            result = self.client.sell(
                self.pair, tp_price, sell_amount, intent['client_order_id'])
            if isinstance(result, dict) and result.get('error'):
                self._record_api_failure('tp_submit', result['error'])
                recovered = self.client.get_order_by_client_id(
                    self.pair, intent['client_order_id'])
                if isinstance(recovered, dict) and not recovered.get('error'):
                    result = recovered
                    self._record_api_success('tp_submit')
                elif self._submission_is_ambiguous(result['error']):
                    self.db.update_order_status(intent['id'], 'SUBMISSION_UNKNOWN')
                    position['tp_order_id'] = None
                    self._log(LogEvent.API_ERROR,
                              "TP submission ACK unknown; durable intent retained",
                              level="WARNING")
                    return
                else:
                    self.db.update_order_status(intent['id'], 'FAILED')
                    self._log(LogEvent.ORDER_FAILED,
                              f"TP order failed: {result['error']}", level="WARNING")
                    position['tp_order_id'] = None
                    return
            else:
                self._record_api_success('tp_submit')
            position['tp_order_id'] = str(result.get('order_id', ''))
            if not position['tp_order_id']:
                self.db.update_order_status(intent['id'], 'SUBMISSION_UNKNOWN')
                return
            self.db.update_order_submission(
                intent['id'], position['tp_order_id'],
                str(result.get('status', 'OPEN')).upper())
            self._log(LogEvent.ORDER_PLACED,
                      f"TP order placed: ID {position['tp_order_id']} @ Rp {tp_price:,.0f}")

    def _get_available_crypto_balance(self) -> Optional[float]:
        """Return the unreserved balance for this bot's base asset only."""
        balance_result = self.client.get_balance()
        if not isinstance(balance_result, dict) or balance_result.get('error'):
            error = balance_result.get('error', 'invalid balance response') \
                if isinstance(balance_result, dict) else 'invalid balance response'
            self._record_api_failure('balance', error)
            return None

        self._record_api_success('balance')

        coin = self.pair.lower().replace('_', '').replace('idr', '')
        balances = balance_result.get('balance', {})
        try:
            return max(float(balances.get(coin, 0) or 0), 0.0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_sell_amount(recorded_amount: float, available_amount: float) -> float:
        """Clamp to available balance and round *down* to Indodax precision."""
        candidate = min(float(recorded_amount), float(available_amount))
        if candidate <= 0:
            return 0.0
        # Leave one unit at the eighth decimal place to avoid float and fee
        # rounding turning a nominally equal balance into an over-sell.
        safe = Decimal(str(candidate)) - Decimal('0.00000001')
        if safe <= 0:
            return 0.0
        return float(safe.quantize(Decimal('0.00000001'), rounding=ROUND_DOWN))

    def _place_so_order(self, position: dict, so_number: int):
        """Place a safety order limit buy"""
        base_price = position.get('base_price', 0)
        so_price = self.strategy.get_so_price(base_price, so_number)
        target_amount = self.strategy.get_so_amount(so_number)
        partial_entries = [
            entry for entry in position.get('so_entries', [])
            if int(entry.get('number') or entry.get('step') or 0) == so_number
            and not entry.get('finalized', True)
        ]
        already_filled = sum(float(entry.get('amount_idr', 0) or 0)
                             for entry in partial_entries)
        so_amount = max(target_amount - already_filled, 0)
        if so_amount <= 0.0001:
            return
        if not self.dry_run and so_amount < 10000:
            # Indodax cannot accept the residual without increasing exposure
            # beyond the configured SO target. Preserve the partial fill and
            # explicitly finalize this level instead of force-rounding it up.
            for entry in partial_entries:
                entry['finalized'] = True
                entry['completion_reason'] = 'residual_below_exchange_minimum'
            if partial_entries:
                self.db.sync_cycle_safety_order_count(
                    position.get('id', ''),
                    sum(1 for entry in position.get('so_entries', [])
                        if entry.get('finalized', True)),
                )
                self._log(
                    LogEvent.ORDER_FAILED,
                    f"SO{so_number} residual Rp {so_amount:,.0f} below exchange minimum; partial level finalized",
                    level="WARNING",
                )
            return
        gross_crypto_amount = so_amount / so_price
        # A resting SO is a limit buy (maker by default). Store the net amount
        # expected after the configured buy fee, but submit the gross size.
        crypto_amount = gross_crypto_amount * (1 - self.strategy.limit_buy_fee_percent / 100)
        open_orders = position.get('open_orders', [])

        if self.dry_run:
            order_id = f"DRY_SO_{so_number}_{uuid.uuid4().hex[:16]}"
            self._log(LogEvent.ORDER_PLACED, f"[DRY RUN] SO{so_number}: {crypto_amount:.8f} @ Rp {so_price:,.0f}")
        else:
            intent = None
            existing = self.db.get_recoverable_order(
                position.get('id', ''), f'so_{so_number}', so_number)
            if existing:
                remote = self._recover_order_intent(existing)
                if remote:
                    remote_status = str(remote.get('status', 'open')).lower()
                    if remote_status not in ('cancelled', 'canceled', 'rejected', 'expired'):
                        order_id = str(remote.get('order_id') or
                                       existing.get('exchange_order_id') or '')
                        so_price = float(existing.get('price', so_price) or so_price)
                        so_amount = float(existing.get('amount_quote', so_amount) or so_amount)
                        gross_crypto_amount = float(existing.get('amount', gross_crypto_amount) or gross_crypto_amount)
                        crypto_amount = gross_crypto_amount * (
                            1 - self.strategy.limit_buy_fee_percent / 100)
                        if not any(str(item.get('order_id')) == order_id
                                   for item in open_orders):
                            open_orders.append({
                                'order_id': order_id, 'type': f'so_{so_number}',
                                'price': so_price, 'amount_idr': so_amount,
                                'amount_crypto': crypto_amount,
                                'gross_amount_crypto': gross_crypto_amount,
                                'so_number': so_number,
                            })
                        position['open_orders'] = open_orders
                        return
                    self.db.update_order_status(existing['id'], remote_status.upper())
                else:
                    intent = existing

            if intent:
                so_price = float(intent.get('price', so_price) or so_price)
                gross_crypto_amount = float(
                    intent.get('amount', gross_crypto_amount) or gross_crypto_amount)
                so_amount = float(intent.get('amount_quote', so_amount) or so_amount)
                crypto_amount = gross_crypto_amount * (
                    1 - self.strategy.limit_buy_fee_percent / 100)
            else:
                intent = self._create_order_intent(
                    position, f'so_{so_number}', 'buy', so_price,
                    gross_crypto_amount, so_amount, so_number)
            result = self.client.buy(
                self.pair, so_price, gross_crypto_amount,
                intent['client_order_id'])
            if isinstance(result, dict) and result.get('error'):
                self._record_api_failure('so_submit', result['error'])
                recovered = self.client.get_order_by_client_id(
                    self.pair, intent['client_order_id'])
                if isinstance(recovered, dict) and not recovered.get('error'):
                    result = recovered
                    self._record_api_success('so_submit')
                elif self._submission_is_ambiguous(result['error']):
                    self.db.update_order_status(intent['id'], 'SUBMISSION_UNKNOWN')
                    self._log(LogEvent.API_ERROR,
                              f"SO{so_number} submission ACK unknown; durable intent retained",
                              level="WARNING")
                    return
                else:
                    self.db.update_order_status(intent['id'], 'FAILED')
                    self._log(LogEvent.ORDER_FAILED,
                              f"SO{so_number} failed: {result['error']}", level="WARNING")
                    return
            else:
                self._record_api_success('so_submit')
            order_id = str(result.get('order_id', ''))
            if not order_id:
                self.db.update_order_status(intent['id'], 'SUBMISSION_UNKNOWN')
                return
            self.db.update_order_submission(
                intent['id'], order_id,
                str(result.get('status', 'OPEN')).upper())
            self._log(LogEvent.ORDER_PLACED, f"SO{so_number} placed: ID {order_id} @ Rp {so_price:,.0f}")

        open_orders.append({
            'order_id': str(order_id),
            'type': f'so_{so_number}',
            'price': so_price,
            'amount_idr': so_amount,
            'amount_crypto': crypto_amount,
            'gross_amount_crypto': gross_crypto_amount,
            'so_number': so_number,
        })
        if self.dry_run:
            self._persist_order(
                order_id, 'buy', so_price, gross_crypto_amount, so_amount,
                so_number=so_number, order_type=f'so_{so_number}',
                position_id=position.get('id', ''))
        position['open_orders'] = open_orders

    def _rebuild_active_position_orders(self, position: dict):
        """Apply the latest strategy to an already-held spot position.

        Called after Stop -> Start, when all previous working orders were
        deliberately cancelled. Filled base/SO amounts are kept intact; only
        TP and SO levels that remain to be executed are recreated.
        """
        so_entries = position.get('so_entries', [])
        average_entry = self.strategy.calculate_average_entry(
            position.get('base_price', 0),
            position.get('base_amount', 0),
            so_entries,
        )
        if average_entry <= 0 or float(position.get('total_amount', 0)) <= 0:
            self._log(LogEvent.ORDER_FAILED,
                      "Cannot apply new strategy: active position has no confirmed amount",
                      level="ERROR")
            return

        # Ensure any old working orders are cancelled first
        self._cancel_all_orders()

        position['average_entry_price'] = average_entry
        position['take_profit_price'] = self.strategy.get_tp_price(average_entry)
        position['stop_loss_price'] = (
            self.strategy.get_sl_price(average_entry)
            if self.strategy.stop_loss_percent > 0 else 0
        )
        position['open_orders'] = []
        position['tp_order_id'] = None

        self._place_tp_order(position)
        filled_numbers = {
            int(entry.get('number') or entry.get('step') or 0)
            for entry in so_entries
            if entry.get('finalized', True)
        }
        for so_number in range(1, self.strategy.max_safety_orders + 1):
            if so_number not in filled_numbers:
                self._place_so_order(position, so_number)

        self.db.save_position(position)
        self._log(LogEvent.ORDER_PLACED,
                  f"Posisi aktif dilanjutkan dengan strategi terbaru! Avg Entry: Rp {average_entry:,.0f} | "
                  f"Target TP ({self.strategy.take_profit_percent}%): Rp {position['take_profit_price']:,.0f}")

    def _restore_recoverable_child_orders(self, position: dict):
        """Restore child intents omitted from position JSON after a crash."""
        changed = False
        open_orders = position.get('open_orders', [])
        for intent in self.db.get_child_orders_for_reconciliation(
                position.get('id', '')):
            role = str(intent.get('order_type') or '')
            if role.startswith('so_') and any(
                    str(entry.get('order_id', '')) ==
                    str(intent.get('exchange_order_id', '')) and
                    entry.get('finalized', True)
                    for entry in position.get('so_entries', [])):
                continue
            remote = self._recover_order_intent(intent)
            if not remote:
                continue
            order_id = str(remote.get('order_id') or
                           intent.get('exchange_order_id') or '')
            if not order_id:
                continue
            status = str(remote.get('status', 'open')).lower()
            has_fill = float(remote.get('filled_amount', 0) or 0) > 0
            if (status in ('cancelled', 'canceled', 'rejected', 'expired')
                    and not has_fill):
                continue
            if role == 'take_profit':
                if not position.get('tp_order_id'):
                    position['tp_order_id'] = order_id
                    changed = True
                continue
            if not role.startswith('so_'):
                continue
            if any(str(order.get('order_id')) == order_id
                   for order in open_orders):
                continue
            gross_amount = float(intent.get('amount', 0) or 0)
            open_orders.append({
                'order_id': order_id,
                'type': role,
                'price': float(intent.get('price', 0) or 0),
                'amount_idr': float(intent.get('amount_quote', 0) or 0),
                'amount_crypto': gross_amount * (
                    1 - self.strategy.limit_buy_fee_percent / 100),
                'gross_amount_crypto': gross_amount,
                'so_number': int(intent.get('so_number', 0) or 0),
            })
            changed = True
        if changed:
            position['open_orders'] = open_orders
            self.db.save_position(position)

    def _repair_simulated_order_ledger(self, position: dict):
        """Backfill legacy dry pseudo-orders and their terminal trade status."""
        position_id = str(position.get('id') or '')
        if not position_id:
            return
        filled_order_ids = set()
        for trade in self.db.get_bot_trades(self.bot_id, 500):
            if str(trade.get('position_id') or '') != position_id:
                continue
            order_id = str(trade.get('order_id') or '')
            if order_id:
                filled_order_ids.add(order_id)
                self.db.update_order_status_by_exchange_id(order_id, 'FILLED')

        active_order_ids = {
            str(order.get('order_id') or '')
            for order in position.get('open_orders', [])
            if order.get('order_id')
        }
        if position.get('tp_order_id'):
            active_order_ids.add(str(position['tp_order_id']))
        if position.get('exit_order_id'):
            active_order_ids.add(str(position['exit_order_id']))
        for order in self.db.get_bot_orders(self.bot_id, 500):
            if str(order.get('position_id') or '') != position_id:
                continue
            exchange_id = str(order.get('exchange_order_id') or '')
            if (str(order.get('status') or '').upper() in
                    ('REQUESTED', 'SUBMISSION_UNKNOWN', 'OPEN', 'PENDING',
                     'PARTIALLY_FILLED')
                    and exchange_id not in active_order_ids
                    and exchange_id not in filled_order_ids):
                self.db.update_order_status(order['id'], 'CANCELLED')

        tp_order_id = str(position.get('tp_order_id') or '')
        if tp_order_id and not self.db.order_exists_by_exchange_id(tp_order_id):
            remaining_amount = max(
                float(position.get('total_amount', 0) or 0)
                - float(position.get('sold_amount', 0) or 0), 0)
            tp_price = float(position.get('take_profit_price', 0) or 0)
            if remaining_amount > 0 and tp_price > 0:
                self._persist_order(
                    tp_order_id, 'sell', tp_price, remaining_amount,
                    remaining_amount * tp_price, order_type='take_profit',
                    position_id=position_id)

        for order in position.get('open_orders', []):
            order_id = str(order.get('order_id') or '')
            if not order_id or self.db.order_exists_by_exchange_id(order_id):
                continue
            amount_quote = float(order.get('amount_idr', 0) or 0)
            amount = float(order.get('gross_amount_crypto', 0) or 0)
            if amount <= 0:
                amount = float(order.get('amount_crypto', 0) or 0)
            self._persist_order(
                order_id, 'buy', float(order.get('price', 0) or 0),
                amount, amount_quote,
                so_number=int(order.get('so_number', 0) or 0),
                order_type=str(order.get('type') or 'limit'),
                position_id=position_id)

    def _normalize_simulated_safety_orders(self, position: dict):
        """Bring legacy simulated SO prices in line with cumulative DCA."""
        changed = False
        for order in position.get('open_orders', []):
            so_number = int(order.get('so_number') or 0)
            if so_number < 1:
                continue
            expected_price = self.strategy.get_so_price(
                float(position.get('base_price') or 0), so_number)
            if expected_price <= 0:
                continue
            previous_price = float(order.get('price') or 0)
            expected_amount_idr = self.strategy.get_so_amount(so_number)
            amount_idr = float(order.get('amount_idr') or 0)
            amount_changed = abs(amount_idr - expected_amount_idr) > 0.000001
            price_changed = abs(previous_price - expected_price) > max(expected_price * 1e-10, 1e-8)
            if not price_changed and not amount_changed:
                continue
            amount_idr = expected_amount_idr
            gross_crypto = amount_idr / expected_price
            order['price'] = expected_price
            order['amount_idr'] = amount_idr
            order['amount_crypto'] = gross_crypto * (1 - self.strategy.limit_buy_fee_percent / 100)
            changed = True

        if changed:
            self.db.save_position(position)
            self._log(LogEvent.ORDER_PLACED,
                      "[DRY RUN] Safety-order grid disesuaikan dengan strategi DCA terbaru")

    def _execute_take_profit(self, state: dict, current_price: float):
        """Execute take profit - sell all crypto"""
        self._log(LogEvent.TAKE_PROFIT, f"Take profit at Rp {current_price:,.0f}")

        # Cancel all open orders
        self._cancel_all_orders()

        total_crypto = state.get('total_crypto_bought', 0)
        total_invested = state.get('total_invested', 0)

        sell_order_id = ''
        if not self.dry_run and total_crypto > 0:
            result = self.client.sell_market(self.pair, total_crypto)
            if 'error' in result:
                self._log(LogEvent.ORDER_FAILED, f"Sell failed: {result['error']}", level="ERROR")
                return
            sell_order_id = str(result.get('order_id', ''))
            self._log(LogEvent.ORDER_FILLED, f"Sell filled: ID {result.get('order_id', 'N/A')}")

        # Calculate profit
        # TP completion uses a market sell in this worker, so report net P/L
        # after the configured market-sell fee.
        total_value = total_crypto * current_price * (1 - self.strategy.market_sell_fee_percent / 100)
        profit_idr = total_value - total_invested
        profit_pct = (profit_idr / total_invested * 100) if total_invested > 0 else 0

        position = self.db.get_position(self.bot_id)
        if position:
            self._record_trade(
                position, 'sell', 'take_profit', current_price, total_crypto,
                total_value, sell_order_id or f"DRY_TP_FILL_{int(time.time())}",
                total_crypto * current_price *
                self.strategy.market_sell_fee_percent / 100,
                total_invested, profit_idr, profit_pct, 'TAKE_PROFIT',
            )

        # Close position
        self.db.close_position(self.bot_id, 'CLOSED')

        self._log(LogEvent.TAKE_PROFIT,
                  f"TP completed. Profit: {profit_pct:+.2f}% (Rp {profit_idr:,.0f})")

    def _execute_stop_loss(self, state: dict, current_price: float):
        """Submit or reconcile an idempotent stop-loss market sell."""
        self._log(LogEvent.STOP_LOSS, f"Stop loss at Rp {current_price:,.0f}")

        self._cancel_all_orders()
        total_crypto = state.get('total_crypto_bought', 0)
        total_invested = state.get('total_invested', 0)

        if not self.dry_run:
            position = self.db.get_position(self.bot_id)
            if position and total_crypto > 0:
                self._submit_stop_loss(
                    position, total_crypto, current_price, total_invested)
            return

        total_value = total_crypto * current_price * (
            1 - self.strategy.market_sell_fee_percent / 100)
        profit_idr = total_value - total_invested
        profit_pct = (profit_idr / total_invested * 100) if total_invested > 0 else 0
        position = self.db.get_position(self.bot_id)
        if position:
            self._record_trade(
                position, 'sell', 'stop_loss', current_price, total_crypto,
                total_value, f"DRY_SL_FILL_{int(time.time())}",
                total_crypto * current_price *
                self.strategy.market_sell_fee_percent / 100,
                total_invested, profit_idr, profit_pct, 'STOP_LOSS',
            )

        self.db.close_position(self.bot_id, 'CLOSED')
        self._log(LogEvent.STOP_LOSS, f"Stop loss completed")

    def _submit_stop_loss(self, position: dict, total_crypto: float,
                          current_price: float, total_invested: float,
                          intent: Optional[dict] = None):
        if intent is None:
            intent = self.db.get_recoverable_order(
                position.get('id', ''), 'stop_loss', 0)
        if intent:
            remote = self._recover_order_intent(intent)
            if remote:
                exchange_id = str(remote.get('order_id') or
                                  intent.get('exchange_order_id') or '')
                position['exit_order_id'] = exchange_id
                position['exit_reason'] = 'STOP_LOSS'
                self.db.save_position(position)
                self._handle_exit_status(position, intent, remote, exchange_id)
                return
            total_crypto = float(intent.get('amount', total_crypto) or total_crypto)
            current_price = float(intent.get('price', current_price) or current_price)
        else:
            intent = self._create_order_intent(
                position, 'stop_loss', 'sell', current_price, total_crypto,
                total_crypto * current_price)

        result = self.client.sell_market(
            self.pair, total_crypto, intent['client_order_id'])
        if isinstance(result, dict) and result.get('error'):
            self._record_api_failure('stop_loss_submit', result['error'])
            recovered = self.client.get_order_by_client_id(
                self.pair, intent['client_order_id'])
            if isinstance(recovered, dict) and not recovered.get('error'):
                result = recovered
                self._record_api_success('stop_loss_submit')
            elif self._submission_is_ambiguous(result['error']):
                self.db.update_order_status(intent['id'], 'SUBMISSION_UNKNOWN')
                position['exit_reason'] = 'STOP_LOSS'
                self.db.save_position(position)
                self._log(LogEvent.API_ERROR,
                          "Stop-loss ACK unknown; durable intent retained",
                          level="ERROR")
                return
            else:
                self.db.update_order_status(intent['id'], 'FAILED')
                self._log(LogEvent.ORDER_FAILED,
                          f"Stop loss sell failed: {result['error']}", level="ERROR")
                return
        else:
            self._record_api_success('stop_loss_submit')

        exchange_id = str(result.get('order_id', ''))
        if not exchange_id:
            self.db.update_order_status(intent['id'], 'SUBMISSION_UNKNOWN')
            position['exit_reason'] = 'STOP_LOSS'
            self.db.save_position(position)
            return
        status_payload = result
        if not result.get('status'):
            fetched = self.client.get_order_status(self.pair, exchange_id)
            if isinstance(fetched, dict) and not fetched.get('error'):
                status_payload = fetched
        self.db.update_order_submission(
            intent['id'], exchange_id,
            str(status_payload.get('status', 'OPEN')).upper())
        position['exit_order_id'] = exchange_id
        position['exit_reason'] = 'STOP_LOSS'
        self.db.save_position(position)
        self._handle_exit_status(position, intent, status_payload, exchange_id)

    def _reconcile_exit_order(self, position: dict):
        intent = self.db.get_recoverable_order(
            position.get('id', ''), 'stop_loss', 0)
        if not intent:
            self._record_api_failure(
                'state_reconciliation',
                'exit marker has no recoverable stop-loss intent',
                force_open=True)
            self._log(LogEvent.BOT_ERROR,
                      "Exit marker has no recoverable stop-loss intent",
                      level="ERROR")
            return
        remote = self._recover_order_intent(intent)
        if remote:
            exchange_id = str(remote.get('order_id') or
                              intent.get('exchange_order_id') or '')
            position['exit_order_id'] = exchange_id
            position['exit_reason'] = 'STOP_LOSS'
            self.db.save_position(position)
            self._handle_exit_status(position, intent, remote, exchange_id)
            return
        if intent.get('status') in ('REQUESTED', 'SUBMISSION_UNKNOWN'):
            remaining = max(
                float(position.get('total_amount', 0) or 0) -
                float(position.get('sold_amount', 0) or 0), 0)
            remaining_cost = float(position.get('total_invested', 0) or 0)
            self._submit_stop_loss(
                position, remaining, float(intent.get('price', 0) or 0),
                remaining_cost, intent)
            return
        self._record_api_failure(
            'state_reconciliation',
            'stop-loss order cannot be reconciled automatically',
            force_open=True)
        self._log(LogEvent.API_ERROR,
                  "Stop-loss order cannot be reconciled automatically",
                  level="ERROR")

    def _handle_exit_status(self, position: dict, intent: dict, payload: dict,
                            exchange_order_id: str):
        status = str(payload.get('status', '')).lower()
        is_final = status in ('filled', 'done', 'closed')
        is_cancelled = status in ('cancelled', 'canceled', 'rejected', 'expired')
        coin = self.pair.lower().replace('_', '').replace('idr', '')
        cumulative = float(
            payload.get('filled_amount', 0) or
            payload.get(f'spend_{coin}', 0) or
            payload.get('spend', 0) or 0)
        totals = self.db.get_order_trade_totals(
            position.get('id', ''), exchange_order_id, 'sell')
        already_amount = float(totals.get('amount', 0) or 0)
        if is_final and cumulative <= 0:
            cumulative = float(intent.get('amount', 0) or 0)
        delta = max(cumulative - already_amount, 0)
        price = float(payload.get('price') or payload.get('average_price') or
                      intent.get('price') or 0)

        if delta > 1e-12:
            cumulative_gross = float(payload.get('filled_quote', 0) or 0)
            cumulative_net = float(payload.get('receive_idr', 0) or 0)
            already_net = float(totals.get('amount_quote', 0) or 0)
            already_fee = float(totals.get('fee', 0) or 0)
            if cumulative_gross > 0:
                gross_delta = max(
                    cumulative_gross - already_net - already_fee, 0)
                fee = gross_delta * self.strategy.market_sell_fee_percent / 100
                net_delta = gross_delta - fee
            elif cumulative_net > 0:
                net_delta = max(cumulative_net - already_net, 0)
                fee = net_delta * self.strategy.market_sell_fee_percent / max(
                    100 - self.strategy.market_sell_fee_percent, 0.000001)
            else:
                gross_delta = delta * price
                fee = gross_delta * self.strategy.market_sell_fee_percent / 100
                net_delta = gross_delta - fee
            total_amount = float(position.get('total_amount', 0) or 0)
            cost_basis = (float(position.get('total_invested', 0) or 0) *
                          delta / total_amount) if total_amount > 0 else 0
            profit = net_delta - cost_basis
            profit_pct = profit / cost_basis * 100 if cost_basis > 0 else 0
            sold_after = min(
                float(position.get('sold_amount', 0) or 0) + delta,
                total_amount)
            exhausted = (total_amount - sold_after) <= max(
                total_amount * 1e-8, 1e-8)
            close_reason = 'STOP_LOSS' if is_final and exhausted else ''
            self._record_trade(
                position, 'sell',
                'stop_loss' if close_reason else 'partial_stop_loss',
                price, delta, net_delta, exchange_order_id, fee, cost_basis,
                profit, profit_pct, close_reason,
                trade_id=self._fill_trade_id(
                    position.get('id', ''), exchange_order_id, 'sell',
                    cumulative, cumulative_gross or cumulative_net or delta * price))
            position['sold_amount'] = sold_after
        else:
            total_amount = float(position.get('total_amount', 0) or 0)
            exhausted = (total_amount - float(position.get('sold_amount', 0) or 0)) <= max(
                total_amount * 1e-8, 1e-8)

        if is_final:
            self.db.update_order_submission(intent['id'], exchange_order_id, 'FILLED')
            if not exhausted:
                position['exit_order_id'] = None
                position['exit_reason'] = ''
        elif is_cancelled:
            self.db.update_order_submission(
                intent['id'], exchange_order_id, status.upper())
            position['exit_order_id'] = None
            position['exit_reason'] = ''
        elif delta > 1e-12:
            self.db.update_order_submission(
                intent['id'], exchange_order_id, 'PARTIALLY_FILLED')
        self.db.save_position(position)

        if is_final and exhausted:
            self.db.close_position(self.bot_id, 'CLOSED')
            self._log(LogEvent.STOP_LOSS, "Stop loss filled; position closed")
        elif delta > 1e-12:
            self._log(LogEvent.STOP_LOSS,
                      f"Stop loss partially filled: {delta:.8f}; reconciliation continues")

    def _sync_and_manage_orders(self, state: dict, current_price: float,
                                replace_missing: bool = True):
        """Reconcile cumulative exchange fills without duplicating trades."""
        if self.dry_run:
            self._simulate_safety_orders(state.get('open_orders', []), current_price)
            return

        exchange_orders = self.client.get_open_orders(self.pair)
        if isinstance(exchange_orders, dict) and 'error' in exchange_orders:
            self._record_api_failure('open_orders', exchange_orders['error'])
            self._log(LogEvent.API_ERROR,
                      f"Failed to get open orders: {exchange_orders['error']}",
                      level="WARNING")
            return
        self._record_api_success('open_orders')
        exchange_order_map = {
            str(order.get('order_id', '')): order
            for order in exchange_orders if isinstance(order, dict)
        } if isinstance(exchange_orders, list) else {}

        position = self.db.get_position(self.bot_id)
        if not position:
            return

        tp_order_id = str(position.get('tp_order_id') or '')
        if tp_order_id:
            tp_status = exchange_order_map.get(tp_order_id)
            if not tp_status:
                tp_status = self.client.get_order_status(self.pair, tp_order_id)
            tp_text = str(tp_status.get('status', '')).lower() \
                if isinstance(tp_status, dict) else ''
            tp_final = tp_text in ('filled', 'done', 'closed')
            tp_terminal_cancel = tp_text in (
                'cancelled', 'canceled', 'rejected', 'expired')
            tp_partial = tp_text in ('partially_filled', 'partial') or (
                isinstance(tp_status, dict) and
                float(tp_status.get('filled_amount', 0) or 0) > 0
            )
            if tp_final or tp_partial:
                closed = self._apply_tp_fill(
                    position, tp_status, tp_order_id, tp_final, current_price)
                if closed:
                    self._cancel_all_orders()
                    self.db.close_position(self.bot_id, 'CLOSED')
                    return
                position = self.db.get_position(self.bot_id)
                if not position:
                    return
                if tp_terminal_cancel:
                    # Preserve the terminal order's cumulative fill, then
                    # release its stale ID so the remaining inventory gets a
                    # fresh TP below in this same reconciliation tick.
                    self.db.update_order_status_by_exchange_id(
                        tp_order_id, tp_text.upper())
                    position['tp_order_id'] = None
                    self.db.save_position(position)
            elif tp_terminal_cancel:
                self.db.update_order_status_by_exchange_id(tp_order_id, tp_text.upper())
                position['tp_order_id'] = None
                self.db.save_position(position)

        remaining = []
        so_changed = False
        for order in position.get('open_orders', []):
            oid = str(order.get('order_id', ''))
            order_status = exchange_order_map.get(oid)
            if not order_status:
                order_status = self.client.get_order_status(self.pair, oid)
            status_text = str(order_status.get('status', '')).lower() \
                if isinstance(order_status, dict) else ''
            is_final = status_text in ('filled', 'done', 'closed')
            is_partial = status_text in ('partially_filled', 'partial') or (
                isinstance(order_status, dict) and
                float(order_status.get('filled_amount', 0) or 0) > 0
            )
            if is_final or is_partial:
                so_changed = self._apply_so_fill(
                    position, order, order_status, is_final) or so_changed
            if is_final:
                self.db.update_order_status_by_exchange_id(oid, 'FILLED')
            elif status_text in ('cancelled', 'canceled', 'rejected', 'expired'):
                self.db.update_order_status_by_exchange_id(oid, status_text.upper())
                self._log(LogEvent.ORDER_FAILED,
                          f"SO{order.get('so_number', 0)} is {status_text}; partial fill preserved",
                          level="WARNING")
            else:
                remaining.append(order)

        position['open_orders'] = remaining
        if so_changed:
            so_entries = position.get('so_entries', [])
            average_entry = self.strategy.calculate_average_entry(
                position.get('base_price', 0), position.get('base_amount', 0), so_entries)
            position['average_entry_price'] = average_entry
            position['take_profit_price'] = self.strategy.get_tp_price(average_entry)
            position['stop_loss_price'] = (
                self.strategy.get_sl_price(average_entry)
                if self.strategy.stop_loss_percent > 0 else 0
            )
            self.db.save_position(position)
            self._cancel_tp_order(position)
            self.db.save_position(position)
            if replace_missing:
                self._place_tp_order(position)
        self.db.save_position(position)

        if replace_missing and not position.get('tp_order_id'):
            self._place_tp_order(position)
            self.db.save_position(position)

        finalized_numbers = {
            int(entry.get('number') or entry.get('step') or 0)
            for entry in position.get('so_entries', [])
            if entry.get('finalized', True)
        }
        if replace_missing:
            self._place_missing_so_orders(
                position.get('open_orders', []), finalized_numbers)

    @staticmethod
    def _fill_trade_id(position_id: str, order_id: str, side: str,
                       cumulative_amount: float, cumulative_quote: float) -> str:
        marker = (f"{position_id}|{order_id}|{side}|"
                  f"{cumulative_amount:.12f}|{cumulative_quote:.4f}")
        return f"trade_fill_{hashlib.sha256(marker.encode()).hexdigest()[:24]}"

    def _apply_so_fill(self, position: dict, order: dict, status: dict,
                       is_final: bool) -> bool:
        """Apply only the unrecorded delta of one cumulative SO fill."""
        oid = str(order.get('order_id', ''))
        so_number = int(order.get('so_number', 0) or 0)
        fee_rate = self.strategy.limit_buy_fee_percent / 100
        gross_filled = float(status.get('filled_amount', 0) or 0)
        if is_final and gross_filled <= 0:
            gross_filled = float(order.get('gross_amount_crypto') or 0)
            if gross_filled <= 0:
                recorded_net = float(order.get('amount_crypto') or 0)
                gross_filled = (recorded_net / (1 - fee_rate)
                                if fee_rate < 1 else recorded_net)
        price = float(status.get('price') or order.get('price') or 0)
        quote_filled = float(status.get('filled_quote', 0) or 0)
        if quote_filled <= 0:
            quote_filled = gross_filled * price
        net_filled = gross_filled * (1 - fee_rate)

        so_entries = position.get('so_entries', [])
        entry = next((item for item in so_entries
                      if str(item.get('order_id', '')) == oid), None)
        if entry is None:
            entry = {
                'number': so_number, 'price': price, 'amount_idr': 0,
                'amount_crypto': 0, 'order_id': oid,
                'timestamp': utc_now_iso(), 'finalized': False,
            }
            so_entries.append(entry)

        previous_net = float(entry.get('amount_crypto', 0) or 0)
        previous_quote = float(entry.get('amount_idr', 0) or 0)
        delta_net = max(net_filled - previous_net, 0)
        delta_quote = max(quote_filled - previous_quote, 0)
        changed = delta_net > 1e-12 or delta_quote > 0.0001
        if changed:
            delta_gross = delta_net / (1 - fee_rate) if fee_rate < 1 else delta_net
            delta_price = delta_quote / delta_gross if delta_gross > 0 else price
            trade_type = f"so_{so_number}" if is_final else f"partial_so_{so_number}"
            self._record_trade(
                position, 'buy', trade_type, delta_price, delta_net,
                delta_quote, oid, delta_quote * fee_rate,
                executed_at=utc_now_iso(),
                trade_id=self._fill_trade_id(
                    position.get('id', ''), oid, 'buy', gross_filled, quote_filled),
            )
            position['total_invested'] = float(position.get('total_invested', 0) or 0) + delta_quote
            position['total_amount'] = float(position.get('total_amount', 0) or 0) + delta_net
            entry.update({
                'price': price or entry.get('price', 0),
                'amount_idr': quote_filled,
                'amount_crypto': net_filled,
                'timestamp': utc_now_iso(),
            })

        entry['finalized'] = bool(is_final)
        position['so_entries'] = so_entries
        if is_final:
            finalized_count = sum(1 for item in so_entries
                                  if item.get('finalized', True))
            self.db.sync_cycle_safety_order_count(position.get('id', ''), finalized_count)
            self._log(LogEvent.DCA_ENTRY,
                      f"SO{so_number} filled @ Rp {price:,.0f} | Total: {finalized_count}/{self.strategy.max_safety_orders}")
        elif changed:
            self.db.update_order_status_by_exchange_id(oid, 'PARTIALLY_FILLED')
            self._log(LogEvent.DCA_ENTRY,
                      f"SO{so_number} partially filled: {gross_filled:.8f} @ Rp {price:,.0f}")
        return changed or is_final

    def _apply_tp_fill(self, position: dict, status: dict, order_id: str,
                       is_final: bool, current_price: float) -> bool:
        """Record a TP delta and return True only when inventory is exhausted."""
        totals = self.db.get_order_trade_totals(position.get('id', ''), order_id, 'sell')
        already_recorded = float(totals.get('amount', 0) or 0)
        cumulative = float(status.get('filled_amount', 0) or 0)
        remaining_inventory = max(
            float(position.get('total_amount', 0) or 0) -
            float(position.get('sold_amount', 0) or 0), 0)
        if is_final and cumulative <= 0:
            cumulative = already_recorded + remaining_inventory
        delta = max(cumulative - already_recorded, 0)
        price = float(status.get('price') or position.get('take_profit_price') or current_price)

        if delta > 1e-12:
            cumulative_quote = float(status.get('filled_quote', 0) or 0)
            already_gross_quote = (
                float(totals.get('amount_quote', 0) or 0) +
                float(totals.get('fee', 0) or 0)
            )
            gross_value = max(cumulative_quote - already_gross_quote, 0)
            if gross_value <= 0:
                gross_value = delta * price
            fee = gross_value * self.strategy.limit_sell_fee_percent / 100
            net_value = gross_value - fee
            total_amount = float(position.get('total_amount', 0) or 0)
            cost_basis = (float(position.get('total_invested', 0) or 0) *
                          delta / total_amount) if total_amount > 0 else 0
            profit = net_value - cost_basis
            profit_pct = profit / cost_basis * 100 if cost_basis > 0 else 0
            sold_after = min(float(position.get('sold_amount', 0) or 0) + delta,
                             total_amount)
            exhausted = (total_amount - sold_after) <= max(total_amount * 1e-8, 1e-8)
            close_reason = 'TAKE_PROFIT' if is_final and exhausted else ''
            self._record_trade(
                position, 'sell', 'take_profit' if close_reason else 'partial_take_profit',
                price, delta, net_value, order_id, fee, cost_basis, profit,
                profit_pct, close_reason,
                trade_id=self._fill_trade_id(
                    position.get('id', ''), order_id, 'sell', cumulative,
                    float(status.get('filled_quote', 0) or cumulative * price)),
            )
            position['sold_amount'] = sold_after
            self.db.save_position(position)
        else:
            total_amount = float(position.get('total_amount', 0) or 0)
            exhausted = (total_amount - float(position.get('sold_amount', 0) or 0)) <= \
                max(total_amount * 1e-8, 1e-8)

        if is_final:
            self.db.update_order_status_by_exchange_id(order_id, 'FILLED')
            position['tp_order_id'] = None
            self.db.save_position(position)
        else:
            self.db.update_order_status_by_exchange_id(order_id, 'PARTIALLY_FILLED')

        if is_final and exhausted:
            self._log(LogEvent.TAKE_PROFIT,
                      f"TP filled on exchange; position inventory exhausted")
            return True
        if delta > 1e-12:
            self._log(LogEvent.TAKE_PROFIT,
                      f"TP partially filled: {delta:.8f} @ Rp {price:,.0f}; remainder stays managed")
        return False

    def _simulate_safety_orders(self, open_orders: list, current_price: float):
        """Fill simulated safety orders and TP from price only; never query exchange."""
        position = self.db.get_position(self.bot_id)
        if not position:
            return

        # Check Dry Run TP Limit Sell completion
        tp_price = float(position.get('take_profit_price', 0) or 0)
        if tp_price > 0 and current_price >= tp_price:
            total_crypto = float(position.get('total_amount', 0) or 0)
            total_invested = float(position.get('total_invested', 0) or 0)
            gross_value = total_crypto * tp_price
            fee = gross_value * self.strategy.limit_sell_fee_percent / 100
            net_value = gross_value - fee
            profit_idr = net_value - total_invested
            profit_pct = (profit_idr / total_invested * 100) if total_invested > 0 else 0

            tp_order_id = position.get('tp_order_id') or f"DRY_TP_FILL_{int(time.time())}"
            self.db.update_order_status_by_exchange_id(
                tp_order_id, 'FILLED')
            self._record_trade(
                position, 'sell', 'take_profit', tp_price, total_crypto,
                net_value, tp_order_id, fee, total_invested, profit_idr,
                profit_pct, 'TAKE_PROFIT',
            )
            self.db.close_position(self.bot_id, 'CLOSED')
            self._log(
                LogEvent.TAKE_PROFIT,
                f"[DRY RUN] TP Limit Sell FILLED @ Rp {tp_price:,.0f}. Profit: {profit_pct:+.2f}% "
                f"(Rp {profit_idr:,.0f}); posisi ditutup & menunggu re-entry RSI",
            )
            return

        triggered = [order for order in open_orders
                     if current_price <= float(order.get('price', 0))]
        if not triggered:
            return

        remaining = [order for order in open_orders if order not in triggered]
        so_entries = position.get('so_entries', [])
        for order in triggered:
            so_number = order.get('so_number', 0)
            amount_idr = float(order.get('amount_idr', 0))
            amount_crypto = float(order.get('amount_crypto', 0))
            price = float(order.get('price', 0))
            so_entries.append({
                'number': so_number, 'price': price,
                'amount_idr': amount_idr, 'amount_crypto': amount_crypto,
                'order_id': order.get('order_id', ''),
                'timestamp': utc_now_iso(),
            })
            self._record_trade(
                position, 'buy', f"so_{so_number}", price, amount_crypto,
                amount_idr, order.get('order_id', ''),
                amount_idr * self.strategy.limit_buy_fee_percent / 100,
                executed_at=so_entries[-1]['timestamp'],
            )
            self.db.update_order_status_by_exchange_id(
                order.get('order_id', ''), 'FILLED')
            self._log(LogEvent.DCA_ENTRY,
                      f"[DRY RUN] SO{so_number} simulated at Rp {price:,.0f}")

        position['so_entries'] = so_entries
        position['total_invested'] = float(position.get('total_invested', 0)) + sum(
            float(order.get('amount_idr', 0)) for order in triggered)
        position['total_amount'] = float(position.get('total_amount', 0)) + sum(
            float(order.get('amount_crypto', 0)) for order in triggered)
        position['open_orders'] = remaining
        position['average_entry_price'] = self.strategy.calculate_average_entry(
            position.get('base_price', 0), position.get('base_amount', 0), so_entries)
        position['take_profit_price'] = self.strategy.get_tp_price(position['average_entry_price'])
        position['stop_loss_price'] = (
            self.strategy.get_sl_price(position['average_entry_price'])
            if self.strategy.stop_loss_percent > 0 else 0
        )
        self._place_tp_order(position)
        self.db.save_position(position)

    def _place_missing_so_orders(self, current_open_orders: list,
                                 filled_numbers):
        """Place safety orders that should exist but are missing"""
        placed_so_nums = {o.get('so_number') for o in current_open_orders if o.get('so_number')}
        position = self.db.get_position(self.bot_id)

        if not position:
            return

        if isinstance(filled_numbers, int):
            filled_numbers = set(range(1, filled_numbers + 1))
        else:
            filled_numbers = {int(number) for number in filled_numbers}
        for so_num in range(1, self.strategy.max_safety_orders + 1):
            if so_num not in placed_so_nums and so_num not in filled_numbers:
                self._place_so_order(position, so_num)
                self.db.save_position(position)

    def _cancel_all_orders(self):
        """Cancel only the TP/SO ids persisted for this bot."""
        if self.dry_run:
            # Simulation only: clear local pseudo-orders.
            position = self.db.get_position(self.bot_id)
            for intent in self.db.get_open_orders(self.bot_id):
                self.db.update_order_status(intent['id'], 'CANCELLED')
            if position:
                position['open_orders'] = []
                position['tp_order_id'] = None
                position['exit_order_id'] = None
                position['exit_reason'] = ''
                self.db.save_position(position)
            self.db.resolve_alert(f'order-cancel:{self.bot_id}')
            return

        # Cancel tracked orders in position
        position = self.db.get_position(self.bot_id)
        failed_exchange_ids = set()
        failed_references = set()
        if position:
            cancelled_exchange_ids = set()
            if position.get('status') == 'PENDING_BASE':
                base_intent = self.db.get_pending_base_order(
                    self.bot_id, position.get('id', ''))
                if base_intent:
                    base_oid = str(base_intent.get('exchange_order_id') or '')
                    try:
                        if base_oid:
                            result = self.client.cancel_order(
                                self.pair, base_oid, 'buy')
                        else:
                            result = self.client.cancel_order_by_client_id(
                                base_intent.get('client_order_id', ''))
                        if not isinstance(result, dict) or not result.get('error'):
                            self.db.update_order_status(
                                base_intent['id'], 'CANCELLED')
                        else:
                            failed_references.add(
                                base_oid or base_intent.get('client_order_id', ''))
                    except Exception as e:
                        failed_references.add(
                            base_oid or base_intent.get('client_order_id', ''))
                        self._logger.warning(
                            f"Error cancelling pending base intent: {e}")
            for order in position.get('open_orders', []):
                oid = str(order.get('order_id', ''))
                if oid:
                    try:
                        result = self.client.cancel_order(self.pair, oid, 'buy')
                        if not isinstance(result, dict) or not result.get('error'):
                            self.db.update_order_status_by_exchange_id(oid, 'CANCELLED')
                            cancelled_exchange_ids.add(oid)
                        else:
                            failed_exchange_ids.add(oid)
                            failed_references.add(oid)
                    except Exception as e:
                        failed_exchange_ids.add(oid)
                        failed_references.add(oid)
                        self._logger.warning(f"Error cancelling SO order {oid}: {e}")
            tp_order_id = str(position.get('tp_order_id') or '')
            if tp_order_id:
                try:
                    result = self.client.cancel_order(self.pair, tp_order_id, 'sell')
                    if not isinstance(result, dict) or not result.get('error'):
                        self.db.update_order_status_by_exchange_id(tp_order_id, 'CANCELLED')
                        cancelled_exchange_ids.add(tp_order_id)
                    else:
                        failed_exchange_ids.add(tp_order_id)
                        failed_references.add(tp_order_id)
                except Exception as e:
                    failed_exchange_ids.add(tp_order_id)
                    failed_references.add(tp_order_id)
                    self._logger.warning(f"Error cancelling TP order {tp_order_id}: {e}")

            exit_order_id = str(position.get('exit_order_id') or '')
            if exit_order_id and exit_order_id not in cancelled_exchange_ids:
                try:
                    result = self.client.cancel_order(
                        self.pair, exit_order_id, 'sell')
                    if not isinstance(result, dict) or not result.get('error'):
                        self.db.update_order_status_by_exchange_id(
                            exit_order_id, 'CANCELLED')
                        cancelled_exchange_ids.add(exit_order_id)
                    else:
                        failed_exchange_ids.add(exit_order_id)
                        failed_references.add(exit_order_id)
                except Exception as e:
                    failed_exchange_ids.add(exit_order_id)
                    failed_references.add(exit_order_id)
                    self._logger.warning(
                        f"Error cancelling exit order {exit_order_id}: {e}")

            # A crash can occur after the intent/exchange ACK but before the
            # position JSON stores the exchange id. Recover and cancel those
            # durable child intents as well, without scanning unrelated pair
            # orders.
            for intent in self.db.get_recoverable_child_orders(
                    position.get('id', '')):
                oid = str(intent.get('exchange_order_id') or '')
                if oid in cancelled_exchange_ids:
                    continue
                if not oid:
                    remote = self._recover_order_intent(intent)
                    oid = str(remote.get('order_id', '')) if remote else ''
                try:
                    if oid:
                        result = self.client.cancel_order(
                            self.pair, oid, intent.get('side', ''))
                    else:
                        result = self.client.cancel_order_by_client_id(
                            intent.get('client_order_id', ''))
                    if not isinstance(result, dict) or not result.get('error'):
                        self.db.update_order_status(intent['id'], 'CANCELLED')
                        if oid:
                            cancelled_exchange_ids.add(oid)
                            failed_exchange_ids.discard(oid)
                            failed_references.discard(oid)
                        failed_references.discard(
                            intent.get('client_order_id', ''))
                    else:
                        failed_references.add(
                            oid or intent.get('client_order_id', ''))
                        if oid:
                            failed_exchange_ids.add(oid)
                except Exception as e:
                    failed_references.add(
                        oid or intent.get('client_order_id', ''))
                    if oid:
                        failed_exchange_ids.add(oid)
                    self._logger.warning(
                        f"Error cancelling recovered intent {intent.get('client_order_id')}: {e}")

        # Clear only cancellation successes. Failed IDs remain durable so a
        # later worker/recovery attempt can retry them safely.
        if position:
            position['open_orders'] = [
                order for order in position.get('open_orders', [])
                if str(order.get('order_id') or '') in failed_exchange_ids
            ]
            if str(position.get('tp_order_id') or '') not in failed_exchange_ids:
                position['tp_order_id'] = None
            if str(position.get('exit_order_id') or '') not in failed_exchange_ids:
                position['exit_order_id'] = None
                position['exit_reason'] = ''
            self.db.save_position(position)
        try:
            if failed_references:
                self.db.raise_alert(
                    kind='ORDER_CANCELLATION_FAILED',
                    dedupe_key=f'order-cancel:{self.bot_id}',
                    severity='CRITICAL',
                    account_id=self.account_id,
                    bot_id=self.bot_id,
                    message=(
                        'Pembatalan order belum terkonfirmasi; bot dihentikan '
                        'dan ID order dipertahankan untuk recovery'
                    ),
                    metadata={'failed_count': len(failed_references)},
                )
            else:
                self.db.resolve_alert(f'order-cancel:{self.bot_id}')
        except Exception as error:
            self._logger.warning(
                f"Error persisting cancellation alert: {error}")

    def _cancel_tp_order(self, position: dict):
        """Cancel TP order"""
        tp_order_id = str(position.get('tp_order_id') or '')
        if tp_order_id and not self.dry_run:
            result = self.client.cancel_order(self.pair, tp_order_id, 'sell')
            if not isinstance(result, dict) or not result.get('error'):
                self.db.update_order_status_by_exchange_id(tp_order_id, 'CANCELLED')
        position['tp_order_id'] = None
