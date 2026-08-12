"""
Strategy Engine - Pemisahan logic strategi dari API exchange
Hanya menentukan: BUY BASE, BUY SO, TAKE PROFIT, STOP LOSS, WAIT
"""
import math
from typing import Optional
from config.constants import OrderSide


class DCADecision:
    """Hasil keputusan strategi"""
    def __init__(self, action: str, **kwargs):
        self.action = action  # 'BASE', 'SO', 'TP', 'SL', 'WAIT', 'RE_ENTER'
        self.data = kwargs

    def __repr__(self):
        return f"DCADecision({self.action}, {self.data})"


class StrategyEngine:
    """
    Strategy Engine untuk DCA
    - Tidak memanggil API exchange
    - Hanya menghitung keputusan trading berdasarkan state dan market data
    """

    def __init__(self, config: dict):
        """
        config: dictionary berisi parameter strategi
        """
        self.base_order_amount = float(config.get('base_order_amount', 15000))
        self.safety_order_amount = float(config.get('safety_order_amount', 15000))
        self.max_safety_orders = int(config.get('max_safety_orders', 5))
        # Last-line protection in case legacy data or a direct DB update
        # bypasses the dashboard/API validation.
        self.price_deviation = max(float(config.get('price_deviation', 1.2)), 0.01)
        self.deviation_scale = float(config.get('deviation_scale', 1.5))
        self.step_scale_enabled = bool(config.get('step_scale_enabled', False))
        self.volume_scale = float(config.get('volume_scale', 1.5))
        self.take_profit_percent = float(config.get('take_profit_percent', 1.0))
        self.stop_loss_percent = float(config.get('stop_loss_percent', 0.0))
        self.limit_buy_fee_percent = float(config.get('limit_buy_fee_percent', 0.15))
        self.limit_sell_fee_percent = float(config.get('limit_sell_fee_percent', 0.15))
        self.market_buy_fee_percent = float(config.get('market_buy_fee_percent', 0.30))
        self.market_sell_fee_percent = float(config.get('market_sell_fee_percent', 0.30))
        self.martingale_enabled = bool(config.get('martingale_enabled', False))
        self.rsi_period = int(config.get('rsi_period', 14))
        self.rsi_oversold = int(config.get('rsi_oversold', 60))
        self.initial_entry_mode = str(config.get('initial_entry_mode', 'MARKET')).upper()
        self.max_position_amount = float(config.get('max_position_amount', 0))
        self._validate()

    def _validate(self):
        """Reject unsafe strategy values even when the API layer is bypassed."""
        if self.base_order_amount < 10000 or self.safety_order_amount < 10000:
            raise ValueError("Base order dan safety order minimal Rp 10.000")
        if not 0 <= self.max_safety_orders <= 20:
            raise ValueError("max_safety_orders harus antara 0 dan 20")
        if not 0.01 <= self.price_deviation <= 50:
            raise ValueError("price_deviation harus antara 0.01 dan 50")
        if not 0.1 <= self.deviation_scale <= 10 or not 0.1 <= self.volume_scale <= 10:
            raise ValueError("deviation_scale dan volume_scale harus antara 0.1 dan 10")
        if not 0.01 <= self.take_profit_percent <= 100:
            raise ValueError("take_profit_percent harus antara 0.01 dan 100")
        if not 0 <= self.stop_loss_percent <= 100:
            raise ValueError("stop_loss_percent harus antara 0 dan 100")
        if self.initial_entry_mode not in ('MARKET', 'LIMIT', 'RSI', 'RSI_LIMIT'):
            raise ValueError("initial_entry_mode tidak valid")
        for fee in (self.limit_buy_fee_percent, self.limit_sell_fee_percent,
                    self.market_buy_fee_percent, self.market_sell_fee_percent):
            if not 0 <= fee <= 10:
                raise ValueError("Fee percent harus antara 0 dan 10")

    def planned_capital(self) -> float:
        """Maximum quote currency reserved by one complete DCA cycle."""
        return self.base_order_amount + sum(
            self.get_so_amount(level)
            for level in range(1, self.max_safety_orders + 1)
        )

    def get_so_price(self, base_price: float, so_number: int) -> float:
        """Calculate a cumulative DCA safety-order price (1-based).

        ``price_deviation`` is the distance between consecutive SO levels.
        Therefore, with Step Scale off, SO1..SO5 are at 1x..5x the base
        distance.  With it on, each next interval is multiplied by the step
        scale.  This prevents all SO levels from being placed at one price
        when Step Scale is disabled.
        """
        level = max(so_number, 1)
        total_distance = sum(
            self.price_deviation * (self.deviation_scale ** index
                                    if self.step_scale_enabled else 1)
            for index in range(level)
        )
        return base_price * (1 - total_distance / 100)

    def get_so_amount(self, so_number: int) -> float:
        """Calculate SO amount with optional martingale"""
        amount = self.safety_order_amount
        if self.martingale_enabled:
            amount = self.safety_order_amount * (self.volume_scale ** (so_number - 1))
        return amount

    def get_tp_price(self, average_entry: float) -> float:
        """TP price that reaches target profit after the limit-sell fee."""
        net_sell_factor = max(1 - self.limit_sell_fee_percent / 100, 0.000001)
        return average_entry * (1 + self.take_profit_percent / 100) / net_sell_factor

    def get_sl_price(self, average_entry: float) -> float:
        """Calculate stop loss price"""
        return average_entry * (1 - self.stop_loss_percent / 100)

    def calculate_average_entry(self, base_price: float, base_amount: float,
                                 so_entries: list) -> float:
        """Calculate average entry price from base + SO entries"""
        buy_factor = max(1 - self.market_buy_fee_percent / 100, 0.000001)
        total_invested = (base_price * base_amount) / buy_factor
        total_crypto = base_amount

        for so in so_entries:
            total_invested += float(so.get('amount_idr', 0))
            total_crypto += float(so.get('amount_crypto', 0))

        if total_crypto > 0:
            return total_invested / total_crypto
        return 0.0

    def calculate_rsi(self, closes: list[float]) -> Optional[float]:
        """Calculate RSI from closing prices"""
        if not closes or len(closes) < self.rsi_period + 1:
            return None

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

        avg_gain = sum(gains[:self.rsi_period]) / self.rsi_period
        avg_loss = sum(losses[:self.rsi_period]) / self.rsi_period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)

    def evaluate(self, state: dict, current_price: float, rsi: Optional[float] = None) -> DCADecision:
        """
        Evaluate current state and return trading decision.
        
        state = {
            'active_position': bool,
            'base_price': float,
            'base_amount_crypto': float,
            'so_entries': list,
            'total_invested': float,
            'total_crypto_bought': float,
            'tp_price': float,
            'sl_price': float,
            'pending_new_entry': bool,
            'open_orders': list,
        }
        """
        # If no active position, check re-entry
        if not state.get('active_position', False):
            if state.get('pending_new_entry', True):
                if rsi is not None and rsi <= self.rsi_oversold:
                    return DCADecision('RE_ENTER', reason=f"RSI {rsi} <= {self.rsi_oversold}")
                return DCADecision('WAIT', reason=f"RSI {rsi} > {self.rsi_oversold}" if rsi else "Waiting for RSI data")
            # Triggered by dashboard
            return DCADecision('START_BOT', reason="Dashboard trigger")

        # Active position - check TP
        avg_entry = self.calculate_average_entry(
            state.get('base_price', 0),
            state.get('base_amount_crypto', 0),
            state.get('so_entries', [])
        )

        if avg_entry > 0:
            tp = self.get_tp_price(avg_entry)
            if current_price >= tp:
                return DCADecision('TP', tp_price=tp, avg_entry=avg_entry, reason=f"Price {current_price} >= TP {tp}")

            if self.stop_loss_percent > 0:
                sl = self.get_sl_price(avg_entry)
                if current_price <= sl:
                    return DCADecision('SL', sl_price=sl, avg_entry=avg_entry, reason=f"Price {current_price} <= SL {sl}")

        # Return current state info
        filled_so_count = len(state.get('so_entries', []))
        return DCADecision('ACTIVE', 
                          filled_so=filled_so_count,
                          max_so=self.max_safety_orders,
                          current_price=current_price,
                          avg_entry=avg_entry)
