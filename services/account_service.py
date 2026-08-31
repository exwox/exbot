"""
Account Service
Mengelola siklus hidup akun - create, update, delete, test connection
"""
from datetime import datetime
from typing import Optional

from database.database import DatabaseManager
from models.account import Account
from services.encryption_service import EncryptionService
from exchanges.indodax_client import IndodaxClient


class AccountService:
    def __init__(self, db: DatabaseManager, encryption: EncryptionService):
        self.db = db
        self.encryption = encryption

    def create_account(self, name: str, api_key: str, api_secret: str,
                       exchange: str = "Indodax",
                       api_version: str = "v1") -> Account:
        """Create a new account with encrypted credentials"""
        account = Account(name=name, exchange=exchange,
                          api_version=self._normalize_api_version(api_version),
                          is_active=True)
        account.api_key_encrypted = self.encryption.encrypt(api_key, account.id)
        account.api_secret_encrypted = self.encryption.encrypt(api_secret, account.id)
        self.db.add_account(account.to_dict())
        return account

    def update_account(self, account_id: str, name: str = None,
                       api_key: str = None, api_secret: str = None,
                       is_active: bool = None,
                       api_version: str = None) -> Optional[Account]:
        """Update account details"""
        account_data = self.db.get_account(account_id)
        if not account_data:
            return None

        account = Account.from_dict(account_data)
        if name is not None:
            account.name = name
        if api_key is not None:
            account.api_key_encrypted = self.encryption.encrypt(api_key, account_id)
        if api_secret is not None:
            account.api_secret_encrypted = self.encryption.encrypt(api_secret, account_id)
        if is_active is not None:
            account.is_active = is_active
        if api_version is not None:
            account.api_version = self._normalize_api_version(api_version)

        account.updated_at = datetime.now().isoformat()
        self.db.update_account(account.to_dict())
        return account

    def get_account(self, account_id: str) -> Optional[Account]:
        """Get account by ID"""
        data = self.db.get_account(account_id)
        if data:
            return Account.from_dict(data)
        return None

    def get_all_accounts(self) -> list[Account]:
        """Get all accounts"""
        return [Account.from_dict(d) for d in self.db.get_all_accounts()]

    def get_active_accounts(self) -> list[Account]:
        """Get active accounts only"""
        return [Account.from_dict(d) for d in self.db.get_active_accounts()]

    def delete_account(self, account_id: str):
        """Delete account"""
        self.db.delete_account(account_id)

    def test_connection(self, account_id: str) -> dict:
        """Test API connection for an account"""
        account = self.get_account(account_id)
        if not account:
            return {'success': False, 'error': 'Account not found'}

        try:
            api_key = self.encryption.decrypt(account.api_key_encrypted, account.id)
            api_secret = self.encryption.decrypt(account.api_secret_encrypted, account.id)
        except Exception as e:
            return {'success': False, 'error': f'Decryption failed: {e}'}

        client = IndodaxClient(api_key, api_secret, account.api_version)
        result = client.test_connection()

        if 'error' in result:
            return {'success': False, 'error': result['error']}

        if account.api_version == 'v2' and result.get('can_trade') is False:
            error = ('API key v2 tidak memiliki izin trading atau IP belum '
                     'masuk whitelist')
            account.last_error = error
            self.db.update_account(account.to_dict())
            return {'success': False, 'error': error}

        # Update last_connected_at
        account.last_connected_at = datetime.now().isoformat()
        account.last_error = None
        self.db.update_account(account.to_dict())

        balance = result.get('balance', {})
        idr_balance = float(balance.get('idr', 0)) if isinstance(balance, dict) else 0

        return {
            'success': True,
            'balance': result,
            'idr_balance': idr_balance,
        }

    def get_decrypted_credentials(self, account_id: str) -> Optional[dict]:
        """Get decrypted API credentials for an account"""
        account = self.get_account(account_id)
        if not account:
            return None
        try:
            return {
                'api_key': self.encryption.decrypt(account.api_key_encrypted, account.id),
                'api_secret': self.encryption.decrypt(account.api_secret_encrypted, account.id),
                'api_version': self._normalize_api_version(account.api_version),
            }
        except Exception:
            return None

    @staticmethod
    def _normalize_api_version(value: str) -> str:
        version = str(value or 'v1').strip().lower()
        if version not in ('v1', 'v2'):
            raise ValueError('Versi API Indodax harus v1 atau v2')
        return version
