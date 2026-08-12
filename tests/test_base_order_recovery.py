import os
import tempfile
import unittest
from datetime import datetime

from core.bot_worker import BotWorker
from database.database import DatabaseManager


class FakeBaseExchange:
    def __init__(self, mode='open'):
        self.mode = mode
        self.remote = None
        self.client_ids = []
        self.cancelled = []
        self.tp_count = 0

    def get_balance(self):
        return {'balance': {'idr': 1000000, 'btc': 100}}

    def buy_market(self, _pair, amount_quote, client_order_id=''):
        self.client_ids.append(client_order_id)
        self.remote = {
            'order_id': 'base-exchange-1', 'client_order_id': client_order_id,
            'status': 'open', 'price': 10000, 'amount': 1,
            'amount_remaining': 1, 'filled_amount': 0, 'filled_quote': 0,
        }
        if self.mode == 'timeout_filled':
            self.remote.update({
                'status': 'filled', 'amount_remaining': 0,
                'filled_amount': amount_quote / 10000,
                'filled_quote': amount_quote,
            })
            return {'error': 'Timeout: response lost after submit'}
        return {'order_id': 'base-exchange-1'}

    def buy(self, _pair, _price, _amount, client_order_id=''):
        raise AssertionError(f'unexpected SO/limit buy: {client_order_id}')

    def get_order_status(self, _pair, _order_id):
        return dict(self.remote) if self.remote else {'error': 'invalid order'}

    def get_order_by_client_id(self, _pair, client_order_id):
        if self.remote and self.remote.get('client_order_id') == client_order_id:
            return dict(self.remote)
        return {'error': 'invalid client order id'}

    def cancel_order(self, _pair, order_id, side):
        self.cancelled.append((str(order_id), side))
        return {'order_id': str(order_id)}

    def sell(self, _pair, _price, _amount, _client_order_id=''):
        self.tp_count += 1
        return {'order_id': f'base-tp-{self.tp_count}'}


class BaseIntentRecoveryTest(unittest.TestCase):
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
            ('account_base', 'Base Test', 'Indodax', '', '', 1, now, now),
        )
        self.db.connection.execute(
            """INSERT INTO bots
               (id, account_id, name, exchange, pair, status, dry_run,
                strategy_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ('bot_base', 'account_base', 'Base Bot', 'Indodax', 'btcidr',
             'RUNNING', 0, None, now, now),
        )
        self.db.connection.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def worker(self, client):
        return BotWorker(
            'account_base', 'bot_base', 'btcidr', client,
            {
                'base_order_amount': 10000,
                'safety_order_amount': 10000,
                'max_safety_orders': 0,
                'initial_entry_mode': 'MARKET',
                'market_buy_fee_percent': 0.30,
            }, self.db, dry_run=False,
        )

    def test_lost_submit_ack_is_recovered_by_client_order_id(self):
        client = FakeBaseExchange('timeout_filled')
        self.worker(client)._execute_start_bot(10000)

        position = self.db.get_position('bot_base')
        self.assertEqual(position['status'], 'OPEN')
        self.assertAlmostEqual(position['base_amount'], 0.997)
        self.assertEqual(self.db.connection.execute(
            "SELECT COUNT(*) FROM trades WHERE bot_id='bot_base'"
        ).fetchone()[0], 1)
        self.assertIsNone(self.db.get_pending_base_order(
            'bot_base', position['id']))
        ledger = self.db.connection.execute(
            "SELECT * FROM orders WHERE order_type='base_market'"
        ).fetchone()
        self.assertEqual(ledger['status'], 'FILLED')
        self.assertEqual(ledger['exchange_order_id'], 'base-exchange-1')
        self.assertTrue(ledger['client_order_id'].startswith('xb_basema_'))
        self.assertLessEqual(len(ledger['client_order_id']), 36)

    def test_restart_recovers_pending_base_without_second_buy(self):
        client = FakeBaseExchange('open')
        self.worker(client)._execute_start_bot(10000)
        pending = self.db.get_position('bot_base')
        self.assertEqual(pending['status'], 'PENDING_BASE')
        self.assertEqual(len(client.client_ids), 1)
        self.assertEqual(client.tp_count, 0)

        client.remote.update({
            'status': 'filled', 'amount_remaining': 0,
            'filled_amount': 1, 'filled_quote': 10000,
        })
        self.worker(client)._reconcile_pending_base(pending)

        recovered = self.db.get_position('bot_base')
        self.assertEqual(recovered['status'], 'OPEN')
        self.assertEqual(len(client.client_ids), 1)
        self.assertEqual(client.tp_count, 1)
        self.assertEqual(self.db.connection.execute(
            "SELECT COUNT(*) FROM trades WHERE bot_id='bot_base'"
        ).fetchone()[0], 1)

    def test_partial_base_is_cancelled_then_protected(self):
        client = FakeBaseExchange('open')
        worker = self.worker(client)
        worker._execute_start_bot(10000)
        pending = self.db.get_position('bot_base')
        client.remote.update({
            'status': 'partially_filled', 'amount_remaining': 0.6,
            'filled_amount': 0.4, 'filled_quote': 4000,
        })
        worker._reconcile_pending_base(pending)

        position = self.db.get_position('bot_base')
        self.assertEqual(position['status'], 'OPEN')
        self.assertAlmostEqual(position['base_amount'], 0.3988)
        self.assertEqual(position['total_invested'], 4000)
        self.assertEqual(client.cancelled, [('base-exchange-1', 'buy')])
        self.assertEqual(client.tp_count, 1)

    def test_stop_cancels_pending_base_intent(self):
        client = FakeBaseExchange('open')
        worker = self.worker(client)
        worker._execute_start_bot(10000)

        worker._cancel_all_orders()

        pending = self.db.get_pending_base_order('bot_base')
        self.assertIsNotNone(pending)
        self.assertEqual(pending['status'], 'CANCELLED')
        self.assertEqual(client.cancelled, [('base-exchange-1', 'buy')])
        position = self.db.get_position('bot_base')
        self.assertEqual(position['status'], 'PENDING_BASE')
        self.assertEqual(position['open_orders'], [])

    def test_restart_promotes_partial_fill_cancelled_during_stop(self):
        client = FakeBaseExchange('open')
        worker = self.worker(client)
        worker._execute_start_bot(10000)
        worker._cancel_all_orders()
        client.remote.update({
            'status': 'cancelled', 'amount_remaining': 0.6,
            'filled_amount': 0.4, 'filled_quote': 4000,
        })

        restarted = self.worker(client)
        restarted._reconcile_pending_base(
            self.db.get_position('bot_base'))

        position = self.db.get_position('bot_base')
        self.assertEqual(position['status'], 'OPEN')
        self.assertAlmostEqual(position['base_amount'], 0.3988)
        self.assertEqual(position['total_invested'], 4000)
        self.assertEqual(client.cancelled, [('base-exchange-1', 'buy')])
        self.assertEqual(client.tp_count, 1)


if __name__ == '__main__':
    unittest.main()
