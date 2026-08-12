import unittest
import os
import tempfile
from unittest.mock import patch
from models.strategy import Strategy
from database.database import DatabaseManager
from core.bot_worker import BotWorker
from exchanges.indodax_client import IndodaxClient


class TestInitialEntryModes(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.db_path = self.temp_db.name
        self.temp_db.close()
        self.db = DatabaseManager(self.db_path)
        self.db.connect()

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_strategy_persistence_with_entry_modes(self):
        strat = Strategy(
            name="Limit Buy Strategy",
            initial_entry_mode="LIMIT",
            limit_buy_fee_percent=0.15,
            market_buy_fee_percent=0.30
        )
        strat_dict = strat.to_dict()
        strat_id = self.db.add_strategy(strat_dict)

        loaded = self.db.get_strategy(strat_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['initial_entry_mode'], 'LIMIT')
        self.assertEqual(loaded['limit_buy_fee_percent'], 0.15)
        self.assertEqual(loaded['market_buy_fee_percent'], 0.30)

        # Update initial_entry_mode to RSI_LIMIT
        loaded['initial_entry_mode'] = 'RSI_LIMIT'
        self.db.update_strategy(loaded)

        updated = self.db.get_strategy(strat_id)
        self.assertEqual(updated['initial_entry_mode'], 'RSI_LIMIT')

    def test_bot_worker_start_entry_modes(self):
        client = IndodaxClient(api_key="test", secret_key="test")
        self.db.add_account({
            'id': 'acc1', 'name': 'Account 1', 'exchange': 'indodax',
            'api_key_encrypted': 'key', 'api_secret_encrypted': 'secret',
            'is_active': 1, 'last_connected_at': None, 'last_error': None
        })
        self.db.add_bot({'id': 'bot1', 'account_id': 'acc1', 'name': 'Bot 1', 'exchange': 'indodax', 'pair': 'btc_idr', 'status': 'STOPPED', 'dry_run': 1, 'strategy_id': 'str1'})
        self.db.add_bot({'id': 'bot2', 'account_id': 'acc1', 'name': 'Bot 2', 'exchange': 'indodax', 'pair': 'btc_idr', 'status': 'STOPPED', 'dry_run': 1, 'strategy_id': 'str1'})

        # 1. MARKET mode -> immediate entry on start
        strat_market = {'initial_entry_mode': 'MARKET', 'base_order_amount': 15000}
        worker_market = BotWorker("acc1", "bot1", "btc_idr", client, strat_market, self.db, dry_run=True)

        # 2. LIMIT mode -> immediate entry on start
        strat_limit = {'initial_entry_mode': 'LIMIT', 'base_order_amount': 15000}
        worker_limit = BotWorker("acc1", "bot2", "btc_idr", client, strat_limit, self.db, dry_run=True)

        # 3. Dry run base order execution test for LIMIT vs MARKET
        worker_market._execute_start_bot(current_price=100000000)
        pos_market = self.db.get_position("bot1")
        self.assertIsNotNone(pos_market)
        self.assertEqual(pos_market['total_invested'], 15000)

        worker_limit._execute_start_bot(current_price=100000000)
        pos_limit = self.db.get_position("bot2")
        self.assertIsNotNone(pos_limit)
        self.assertEqual(pos_limit['total_invested'], 15000)

    def test_user_subscription_expiration_autostop(self):
        # 1. Insert an expired user into database
        cursor = self.db.connection.cursor()
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, salt, is_active, expired_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("usr_exp", "expired_user", "exp@test.com", "hash", "salt", 1, "2020-01-01", "2020-01-01", "2020-01-01")
        )
        self.db.connection.commit()

        # 2. Add active account and running bot for this user
        self.db.add_account({
            'id': 'acc_exp', 'user_id': 'usr_exp', 'name': 'Account Expired', 'exchange': 'indodax',
            'api_key_encrypted': 'key', 'api_secret_encrypted': 'secret',
            'is_active': 1, 'last_connected_at': None, 'last_error': None
        })
        self.db.add_bot({
            'id': 'bot_exp', 'account_id': 'acc_exp', 'name': 'Bot Expired',
            'exchange': 'indodax', 'pair': 'btc_idr', 'status': 'RUNNING', 'dry_run': 1, 'strategy_id': None
        })

        from services.encryption_service import EncryptionService
        from services.account_service import AccountService
        from core.bot_manager import BotManager

        # Mock encryption service to decrypt test key
        class DummyEncryption:
            def decrypt(self, val): return 'test'
            def encrypt(self, val): return 'test'

        account_service = AccountService(self.db, DummyEncryption())
        bot_manager = BotManager(self.db, DummyEncryption())
        bot_manager.account_service = account_service

        # 3. Run reconciliation loop
        bot_manager.reconcile_workers()

        # 4. Verify bot was automatically STOPPED
        bot_exp = self.db.get_bot('bot_exp')
        self.assertEqual(bot_exp['status'], 'STOPPED')

    def test_live_worker_is_stopped_when_rollout_gate_is_closed(self):
        self.db.add_account({
            'id': 'acc_live', 'name': 'Live Account', 'exchange': 'indodax',
            'api_key_encrypted': 'key', 'api_secret_encrypted': 'secret',
            'is_active': 1, 'last_connected_at': None, 'last_error': None
        })
        self.db.add_bot({
            'id': 'bot_live', 'account_id': 'acc_live', 'name': 'Live Bot',
            'exchange': 'indodax', 'pair': 'btcidr', 'status': 'RUNNING',
            'dry_run': 0, 'strategy_id': None
        })

        class DummyEncryption:
            def decrypt(self, _value): return 'test'
            def encrypt(self, _value): return 'test'

        from core.bot_manager import BotManager
        manager = BotManager(self.db, DummyEncryption())
        with patch('core.bot_manager.live_trading_allowed_for', return_value=False):
            manager.initialize()

        self.assertEqual(self.db.get_bot('bot_live')['status'], 'STOPPED')
        self.assertEqual(
            self.db.get_completed_dry_run_cycle_count('bot_live'), 0)
        self.assertNotIn('bot_live', manager.workers)
        alert = self.db.connection.execute(
            "SELECT * FROM alerts WHERE dedupe_key='live-gate:bot_live'"
        ).fetchone()
        self.assertIsNotNone(alert)
        self.assertEqual(alert['kind'], 'LIVE_TRADING_BLOCKED')


if __name__ == '__main__':
    unittest.main()
