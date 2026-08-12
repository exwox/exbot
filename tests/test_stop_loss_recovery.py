import os
import tempfile
import unittest
from datetime import datetime

from core.bot_worker import BotWorker
from database.database import DatabaseManager


class FakeExitExchange:
    def __init__(self, mode='open'):
        self.mode = mode
        self.remote = None
        self.sell_calls = 0

    def sell_market(self, _pair, amount, client_order_id=''):
        self.sell_calls += 1
        self.remote = {
            'order_id': 'sl-exchange-1', 'client_order_id': client_order_id,
            'status': 'open', 'price': 9000, 'amount': amount,
            'amount_remaining': amount, 'filled_amount': 0, 'filled_quote': 0,
        }
        if self.mode == 'timeout_filled':
            self.remote.update({
                'status': 'filled', 'amount_remaining': 0,
                'filled_amount': amount, 'filled_quote': amount * 9000,
            })
            return {'error': 'Timeout: stop-loss ACK lost'}
        if self.mode == 'partial':
            self.remote.update({
                'status': 'partially_filled',
                'amount_remaining': amount - 0.4,
                'filled_amount': 0.4, 'filled_quote': 3600,
            })
        return {'order_id': 'sl-exchange-1'}

    def get_order_by_client_id(self, _pair, client_order_id):
        if self.remote and self.remote['client_order_id'] == client_order_id:
            return dict(self.remote)
        return {'error': 'invalid client order id'}

    def get_order_status(self, _pair, order_id):
        if self.remote and self.remote['order_id'] == order_id:
            return dict(self.remote)
        return {'error': 'invalid order'}

    def cancel_order(self, _pair, order_id, side):
        return {'order_id': str(order_id), 'side': side}


class StopLossRecoveryTest(unittest.TestCase):
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
            ('account_exit', 'Exit Test', 'Indodax', '', '', 1, now, now),
        )
        self.db.connection.execute(
            """INSERT INTO bots
               (id, account_id, name, exchange, pair, status, dry_run,
                strategy_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ('bot_exit', 'account_exit', 'Exit Bot', 'Indodax', 'btcidr',
             'RUNNING', 0, None, now, now),
        )
        self.db.connection.commit()
        self.position = {
            'id': 'position_exit', 'bot_id': 'bot_exit', 'status': 'OPEN',
            'base_price': 10000, 'average_entry_price': 10000,
            'base_amount': 1, 'total_amount': 1, 'sold_amount': 0,
            'total_invested': 10000, 'take_profit_price': 10150,
            'stop_loss_price': 9000, 'current_price': 9000,
            'so_entries': [], 'tp_order_id': None, 'exit_order_id': None,
            'exit_reason': '', 'open_orders': [],
        }
        self.db.save_position(self.position)

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def worker(self, client):
        return BotWorker(
            'account_exit', 'bot_exit', 'btcidr', client,
            {
                'base_order_amount': 10000, 'safety_order_amount': 10000,
                'max_safety_orders': 0, 'stop_loss_percent': 10,
                'market_sell_fee_percent': 0.30,
            }, self.db, dry_run=False,
        )

    def state(self, worker):
        return worker._build_state(self.db.get_position('bot_exit'))

    def test_lost_stop_loss_ack_closes_once(self):
        client = FakeExitExchange('timeout_filled')
        worker = self.worker(client)
        worker._execute_stop_loss(self.state(worker), 9000)

        self.assertIsNone(self.db.get_position('bot_exit'))
        self.assertEqual(client.sell_calls, 1)
        trades = self.db.connection.execute(
            "SELECT * FROM trades WHERE bot_id='bot_exit'"
        ).fetchall()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['close_reason'], 'STOP_LOSS')

    def test_restart_reconciles_open_stop_loss_without_resubmit(self):
        client = FakeExitExchange('open')
        worker = self.worker(client)
        worker._execute_stop_loss(self.state(worker), 9000)
        pending = self.db.get_position('bot_exit')
        self.assertEqual(pending['exit_order_id'], 'sl-exchange-1')
        self.assertEqual(client.sell_calls, 1)

        client.remote.update({
            'status': 'filled', 'amount_remaining': 0,
            'filled_amount': 1, 'filled_quote': 9000,
        })
        self.worker(client)._reconcile_exit_order(pending)
        self.assertIsNone(self.db.get_position('bot_exit'))
        self.assertEqual(client.sell_calls, 1)

    def test_partial_stop_loss_replay_is_idempotent(self):
        client = FakeExitExchange('partial')
        worker = self.worker(client)
        worker._execute_stop_loss(self.state(worker), 9000)
        partial = self.db.get_position('bot_exit')
        self.assertAlmostEqual(partial['sold_amount'], 0.4)
        self.assertEqual(self.db.connection.execute(
            "SELECT COUNT(*) FROM trades WHERE bot_id='bot_exit'"
        ).fetchone()[0], 1)

        worker._reconcile_exit_order(partial)
        replayed = self.db.get_position('bot_exit')
        self.assertAlmostEqual(replayed['sold_amount'], 0.4)
        self.assertEqual(self.db.connection.execute(
            "SELECT COUNT(*) FROM trades WHERE bot_id='bot_exit'"
        ).fetchone()[0], 1)

        client.remote.update({
            'status': 'filled', 'amount_remaining': 0,
            'filled_amount': 1, 'filled_quote': 9000,
        })
        worker._reconcile_exit_order(replayed)
        self.assertIsNone(self.db.get_position('bot_exit'))
        self.assertEqual(self.db.connection.execute(
            "SELECT COUNT(*) FROM trades WHERE bot_id='bot_exit'"
        ).fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
