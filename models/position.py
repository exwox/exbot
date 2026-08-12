"""
Position Model
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Position:
    id: str = ""
    bot_id: str = ""
    status: str = "OPEN"
    base_price: float = 0.0
    average_entry_price: float = 0.0
    base_amount: float = 0.0
    total_amount: float = 0.0
    sold_amount: float = 0.0
    total_invested: float = 0.0
    reserved_capital: float = 0.0
    take_profit_price: float = 0.0
    stop_loss_price: float = 0.0
    current_price: float = 0.0
    so_entries: list = None  # list of SO entry dicts
    tp_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    exit_reason: str = ""
    open_orders: list = None  # list of open order dicts
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.id:
            import uuid
            self.id = f"pos_{uuid.uuid4().hex[:12]}"
        if self.so_entries is None:
            self.so_entries = []
        if self.open_orders is None:
            self.open_orders = []

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'bot_id': self.bot_id,
            'status': self.status,
            'base_price': self.base_price,
            'average_entry_price': self.average_entry_price,
            'base_amount': self.base_amount,
            'total_amount': self.total_amount,
            'sold_amount': self.sold_amount,
            'total_invested': self.total_invested,
            'reserved_capital': self.reserved_capital,
            'take_profit_price': self.take_profit_price,
            'stop_loss_price': self.stop_loss_price,
            'current_price': self.current_price,
            'so_entries': self.so_entries,
            'tp_order_id': self.tp_order_id,
            'exit_order_id': self.exit_order_id,
            'exit_reason': self.exit_reason,
            'open_orders': self.open_orders,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict) -> 'Position':
        return Position(
            id=data.get('id', ''),
            bot_id=data.get('bot_id', ''),
            status=data.get('status', 'OPEN'),
            base_price=float(data.get('base_price', 0)),
            average_entry_price=float(data.get('average_entry_price', 0)),
            base_amount=float(data.get('base_amount', 0)),
            total_amount=float(data.get('total_amount', 0)),
            sold_amount=float(data.get('sold_amount', 0)),
            total_invested=float(data.get('total_invested', 0)),
            reserved_capital=float(data.get('reserved_capital', 0)),
            take_profit_price=float(data.get('take_profit_price', 0)),
            stop_loss_price=float(data.get('stop_loss_price', 0)),
            current_price=float(data.get('current_price', 0)),
            so_entries=data.get('so_entries', []),
            tp_order_id=data.get('tp_order_id'),
            exit_order_id=data.get('exit_order_id'),
            exit_reason=data.get('exit_reason', ''),
            open_orders=data.get('open_orders', []),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
        )
