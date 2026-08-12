"""
Order Model
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Order:
    id: str = ""
    bot_id: str = ""
    account_id: str = ""
    exchange_order_id: str = ""
    order_type: str = "buy"  # buy/sell
    side: str = ""  # base/so/tp/sl
    pair: str = "btcidr"
    price: float = 0.0
    amount: float = 0.0  # crypto amount
    amount_quote: float = 0.0  # IDR amount
    status: str = "OPEN"
    is_dca: bool = True
    dca_level: int = 0
    so_number: int = 0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.id:
            import uuid
            self.id = f"ord_{uuid.uuid4().hex[:12]}"
        if not self.side:
            self.side = "so" if self.dca_level > 0 else "base"

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'bot_id': self.bot_id,
            'account_id': self.account_id,
            'exchange_order_id': self.exchange_order_id,
            'order_type': self.order_type,
            'side': self.side,
            'pair': self.pair,
            'price': self.price,
            'amount': self.amount,
            'amount_quote': self.amount_quote,
            'status': self.status,
            'is_dca': self.is_dca,
            'dca_level': self.dca_level,
            'so_number': self.so_number,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict) -> 'Order':
        return Order(
            id=data.get('id', ''),
            bot_id=data.get('bot_id', ''),
            account_id=data.get('account_id', ''),
            exchange_order_id=data.get('exchange_order_id', ''),
            order_type=data.get('order_type', 'buy'),
            side=data.get('side', ''),
            pair=data.get('pair', 'btcidr'),
            price=float(data.get('price', 0)),
            amount=float(data.get('amount', 0)),
            amount_quote=float(data.get('amount_quote', 0)),
            status=data.get('status', 'OPEN'),
            is_dca=bool(data.get('is_dca', True)),
            dca_level=int(data.get('dca_level', 0)),
            so_number=int(data.get('so_number', 0)),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
        )