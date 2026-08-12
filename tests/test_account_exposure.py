import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from core.bot_worker import BotWorker
from database.database import DatabaseManager


class ExposureExchange:
    def __init__(self):
        self.buy_count = 0

    def get_balance(self):
        return {'balance': {'idr': 1_000_000, 'btc': 100}}

    def buy_market(self, _pair, _amount_quote, _client_order_id=''):
        self.buy_count += 1
        return {'order_id': 'unexpected-base'}


class AccountExposureTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        self.db = DatabaseManager(self.path)
        self.db.connect()
        now = datetime.now().isoformat()
        self.db.connection.execute(
            """INSERT INTO accounts
               (id, name, exchange, api_key_encrypted, api_secret_encrypted,
                is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ('account_exp', 'Exposure Test', 'Indodax', '', '', 1, now, now),
        )
        for bot_id, dry_run in (
                ('bot_existing', 0), ('bot_new', 0), ('bot_dry', 1)):
            self.db.connection.execute(
                """INSERT INTO bots
                   (id, account_id, name, exchange, pair, status, dry_run,
                    strategy_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (bot_id, 'account_exp', bot_id, 'Indodax', 'btcidr',
                 'RUNNING', dry_run, None, now, now),
            )
        self.db.connection.commit()
        self.db.save_position(self.position(
            'position_existing', 'bot_existing', 50_000, 10_000))
        self.db.save_position(self.position(
            'position_dry', 'bot_dry', 500_000, 500_000))

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    @staticmethod
    def position(position_id, bot_id, reserved, invested):
        return {
            'id': position_id, 'bot_id': bot_id, 'status': 'OPEN',
            'base_price': 10000, 'average_entry_price': 10000,
            'base_amount': 1, 'total_amount': 1, 'sold_amount': 0,
            'total_invested': invested, 'reserved_capital': reserved,
            'take_profit_price': 10200, 'stop_loss_price': 0,
            'current_price': 10000, 'so_entries': [],
            'tp_order_id': None, 'exit_order_id': None,
            'exit_reason': '', 'open_orders': [],
        }

    def test_live_reservation_is_conservative_and_dry_run_is_ignored(self):
        self.assertEqual(
            self.db.get_account_exposure('account_exp'), 50_000)
        self.db.close_position('bot_existing', 'CLOSED')
        self.assertEqual(self.db.get_account_exposure('account_exp'), 0)

    def test_account_limit_blocks_before_exchange_submit(self):
        client = ExposureExchange()
        worker = BotWorker(
            'account_exp', 'bot_new', 'btcidr', client,
            {'base_order_amount': 10_000, 'safety_order_amount': 10_000,
             'max_safety_orders': 1},
            self.db, dry_run=False,
        )

        with patch('core.bot_worker.MAX_ACCOUNT_EXPOSURE_IDR', 60_000):
            worker._execute_start_bot(10_000)

        self.assertEqual(client.buy_count, 0)
        self.assertIsNone(self.db.get_position('bot_new'))
        self.assertEqual(
            self.db.get_account_exposure('account_exp'), 50_000)


if __name__ == '__main__':
    unittest.main()
