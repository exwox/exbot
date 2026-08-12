import os
import tempfile
import unittest
from datetime import datetime

from core.bot_worker import BotWorker
from database.database import DatabaseManager


class FakeChildExchange:
    def __init__(self):
        self.by_client_id = {}
        self.sell_calls = 0
        self.buy_calls = 0
        self.cancelled = []

    def get_balance(self):
        return {'balance': {'idr': 1000000, 'btc': 100}}

    def sell(self, _pair, price, amount, client_order_id=''):
        self.sell_calls += 1
        order = {
            'order_id': 'tp-exchange-1', 'client_order_id': client_order_id,
            'status': 'open', 'price': price, 'amount': amount,
            'amount_remaining': amount, 'filled_amount': 0, 'filled_quote': 0,
        }
        self.by_client_id[client_order_id] = order
        return {'error': 'Timeout: ACK lost after TP submit'}

    def buy(self, _pair, price, amount, client_order_id=''):
        self.buy_calls += 1
        order = {
            'order_id': 'so-exchange-1', 'client_order_id': client_order_id,
            'status': 'open', 'price': price, 'amount': amount,
            'amount_remaining': amount, 'filled_amount': 0, 'filled_quote': 0,
        }
        self.by_client_id[client_order_id] = order
        return {'error': 'Timeout: ACK lost after SO submit'}

    def get_order_by_client_id(self, _pair, client_order_id):
        order = self.by_client_id.get(client_order_id)
        return dict(order) if order else {'error': 'invalid client order id'}

    def get_order_status(self, _pair, order_id):
        for order in self.by_client_id.values():
            if order['order_id'] == order_id:
                return dict(order)
        return {'error': 'invalid order'}

    def get_open_orders(self, _pair):
        return [dict(order) for order in self.by_client_id.values()
                if order.get('status') not in ('filled', 'cancelled')]

    def cancel_order(self, _pair, order_id, side):
        self.cancelled.append((str(order_id), side))
        return {'order_id': str(order_id)}

    def cancel_order_by_client_id(self, client_order_id):
        self.cancelled.append((str(client_order_id), 'client_id'))
        return {'client_order_id': str(client_order_id)}


class ChildOrderRecoveryTest(unittest.TestCase):
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
            ('account_child', 'Child Test', 'Indodax', '', '', 1, now, now),
        )
        self.db.connection.execute(
            """INSERT INTO bots
               (id, account_id, name, exchange, pair, status, dry_run,
                strategy_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ('bot_child', 'account_child', 'Child Bot', 'Indodax', 'btcidr',
             'RUNNING', 0, None, now, now),
        )
        self.db.connection.commit()
        self.position = {
            'id': 'position_child', 'bot_id': 'bot_child', 'status': 'OPEN',
            'base_price': 10000, 'average_entry_price': 10000,
            'base_amount': 1, 'total_amount': 1, 'sold_amount': 0,
            'total_invested': 10000, 'take_profit_price': 0,
            'stop_loss_price': 0, 'current_price': 10000,
            'so_entries': [], 'tp_order_id': None, 'open_orders': [],
        }
        self.db.save_position(self.position)
        self.client = FakeChildExchange()

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def worker(self):
        return BotWorker(
            'account_child', 'bot_child', 'btcidr', self.client,
            {
                'base_order_amount': 10000,
                'safety_order_amount': 10000,
                'max_safety_orders': 1,
                'price_deviation': 1,
                'take_profit_percent': 1,
            }, self.db, dry_run=False,
        )

    def test_tp_intent_recovers_after_position_save_is_lost(self):
        worker = self.worker()
        memory_position = self.db.get_position('bot_child')
        worker._place_tp_order(memory_position)
        self.assertEqual(memory_position['tp_order_id'], 'tp-exchange-1')
        self.assertEqual(self.client.sell_calls, 1)

        # Simulate a crash before save_position(memory_position).
        restarted_position = self.db.get_position('bot_child')
        self.assertIsNone(restarted_position['tp_order_id'])
        self.worker()._place_tp_order(restarted_position)
        self.assertEqual(restarted_position['tp_order_id'], 'tp-exchange-1')
        self.assertEqual(self.client.sell_calls, 1)

    def test_so_intent_recovers_after_position_save_is_lost(self):
        worker = self.worker()
        memory_position = self.db.get_position('bot_child')
        worker._place_so_order(memory_position, 1)
        self.assertEqual(memory_position['open_orders'][0]['order_id'],
                         'so-exchange-1')
        self.assertEqual(self.client.buy_calls, 1)

        # Simulate a crash before save_position(memory_position).
        restarted_position = self.db.get_position('bot_child')
        self.assertEqual(restarted_position['open_orders'], [])
        self.worker()._place_so_order(restarted_position, 1)
        self.assertEqual(restarted_position['open_orders'][0]['order_id'],
                         'so-exchange-1')
        self.assertEqual(self.client.buy_calls, 1)

    def test_stop_cancels_child_intent_missing_from_position_json(self):
        worker = self.worker()
        memory_position = self.db.get_position('bot_child')
        worker._place_tp_order(memory_position)
        self.assertIsNone(self.db.get_position('bot_child')['tp_order_id'])

        worker._cancel_all_orders()
        self.assertIn(('tp-exchange-1', 'sell'), self.client.cancelled)
        ledger = self.db.connection.execute(
            "SELECT status FROM orders WHERE order_type='take_profit'"
        ).fetchone()
        self.assertEqual(ledger['status'], 'CANCELLED')

    def test_startup_restores_and_applies_so_filled_during_downtime(self):
        first_worker = self.worker()
        memory_position = self.db.get_position('bot_child')
        first_worker._place_so_order(memory_position, 1)
        # Crash before the in-memory SO list is saved; the durable intent and
        # exchange order remain. It fills while the process is down.
        remote = next(iter(self.client.by_client_id.values()))
        remote.update({
            'status': 'filled', 'amount_remaining': 0,
            'filled_amount': remote['amount'],
            'filled_quote': 10000,
        })
        # Simulate the narrow crash window after the terminal ledger commit
        # but before inventory/position JSON is updated.
        intent = self.db.connection.execute(
            "SELECT id FROM orders WHERE order_type='so_1'"
        ).fetchone()
        self.db.update_order_submission(
            intent['id'], 'so-exchange-1', 'FILLED')

        restarted = self.worker()
        persisted = self.db.get_position('bot_child')
        self.assertEqual(persisted['open_orders'], [])
        restarted._restore_recoverable_child_orders(persisted)
        restored = self.db.get_position('bot_child')
        self.assertEqual(restored['open_orders'][0]['order_id'],
                         'so-exchange-1')

        restarted._sync_and_manage_orders(
            restarted._build_state(restored), 10000,
            replace_missing=False)

        reconciled = self.db.get_position('bot_child')
        self.assertGreater(reconciled['total_amount'], 1)
        self.assertEqual(reconciled['total_invested'], 20000)
        self.assertEqual(reconciled['open_orders'], [])
        self.assertEqual(self.client.buy_calls, 1)
        trade_count = self.db.connection.execute(
            "SELECT COUNT(*) FROM trades WHERE trade_type='so_1'"
        ).fetchone()[0]
        self.assertEqual(trade_count, 1)

if __name__ == '__main__':
    unittest.main()
