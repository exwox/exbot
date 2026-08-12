"""
Strategy Model
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Strategy:
    id: str = ""
    name: str = "Default"
    base_order_amount: float = 15000.0
    safety_order_amount: float = 15000.0
    max_safety_orders: int = 5
    price_deviation: float = 1.2
    deviation_scale: float = 1.5
    volume_scale: float = 1.5
    take_profit_percent: float = 1.0
    stop_loss_percent: float = 0.0
    max_position_amount: float = 0.0
    cooldown_seconds: int = 0
    martingale_enabled: bool = False
    rsi_period: int = 14
    rsi_oversold: int = 60
    rsi_overbought: int = 70
    step_scale_enabled: bool = False
    limit_buy_fee_percent: float = 0.15
    limit_sell_fee_percent: float = 0.15
    market_buy_fee_percent: float = 0.30
    market_sell_fee_percent: float = 0.30
    initial_entry_mode: str = "MARKET"
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.id:
            import uuid
            self.id = f"str_{uuid.uuid4().hex[:12]}"

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'base_order_amount': self.base_order_amount,
            'safety_order_amount': self.safety_order_amount,
            'max_safety_orders': self.max_safety_orders,
            'price_deviation': self.price_deviation,
            'deviation_scale': self.deviation_scale,
            'volume_scale': self.volume_scale,
            'take_profit_percent': self.take_profit_percent,
            'stop_loss_percent': self.stop_loss_percent,
            'max_position_amount': self.max_position_amount,
            'cooldown_seconds': self.cooldown_seconds,
            'martingale_enabled': self.martingale_enabled,
            'rsi_period': self.rsi_period,
            'rsi_oversold': self.rsi_oversold,
            'rsi_overbought': self.rsi_overbought,
            'step_scale_enabled': self.step_scale_enabled,
            'limit_buy_fee_percent': self.limit_buy_fee_percent,
            'limit_sell_fee_percent': self.limit_sell_fee_percent,
            'market_buy_fee_percent': self.market_buy_fee_percent,
            'market_sell_fee_percent': self.market_sell_fee_percent,
            'initial_entry_mode': self.initial_entry_mode,
            'enabled': self.enabled,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict) -> 'Strategy':
        return Strategy(
            id=data.get('id', ''),
            name=data.get('name', 'Default'),
            base_order_amount=float(data.get('base_order_amount', 15000)),
            safety_order_amount=float(data.get('safety_order_amount', 15000)),
            max_safety_orders=int(data.get('max_safety_orders', 5)),
            price_deviation=float(data.get('price_deviation', 1.2)),
            deviation_scale=float(data.get('deviation_scale', 1.5)),
            volume_scale=float(data.get('volume_scale', 1.5)),
            take_profit_percent=float(data.get('take_profit_percent', 1.0)),
            stop_loss_percent=float(data.get('stop_loss_percent', 0.0)),
            max_position_amount=float(data.get('max_position_amount', 0.0)),
            cooldown_seconds=int(data.get('cooldown_seconds', 0)),
            martingale_enabled=bool(data.get('martingale_enabled', False)),
            rsi_period=int(data.get('rsi_period', 14)),
            rsi_oversold=int(data.get('rsi_oversold', 60)),
            rsi_overbought=int(data.get('rsi_overbought', 70)),
            step_scale_enabled=bool(data.get('step_scale_enabled', False)),
            limit_buy_fee_percent=float(data.get('limit_buy_fee_percent', 0.15)),
            limit_sell_fee_percent=float(data.get('limit_sell_fee_percent', 0.15)),
            market_buy_fee_percent=float(data.get('market_buy_fee_percent', 0.30)),
            market_sell_fee_percent=float(data.get('market_sell_fee_percent', 0.30)),
            initial_entry_mode=str(data.get('initial_entry_mode', 'MARKET')).upper(),
            enabled=bool(data.get('enabled', True)),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
        )