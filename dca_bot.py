"""
Exbot - Full Trading Bot
Safety Order + Take Profit + Martingale + RSI Re-entry
"""

import time
import json
import logging
import sys
import math
import importlib
import config
from datetime import datetime
from typing import Any
from indodax_client import IndodaxClient
from config import (
    INDODAX_API_KEY,
    INDODAX_SECRET_KEY,
    BASE_ORDER_IDR,
    SAFETY_ORDER_IDR,
    MAX_SAFETY_ORDERS,
    SAFETY_ORDER_DISTANCE,
    TAKE_PROFIT_PERCENT,
    STOP_LOSS_PERCENT,
    MARTINGALE_ENABLED,
    VOLUME_SCALE,
    STEP_SCALE,
    TRADING_PAIR,
    DRY_RUN,
    LOG_FILE,
    DATA_FILE,
    RSI_PERIOD,
    RSI_OVERSOLD,
)

# Setup logging
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class DCABot:
    def __init__(self):
        self.client = IndodaxClient(INDODAX_API_KEY, INDODAX_SECRET_KEY)
        self.pair = TRADING_PAIR
        self.base_order = BASE_ORDER_IDR
        self.so_order = SAFETY_ORDER_IDR
        self.max_so = MAX_SAFETY_ORDERS
        self.so_distance = SAFETY_ORDER_DISTANCE
        self.tp_percent = TAKE_PROFIT_PERCENT
        self.sl_percent = STOP_LOSS_PERCENT
        self.martingale = MARTINGALE_ENABLED
        self.volume_scale = VOLUME_SCALE
        self.step_scale = STEP_SCALE
        self.dry_run = DRY_RUN
        self.data_file = DATA_FILE
        self.rsi_period = RSI_PERIOD
        self.rsi_oversold = RSI_OVERSOLD

        # Trade state
        self.state: dict[str, Any] = self.load_state()
        self.ensure_state()

        logger.info("=" * 50)
        logger.info("🚀 Exbot DCA Bot Started (Full Mode)")
        logger.info(f"Pair: {self.pair.upper()}")
        logger.info(f"Base Order: Rp {self.base_order:,.0f}")
        logger.info(f"SO Amount: Rp {self.so_order:,.0f} | Max SO: {self.max_so}")
        logger.info(f"SO Distance: {self.so_distance}% | Step Scale: {self.step_scale}x")
        logger.info(f"TP: {self.tp_percent}% | SL: {self.sl_percent}%")
        logger.info(f"Martingale: {'ON' if self.martingale else 'OFF'} | Volume Scale: {self.volume_scale}x")
        logger.info(f"RSI Period: {self.rsi_period} | Oversold: {self.rsi_oversold}")
        logger.info(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        logger.info("=" * 50)

    def ensure_state(self):
        """Ensure state has all required keys"""
        defaults = {
            'active_position': False,
            'base_price': 0,
            'base_amount_crypto': 0,
            'so_entries': [],  # [{price, amount_idr, amount_crypto, step, order_id}]
            'tp_price': 0,
            'sl_price': 0,
            'total_invested': 0,
            'total_crypto_bought': 0,
            'trades': [],
            'pending_new_entry': True,  # True = menunggu re-entry setelah TP
            'open_orders': [],  # [{order_id, type, price, amount_idr, so_number}]
        }
        for key, default_val in defaults.items():
            if key not in self.state:
                self.state[key] = default_val
        self.save_state()

    def load_state(self) -> dict[str, Any]:
        """Load trade state from JSON file"""
        try:
            with open(self.data_file, 'r') as f:
                data = json.load(f)
                logger.info(f"State loaded from {self.data_file}")
                return data
        except FileNotFoundError:
            logger.info("No existing state, creating new")
            return self.default_state()

    def default_state(self) -> dict[str, Any]:
        return {
            'active_position': False,
            'base_price': 0,
            'base_amount_crypto': 0,
            'so_entries': [],
            'tp_price': 0,
            'sl_price': 0,
            'total_invested': 0,
            'total_crypto_bought': 0,
            'trades': [],
            'pending_new_entry': True,
        }

    def save_state(self):
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def get_current_price(self) -> float | None:
        """Get current market price"""
        ticker: dict[str, Any] = self.client.get_ticker(self.pair)
        if not isinstance(ticker, dict) or 'error' in ticker:
            logger.error(f"Ticker fetch failed: {ticker.get('error') if isinstance(ticker, dict) else ticker}")
            return None
        return float(ticker.get('last', 0))

    def calculate_rsi(self, period: int | None = None) -> float | None:
        """Calculate RSI from OHLC data"""
        if period is None:
            period = self.rsi_period

        candles = self.client.get_ohlc(self.pair, '1h', period + 10)
        if isinstance(candles, dict):
            logger.error(f"Failed to get OHLC for RSI: {candles.get('error')}")
            return None

        if not isinstance(candles, list) or len(candles) < period + 1:
            return None

        closes: list[float] = [float(c['close']) for c in candles]
        gains = []
        losses = []

        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains.append(diff)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(diff))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)

    def calculate_avg_entry(self) -> float:
        """Calculate average entry price"""
        base_idr = 0
        base_crypto = 0
        for t in reversed(self.state.get('trades', [])):
            if t.get('type') == 'base':
                base_idr = float(t.get('amount_idr', 0))
                base_crypto = float(t.get('amount_crypto', t.get('crypto_amount', 0)))
                break

        if base_idr <= 0 or base_crypto <= 0:
            # Fallback to calculated amount based on base price and crypto quantity
            base_idr = float(self.state.get('base_price', 0)) * float(self.state.get('base_amount_crypto', 0))
            base_crypto = float(self.state.get('base_amount_crypto', 0))

        if base_idr <= 0 or base_crypto <= 0:
            # Fallback to config base order
            base_idr = float(self.base_order)
            base_price = float(self.state.get('base_price', 0)) or float(self.get_current_price() or 1)
            base_crypto = base_idr / base_price

        invested: float = base_idr
        crypto: float = base_crypto

        for so in self.state['so_entries']:
            invested += so['amount_idr']
            crypto += so['amount_crypto']

        if crypto > 0:
            return invested / crypto
        return 0

    def start_bot(self):
        """Start bot - execute base order at market, place TP sell limit and all SO buy limits"""
        if self.state['active_position']:
            logger.info("Bot already has active position")
            return

        current_price = self.get_current_price()
        if not current_price or current_price <= 0:
            logger.error("Cannot get current price, aborting start")
            return

        logger.info("=" * 50)
        logger.info("🟢 STARTING BOT - Executing Base Order (Market Buy)")
        logger.info(f"Current Price: Rp {current_price:,.0f}")

        # Set position state first
        self.state['active_position'] = True
        self.state['base_price'] = current_price
        self.state['pending_new_entry'] = False
        self.state['so_entries'] = []
        self.state['open_orders'] = []
        self.state['total_invested'] = 0
        self.state['total_crypto_bought'] = 0
        self.state['tp_order_id'] = None

        # --- 1. Buy BASE order at market price ---
        if self.dry_run:
            order_id = f"DRY_BASE_{int(time.time())}"
            price = current_price
            crypto_amount = self.base_order / price
            logger.info(f"[DRY RUN] Base Market Buy: {crypto_amount:.8f} @ Rp {price:,.0f}")
        else:
            result = self.client.buy_market(self.pair, self.base_order)
            if 'error' in result:
                logger.error(f"Base market buy failed: {result['error']}")
                self.state['active_position'] = False
                self.state['pending_new_entry'] = True
                self.save_state()
                return
            order_id = str(result.get('order_id', 'N/A'))
            price = float(result.get('price', 0)) or current_price
            crypto_amount = float(result.get('receive_btc', 0)) or (self.base_order / price)
            logger.info(f"✅ Base Market Buy filled! Order ID: {order_id} @ Rp {price:,.0f} -> {crypto_amount:.8f} crypto")

        self.state['total_invested'] = self.base_order
        self.state['total_crypto_bought'] = crypto_amount
        self.state['base_amount_crypto'] = crypto_amount
        self.state['base_price'] = price

        # Calculate TP price
        self.state['tp_price'] = price * (1 + self.tp_percent / 100)
        if self.sl_percent > 0:
            self.state['sl_price'] = price * (1 - self.sl_percent / 100)

        # Record base trade
        self.state['trades'].append({
            'timestamp': datetime.now().isoformat(),
            'type': 'base',
            'price': price,
            'amount_idr': self.base_order,
            'amount_crypto': crypto_amount,
            'order_id': str(order_id),
            'dry_run': self.dry_run,
        })

        # --- 2. Place TP Limit Sell Order on Exchange ---
        self.place_tp_order()

        # --- 3. Place ALL Safety Orders as buy limit orders ---
        so_log_lines = []
        for so_num in range(1, self.max_so + 1):
            so_price_raw = self.get_so_price(so_num)
            so_price = self.round_price(so_price_raw, 'buy')

            # Calculate amount with optional martingale
            amount = self.so_order
            if self.martingale:
                amount = self.so_order * (self.volume_scale ** (so_num - 1))

            so_crypto_amount = amount / so_price

            if self.dry_run:
                so_order_id = f"DRY_SO_{so_num}_{int(time.time())}"
                so_log_lines.append(f"[DRY RUN] SO{so_num} Limit: {so_crypto_amount:.8f} @ Rp {so_price:,.0f}")
            else:
                result = self.client.buy(self.pair, so_price, so_crypto_amount)
                if 'error' in result:
                    logger.error(f"SO{so_num} limit order failed: {result['error']}")
                    continue
                so_order_id = str(result.get('order_id', 'N/A'))
                so_log_lines.append(f"✅ SO{so_num} Limit placed! Order ID: {so_order_id} @ Rp {so_price:,.0f}")

            self.state['open_orders'].append({
                'order_id': str(so_order_id),
                'type': f'so_{so_num}',
                'price': so_price,
                'amount_idr': amount,
                'amount_crypto': so_crypto_amount,
                'so_number': so_num,
            })

            so_distance_total = self.so_distance * (self.step_scale ** (so_num - 1))
            so_log_lines.append(f"\n  SO{so_num} @ Rp {so_price:,.0f} ({so_distance_total:.2f}% below) | Amount: Rp {amount:,.0f}")

        self.save_state()

        logger.info(f"📊 Base filled @ Rp {price:,.0f} (Amount: Rp {self.base_order:,.0f})")
        logger.info(f"🎯 TP Order placed @ Rp {self.state['tp_price']:,.0f}")
        logger.info(f"📉 Safety Orders:{''.join(so_log_lines)}")
        logger.info(f"📋 Total Open Orders: {len(self.state['open_orders']) + (1 if self.state.get('tp_order_id') else 0)}")
        logger.info("=" * 50)

    def place_tp_order(self):
        """Place limit sell order at TP price"""
        if self.state['total_crypto_bought'] <= 0 or self.state['tp_price'] <= 0:
            return

        tp_price = self.round_price(self.state['tp_price'], 'sell')
        amount = self.state['total_crypto_bought']

        if self.dry_run:
            self.state['tp_order_id'] = f"DRY_TP_{int(time.time())}"
            logger.info(f"[DRY RUN] Placing TP Limit Sell: {amount:.8f} @ Rp {tp_price:,.0f}")
        else:
            # Query actual available balance of the coin to avoid fee-related Insufficient Balance error
            coin = self.pair.lower().replace('_', '').replace('idr', '')
            balance_res = self.client.get_balance()
            if isinstance(balance_res, dict) and 'balance' in balance_res:
                avail_balance = float(balance_res['balance'].get(coin, 0))
                if avail_balance < amount:
                    logger.info(f"Theoretical TP amount ({amount:.8f}) is greater than available {coin.upper()} balance ({avail_balance:.8f}) due to exchange fees. Adjusting TP amount to {avail_balance:.8f}")
                    amount = avail_balance
            
            if amount <= 0:
                logger.error(f"Cannot place TP limit sell order: available {coin.upper()} balance is 0 or not found")
                return

            if amount * tp_price < 10000:
                logger.error(f"Cannot place TP limit sell order: transaction value (Rp {amount * tp_price:,.0f}) is below minimum limit of 10,000 IDR")
                return

            result = self.client.sell(self.pair, tp_price, amount)
            if 'error' in result:
                logger.error(f"Failed to place TP limit sell order: {result['error']}")
                self.state['tp_order_id'] = None
            else:
                self.state['tp_order_id'] = str(result.get('order_id', ''))
                logger.info(f"🎯 Placed TP Limit Sell! Order ID: {self.state['tp_order_id']} @ Rp {tp_price:,.0f} (Amount: {amount:.8f})")
        self.save_state()

    def cancel_tp_order(self):
        """Cancel the active TP limit sell order if exists"""
        tp_order_id = self.state.get('tp_order_id')
        if not tp_order_id:
            return

        if self.dry_run:
            logger.info(f"[DRY RUN] Cancelled TP order: {tp_order_id}")
        else:
            result = self.client.cancel_order(self.pair, tp_order_id, 'sell')
            if isinstance(result, dict) and 'error' in result:
                logger.warning(f"Failed to cancel TP order {tp_order_id}: {result['error']}")
            else:
                logger.info(f"🚫 Cancelled active TP order: {tp_order_id}")
        
        self.state['tp_order_id'] = None 
        self.save_state()
        logger.info("=" * 50)

    def check_and_update_orders(self):
        """Check if trading configuration has changed and update TP/SO orders accordingly"""
        if not self.state.get('active_position', False):
            return

        # 1. Check if TP percent setting changed and update TP price
        avg_entry = self.calculate_avg_entry()
        if avg_entry > 0:
            target_tp = self.round_price(avg_entry * (1 + self.tp_percent / 100), 'sell')
            # Compare current tp_price with target_tp
            if abs(self.state.get('tp_price', 0) - target_tp) >= 2:
                logger.info(f"🔄 TP Setting change detected! Recalculating TP: Rp {self.state.get('tp_price', 0):,.0f} -> Rp {target_tp:,.0f}")
                self.cancel_tp_order()
                self.state['tp_price'] = target_tp
                self.save_state()

        # 2. Check if SO settings changed (distance, amount, martingale, max_so)
        filled_count = len(self.state.get('so_entries', []))
        open_orders = self.state.get('open_orders', [])
        
        needs_rebuild = False
        
        # Check if any open order has outdated price/amount, or is beyond max_so
        for o in open_orders:
            so_num = o.get('so_number')
            if not so_num:
                continue
            
            # If so_num is beyond max_so, we need to cancel it
            if so_num > self.max_so:
                needs_rebuild = True
                break
                
            # Desired price and amount for this SO
            target_so_price = self.round_price(self.get_so_price(so_num), 'buy')
            target_so_amount = self.so_order
            if self.martingale:
                target_so_amount = self.so_order * (self.volume_scale ** (so_num - 1))
                
            # Compare price and amount (allow 2 IDR tolerance for rounding)
            price_diff = abs(o.get('price', 0) - target_so_price)
            amount_diff = abs(o.get('amount_idr', 0) - target_so_amount)
            
            if price_diff >= 2 or amount_diff >= 2:
                logger.info(f"🔄 SO{so_num} setting mismatch detected! "
                            f"Price diff: {price_diff}, Amount diff: {amount_diff}. Rebuilding SO orders.")
                needs_rebuild = True
                break

        # If we need to rebuild, cancel all open safety orders and let the normal loop re-place them
        if needs_rebuild:
            logger.info("🔄 Recreating safety orders to match new configuration...")
            self.cancel_open_orders()  # Cancel all open safety orders on exchange
            self.state['open_orders'] = []
            self.save_state()

    def place_missing_safety_orders(self):
        """Place any safety orders that should be active but are missing"""
        if not self.state['active_position']:
            return

        filled_count = len(self.state.get('so_entries', []))
        placed_so_nums = {o['so_number'] for o in self.state.get('open_orders', []) if 'so_number' in o}

        for so_num in range(filled_count + 1, self.max_so + 1):
            if so_num not in placed_so_nums:
                logger.info(f"Checking/Placing missing SO{so_num} limit order...")
                so_price_raw = self.get_so_price(so_num)
                so_price = self.round_price(so_price_raw, 'buy')

                # Calculate amount with optional martingale
                amount = self.so_order
                if self.martingale:
                    amount = self.so_order * (self.volume_scale ** (so_num - 1))

                so_crypto_amount = amount / so_price

                if self.dry_run:
                    so_order_id = f"DRY_SO_{so_num}_{int(time.time())}"
                    logger.info(f"[DRY RUN] Placed missing SO{so_num} Limit: {so_crypto_amount:.8f} @ Rp {so_price:,.0f}")
                else:
                    result = self.client.buy(self.pair, so_price, so_crypto_amount)
                    if 'error' in result:
                        logger.error(f"Failed to place missing SO{so_num} limit order: {result['error']}")
                        continue
                    so_order_id = str(result.get('order_id', 'N/A'))
                    logger.info(f"✅ Placed missing SO{so_num} Limit! Order ID: {so_order_id} @ Rp {so_price:,.0f}")

                self.state['open_orders'].append({
                    'order_id': str(so_order_id),
                    'type': f'so_{so_num}',
                    'price': so_price,
                    'amount_idr': amount,
                    'amount_crypto': so_crypto_amount,
                    'so_number': so_num,
                })
                self.save_state()

    def get_so_price(self, index):
        """Calculate SO price for given index (1-based) using cumulative distance"""
        level = max(index, 1)
        total_distance = sum(
            self.so_distance * (self.step_scale ** idx if self.step_scale > 1.0 else 1)
            for idx in range(level)
        )
        return self.state['base_price'] * (1 - total_distance / 100)

    def get_price_increment(self):
        """Get the price increment for the trading pair from Indodax"""
        increments = self.client.get_price_increments()
        if isinstance(increments, dict) and 'error' not in increments:
            pair_key = self.pair.lower().replace('idr', '_idr')
            # Indodax price_increments returns data under 'increments' key
            incr_dict: Any = increments.get('increments', {})
            if isinstance(incr_dict, dict):
                pair_info: Any = incr_dict.get(pair_key, {})
                if isinstance(pair_info, dict):
                    return float(pair_info.get('price_increment', 1))
        return 1  # Default to 1 IDR

    def round_price(self, price, side):
        """Round price according to Indodax price increment rules"""
        increment = self.get_price_increment()
        if increment <= 0:
            return float(int(price))
        if side == 'buy':
            # For buy limit orders, round down to nearest increment (bid)
            units = math.floor(price / increment)
        else:
            # For sell limit orders, round up to nearest increment (ask)
            units = math.ceil(price / increment)
        rounded = units * increment
        return float(int(rounded) if increment >= 1 else rounded)

    def sync_filled_orders(self):
        """Check which placed orders have been filled and update state"""
        if not self.state['active_position']:
            return

        if self.dry_run:
            current_price = self.get_current_price()
            if not current_price:
                return

            newly_filled = []
            remaining = []
            for order in self.state.get('open_orders', []):
                if order.get('type', '').startswith('so_'):
                    if current_price <= order['price']:
                        newly_filled.append(order)
                    else:
                        remaining.append(order)
                else:
                    remaining.append(order)

            if newly_filled:
                self.cancel_tp_order()
                for order in newly_filled:
                    logger.info(f"🟢 [DRY RUN] Order {order['order_id']} ({order['type']}) FILLED @ Rp {order['price']:,.0f}")
                    # Force dry_run flag to match simulation context
                    order['dry_run'] = True
                    self._process_filled_order(order)

                self.state['open_orders'] = remaining
                
                # Recalculate TP/SL based on avg entry and place new TP order
                if self.state['total_crypto_bought'] > 0:
                    new_avg = self.calculate_avg_entry()
                    self.state['tp_price'] = new_avg * (1 + self.tp_percent / 100)
                    if self.sl_percent > 0:
                        self.state['sl_price'] = new_avg * (1 - self.sl_percent / 100)
                    logger.info(f"📊 [DRY RUN] New Avg Entry: Rp {new_avg:,.0f} | TP: Rp {self.state['tp_price']:,.0f}")
                    self.place_tp_order()
                    
                self.save_state()
            return

        open_orders = self.client.get_open_orders(self.pair)
        if isinstance(open_orders, dict) and 'error' in open_orders:
            logger.warning(f"Failed to check open orders: {open_orders['error']}")
            return

        # Get list of still-open order IDs from Indodax
        still_open_ids = set()
        if isinstance(open_orders, list):
            for o in open_orders:
                still_open_ids.add(str(o.get('order_id', '')))

        # 1. Check if the TP order is filled
        tp_order_id = self.state.get('tp_order_id')
        
        # Self-healing: if tp_order_id is missing locally, scan open orders for an existing sell limit order
        if not tp_order_id and isinstance(open_orders, list):
            sell_orders = [o for o in open_orders if o.get('type') == 'sell']
            if sell_orders:
                tp_order_id = str(sell_orders[0]['order_id'])
                self.state['tp_order_id'] = tp_order_id
                logger.info(f"🔄 Recovered TP order ID from exchange open orders: {tp_order_id}")
                self.save_state()
                # Add to still_open_ids to avoid triggering immediate fill check
                still_open_ids.add(tp_order_id)

        if tp_order_id and str(tp_order_id) not in still_open_ids:
            logger.info(f"Checking status of TP order {tp_order_id}...")
            status_check = self.client.get_order_status(self.pair, tp_order_id)
            if isinstance(status_check, dict) and 'error' in status_check:
                logger.warning(f"TP order status check returned error: {status_check['error']}. Resetting TP order ID to re-place.")
                self.state['tp_order_id'] = None
                self.save_state()
            else:
                status = str(status_check.get('status', '')).lower()
                if status in ('filled', 'done', 'closed'):
                    logger.info(f"🎯 TP Limit Order {tp_order_id} FILLED!")
                    current_price = self.get_current_price() or self.state['tp_price']
                    self.execute_take_profit(current_price, limit_sell_already_executed=True)
                    return
                elif status in ['cancelled', 'canceled']:
                    logger.warning(f"TP order {tp_order_id} was CANCELLED on exchange. Resetting TP order ID to re-place.")
                    self.state['tp_order_id'] = None
                    self.save_state()
                else:
                    logger.info(f"TP order {tp_order_id} status is '{status}'. Keeping it.")

        # 2. Check safety/base orders
        newly_filled = []
        remaining = []
        for order in self.state['open_orders']:
            oid = str(order['order_id'])
            if oid not in still_open_ids:
                logger.info(f"Checking status of SO order {oid}...")
                status_check = self.client.get_order_status(self.pair, oid)
                if isinstance(status_check, dict) and 'error' in status_check:
                    logger.warning(f"SO order status check returned error: {status_check['error']}. Assuming cancelled/removed.")
                else:
                    status = status_check.get('status', '')
                    if status == 'filled':
                        newly_filled.append(order)
                    elif status in ['cancelled', 'canceled']:
                        logger.warning(f"SO order {oid} was CANCELLED on exchange. Removing to re-place.")
                    else:
                        remaining.append(order)
            else:
                remaining.append(order)

        if newly_filled:
            self.cancel_tp_order()
            for order in newly_filled:
                logger.info(f"🟢 Order {order['order_id']} ({order['type']}) FILLED @ Rp {order['price']:,.0f}")
                self._process_filled_order(order)

            self.state['open_orders'] = remaining
            
            # Recalculate TP/SL based on avg entry and place new TP order
            if self.state['total_crypto_bought'] > 0:
                new_avg = self.calculate_avg_entry()
                self.state['tp_price'] = new_avg * (1 + self.tp_percent / 100)
                if self.sl_percent > 0:
                    self.state['sl_price'] = new_avg * (1 - self.sl_percent / 100)
                logger.info(f"📊 New Avg Entry: Rp {new_avg:,.0f} | TP: Rp {self.state['tp_price']:,.0f}")
                self.place_tp_order()
                
            self.save_state()

    def _process_filled_order(self, order):
        """Process a filled limit order - update invested, crypto, TP levels"""
        so_number = order.get('so_number', 0)
        price = order['price']
        amount_idr = order['amount_idr']
        amount_crypto = order['amount_crypto']

        self.state['total_invested'] += amount_idr
        self.state['total_crypto_bought'] += amount_crypto

        if so_number == 0:
            # Base order filled
            self.state['base_amount_crypto'] += amount_crypto
        else:
            # Safety order filled
            so_entry = {
                'number': so_number,
                'price': price,
                'target_price': price,
                'amount_idr': amount_idr,
                'amount_crypto': amount_crypto,
                'order_id': order['order_id'],
                'dry_run': False,
                'timestamp': datetime.now().isoformat(),
            }
            self.state['so_entries'].append(so_entry)

        # Record in trades
        self.state['trades'].append({
            'timestamp': datetime.now().isoformat(),
            'type': f'so_{so_number}' if so_number > 0 else 'base',
            'price': price,
            'amount_idr': amount_idr,
            'amount_crypto': amount_crypto,
            'order_id': order['order_id'],
            'dry_run': False,
        })

    def cancel_open_orders(self):
        """Cancel all remaining unfilled limit orders"""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would cancel {len(self.state['open_orders'])} open orders")
            self.state['open_orders'] = []
            self.save_state()
            return

        cancelled_count = 0
        for order in list(self.state['open_orders']):
            oid = str(order['order_id'])
            result = self.client.cancel_order(self.pair, oid, 'buy')
            if isinstance(result, dict) and 'error' in result:
                logger.warning(f"Failed to cancel order {oid}: {result['error']}")
            else:
                logger.info(f"🚫 Cancelled order {oid} ({order['type']}) @ Rp {order['price']:,.0f}")
                cancelled_count += 1
            time.sleep(0.2)  # Delay to ensure strictly increasing nonce for the next request

        self.state['open_orders'] = []
        self.save_state()
        logger.info(f"📋 Cancelled {cancelled_count} open orders")

    def cancel_all_exchange_orders(self):
        """Cancel every single open order on the exchange for this pair (including safety orders and TP)"""
        if self.dry_run:
            self.state['open_orders'] = []
            self.state['tp_order_id'] = None
            self.save_state()
            return

        # Fetch open orders from exchange
        open_orders = self.client.get_open_orders(self.pair)
        if isinstance(open_orders, dict) and 'error' in open_orders:
            logger.warning(f"Failed to check open orders for cancellation: {open_orders['error']}")
            return

        cancelled_count = 0
        if isinstance(open_orders, list):
            for o in open_orders:
                oid = str(o.get('order_id', ''))
                otype = o.get('type', '')
                if oid:
                    result = self.client.cancel_order(self.pair, oid, otype)
                    if isinstance(result, dict) and 'error' in result:
                        logger.warning(f"Failed to cancel order {oid} ({otype}) on exchange: {result['error']}")
                    else:
                        logger.info(f"🚫 Cancelled exchange open order {oid}")
                        cancelled_count += 1
                    time.sleep(0.2)  # Delay to ensure strictly increasing nonce for the next request

        self.state['open_orders'] = []
        self.state['tp_order_id'] = None
        self.save_state()
        logger.info(f"📋 Cancelled {cancelled_count} open orders on the exchange")

    def check_and_execute(self):
        """Main check loop - sync filled orders, check TP, SL"""
        # Reload config from config.py to dynamically apply dashboard setting changes
        try:
            importlib.reload(config)
            self.pair = config.TRADING_PAIR
            self.base_order = config.BASE_ORDER_IDR
            self.so_order = config.SAFETY_ORDER_IDR
            self.max_so = config.MAX_SAFETY_ORDERS
            self.so_distance = config.SAFETY_ORDER_DISTANCE
            self.tp_percent = config.TAKE_PROFIT_PERCENT
            self.sl_percent = config.STOP_LOSS_PERCENT
            self.martingale = config.MARTINGALE_ENABLED
            self.volume_scale = config.VOLUME_SCALE
            self.step_scale = config.STEP_SCALE
            self.dry_run = config.DRY_RUN
            self.rsi_period = config.RSI_PERIOD
            self.rsi_oversold = config.RSI_OVERSOLD
            
            # Update client API credentials if changed
            self.client.api_key = config.INDODAX_API_KEY
            self.client.secret_key = config.INDODAX_SECRET_KEY
        except Exception as e:
            logger.warning(f"Failed to reload config.py: {e}")

        # Reload state from JSON to capture updates from dashboard
        try:
            new_state = self.load_state()
            # Merge key values but keep trades list (managed locally)
            for k, v in new_state.items():
                if k != 'trades':
                    self.state[k] = v
            self.ensure_state()
        except Exception as e:
            logger.warning(f"Failed to reload state: {e}")

        # Check if dashboard stopped the bot
        if not self.state.get('bot_running', True):
            if len(self.state.get('open_orders', [])) > 0 or self.state.get('tp_order_id'):
                logger.info("🛑 Bot stopped via dashboard! Cancelling all open orders on the exchange...")
                self.cancel_all_exchange_orders()
                self.state['tp_order_id'] = None
                self.state['open_orders'] = []
                self.save_state()
            return

        if not self.state['active_position']:
            if not self.state.get('pending_new_entry', True):
                logger.info("🚀 Dashboard triggered start! Opening position immediately...")
                self.start_bot()
            elif self.state.get('pending_new_entry'):
                self.check_reentry()
            return

        # Sync filled orders from exchange
        self.sync_filled_orders()

        # Check and update orders if settings changed dynamically
        self.check_and_update_orders()

        # Place/retry missing safety orders and TP orders if any failed initially
        if self.state.get('active_position'):
            if not self.state.get('tp_order_id'):
                logger.info("TP order is missing on exchange. Attempting to place it...")
                self.place_tp_order()
            self.place_missing_safety_orders()

        # If all open orders are filled, we have full position
        if len(self.state['open_orders']) == 0:
            pass

        current_price = self.get_current_price()
        if not current_price:
            return

        filled_count = len(self.state['so_entries'])
        remaining_count = len(self.state['open_orders'])
        logger.info(f"📈 Price: Rp {current_price:,.0f} | "
                    f"Filled: {filled_count}/{self.max_so} SOs | "
                    f"Open Orders: {remaining_count} | "
                    f"TP: Rp {self.state['tp_price']:,.0f}")

        # Check Take Profit (executes on price touch in both live and dry run modes)
        if self.state['tp_price'] > 0 and self.state['base_amount_crypto'] > 0:
            if current_price >= self.state['tp_price']:
                logger.info(f"🎯 Price touched/exceeded TP! (Current: Rp {current_price:,.0f} >= TP: Rp {self.state['tp_price']:,.0f})")
                self.execute_take_profit(current_price, limit_sell_already_executed=False)
                return

        # Check Stop Loss
        if self.sl_percent > 0 and self.state['sl_price'] > 0:
            if current_price <= self.state['sl_price']:
                self.execute_stop_loss(current_price)
                return

    def execute_take_profit(self, current_price, limit_sell_already_executed=False):
        """Execute take profit - sell all crypto"""
        logger.info("=" * 50)
        logger.info(f"🎯 TAKE PROFIT HIT @ Rp {current_price:,.0f}")

        # Cancel all open orders on exchange (such as safety orders and TP limit order)
        self.cancel_all_exchange_orders()

        avg_entry = self.calculate_avg_entry()
        profit_pct = ((current_price - avg_entry) / avg_entry) * 100 if avg_entry > 0 else 0
        total_idr_value = self.state['total_crypto_bought'] * current_price
        profit_idr = total_idr_value - self.state['total_invested']

        logger.info(f"📊 Avg Entry: Rp {avg_entry:,.0f}")
        logger.info(f"📈 Profit: {profit_pct:+.2f}% (Rp {profit_idr:,.0f})")

        if not self.dry_run and not limit_sell_already_executed:
            if self.state['total_crypto_bought'] > 0:
                result = self.client.sell_market(self.pair, self.state['total_crypto_bought'])
                if 'error' in result:
                    logger.error(f"Sell failed: {result['error']}")
                    return
                logger.info(f"✅ Sold! Order ID: {result.get('order_id', 'N/A')}")
        elif limit_sell_already_executed:
            logger.info("✅ Sold via Limit Order on Exchange!")

        # Record TP trade
        self.state['trades'].append({
            'timestamp': datetime.now().isoformat(),
            'type': 'take_profit',
            'price': current_price,
            'amount_idr': total_idr_value,
            'amount_crypto': self.state['total_crypto_bought'],
            'profit_pct': round(profit_pct, 4),
            'profit_idr': round(profit_idr),
            'dry_run': self.dry_run,
        })

        # Reset position
        self.state['active_position'] = False
        self.state['base_price'] = 0
        self.state['base_amount_crypto'] = 0
        self.state['so_entries'] = []
        self.state['tp_price'] = 0
        self.state['sl_price'] = 0
        self.state['total_invested'] = 0
        self.state['total_crypto_bought'] = 0
        self.state['pending_new_entry'] = True
        self.state['open_orders'] = []
        self.state['tp_order_id'] = None

        self.save_state()
        logger.info("⏳ Waiting for RSI oversold to re-enter...")
        logger.info("=" * 50)

    def execute_stop_loss(self, current_price):
        """Execute stop loss"""
        logger.info("=" * 50)
        logger.info(f"🛑 STOP LOSS HIT @ Rp {current_price:,.0f}")

        # Cancel all open orders on exchange
        self.cancel_all_exchange_orders()

        total_idr_value = self.state['total_crypto_bought'] * current_price
        loss_idr = self.state['total_invested'] - total_idr_value

        logger.info(f"📉 Loss: Rp {loss_idr:,.0f}")

        if not self.dry_run:
            if self.state['total_crypto_bought'] > 0:
                result = self.client.sell_market(self.pair, self.state['total_crypto_bought'])
                if 'error' in result:
                    logger.error(f"Sell failed: {result['error']}")
                    return
                logger.info(f"✅ Sold! Order ID: {result.get('order_id', 'N/A')}")

        self.state['trades'].append({
            'timestamp': datetime.now().isoformat(),
            'type': 'stop_loss',
            'price': current_price,
            'amount_idr': total_idr_value,
            'amount_crypto': self.state['total_crypto_bought'],
            'loss_idr': round(loss_idr),
            'dry_run': self.dry_run,
        })

        # Reset and wait for re-entry
        self.state['active_position'] = False
        self.state['base_price'] = 0
        self.state['base_amount_crypto'] = 0
        self.state['so_entries'] = []
        self.state['tp_price'] = 0
        self.state['sl_price'] = 0
        self.state['total_invested'] = 0
        self.state['total_crypto_bought'] = 0
        self.state['pending_new_entry'] = True
        self.state['open_orders'] = []
        self.state['tp_order_id'] = None

        self.save_state()
        logger.info("⏳ Waiting for RSI oversold to re-enter...")
        logger.info("=" * 50)

    def check_reentry(self):
        """Check RSI for re-entry signal"""
        if not self.state['pending_new_entry']:
            return

        rsi = self.calculate_rsi()
        if rsi is None:
            return

        current_price = self.get_current_price()
        if not current_price:
            return

        logger.info(f"🔍 RSI: {rsi} | Price: Rp {current_price:,.0f} "
                    f"(Re-entry when RSI < {self.rsi_oversold})")

        if rsi <= self.rsi_oversold:
            logger.info(f"🟢 RSI OVERSOLD ({rsi}) - Re-entering position!")
            self.start_bot()

    def show_status(self):
        """Display current bot status"""
        logger.info("=" * 50)
        logger.info("📊 BOT STATUS")
        logger.info("=" * 50)

        current_price = self.get_current_price()
        if current_price:
            logger.info(f"📈 Current Price: Rp {current_price:,.0f}")

        rsi = self.calculate_rsi()
        if rsi:
            logger.info(f"📊 RSI(14): {rsi}")

        if self.state['active_position']:
            avg_entry = self.calculate_avg_entry()
            logger.info(f"🟢 Position: ACTIVE")
            logger.info(f"📊 Avg Entry: Rp {avg_entry:,.0f}")
            logger.info(f"🎯 TP: Rp {self.state['tp_price']:,.0f} ({self.tp_percent}%)")
            logger.info(f"💰 Invested: Rp {self.state['total_invested']:,.0f}")
            logger.info(f"🪙 Crypto: {self.state['total_crypto_bought']:.8f}")
            logger.info(f"📉 SOs Executed: {len(self.state['so_entries'])}/{self.max_so}")

            if current_price:
                pnl = ((current_price - avg_entry) / avg_entry) * 100
                pnl_idr = (self.state['total_crypto_bought'] * current_price) - self.state['total_invested']
                logger.info(f"📈 P/L: {pnl:+.2f}% (Rp {pnl_idr:,.0f})")
        else:
            logger.info(f"⚪ Position: INACTIVE")
            if self.state['pending_new_entry']:
                logger.info(f"⏳ Waiting for RSI < {self.rsi_oversold} to re-enter")

        total_trades = len(self.state.get('trades', []))
        logger.info(f"📋 Total Trades: {total_trades}")
        logger.info("=" * 50)

    def run(self):
        """Main bot loop"""
        logger.info("Bot running... Press Ctrl+C to stop")

        try:
            while True:
                self.check_and_execute()
                time.sleep(30)  # Check every 30 seconds
        except KeyboardInterrupt:
            logger.info("\nBot stopped by user. Cleaning up exchange orders...")
            try:
                self.cancel_all_exchange_orders()
            except Exception as e:
                logger.error(f"Failed to clean up exchange orders: {e}")
        except Exception as e:
            logger.error(f"Bot error: {e}")


def main():
    bot = DCABot()
    bot.show_status()
    bot.run()


if __name__ == "__main__":
    main()