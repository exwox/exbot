"""
Account Model
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Account:
    id: str = ""
    user_id: str = ""
    name: str = ""
    exchange: str = "Indodax"
    api_version: str = "v1"
    api_key_encrypted: str = ""
    api_secret_encrypted: str = ""
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""
    last_connected_at: Optional[str] = None
    last_error: Optional[str] = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.id:
            import uuid
            self.id = f"acc_{uuid.uuid4().hex[:12]}"

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'exchange': self.exchange,
            'api_version': self.api_version,
            'api_key_encrypted': self.api_key_encrypted,
            'api_secret_encrypted': self.api_secret_encrypted,
            'is_active': self.is_active,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'last_connected_at': self.last_connected_at,
            'last_error': self.last_error,
        }

    @staticmethod
    def from_dict(data: dict) -> 'Account':
        return Account(
            id=data.get('id', ''),
            user_id=str(data.get('user_id', '') or ''),
            name=data.get('name', ''),
            exchange=data.get('exchange', 'Indodax'),
            api_version=data.get('api_version', 'v1') or 'v1',
            api_key_encrypted=data.get('api_key_encrypted', ''),
            api_secret_encrypted=data.get('api_secret_encrypted', ''),
            is_active=data.get('is_active', True),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            last_connected_at=data.get('last_connected_at'),
            last_error=data.get('last_error'),
        )
