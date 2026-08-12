"""
Bot Model
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Bot:
    id: str = ""
    account_id: str = ""
    name: str = ""
    exchange: str = "Indodax"
    pair: str = "btcidr"
    status: str = "STOPPED"
    dry_run: bool = True
    strategy_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.id:
            import uuid
            self.id = f"bot_{uuid.uuid4().hex[:12]}"

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'account_id': self.account_id,
            'name': self.name,
            'exchange': self.exchange,
            'pair': self.pair,
            'status': self.status,
            'dry_run': self.dry_run,
            'strategy_id': self.strategy_id,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict) -> 'Bot':
        return Bot(
            id=data.get('id', ''),
            account_id=data.get('account_id', ''),
            name=data.get('name', ''),
            exchange=data.get('exchange', 'Indodax'),
            pair=data.get('pair', 'btcidr'),
            status=data.get('status', 'STOPPED'),
            dry_run=data.get('dry_run', True),
            strategy_id=data.get('strategy_id'),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
        )