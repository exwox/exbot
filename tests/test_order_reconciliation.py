import os
import tempfile
import unittest
from datetime import datetime

from core.bot_worker import BotWorker
from database.database import DatabaseManager
from exchanges.indodax_client import IndodaxClient


class FakeExchange:
    def __init__(self):
        self.orders = []
        self.trade_history = []
        self.cancelled = []
        self.cancel_failures = set()
        self.next_tp = 2

    def get_open_orders(self, _pair):
        return list(self.orders)

    def get_order_status(self, _pair, order_id):
        for order in self.orders:
            if str(order.get('order_id')) == str(order_id):
                return order
        return {'status': 'open', 'order_id': str(order_id)}

    def cancel_order(self, _pair, order_id, side):
        self.cancelled.append((str(order_id), side))
        if str(order_id) in self.cancel_failures:
            return {'error': 'simulated cancellation failure'}
        return {'order_id': str(order_id)}

    def get_balance(self):
        return {'balance': {'btc': 100, 'idr': 100000000}}

    def sell(self, _pair, _price, _amount, _client_order_id=''):
        order_id = f'tp{self.next_tp}'
        self.next_tp += 1
        return {'order_id': order_id}

    def buy(self, _pair, _price, _amount, _client_order_id=''):
        raise AssertionError('reconciliation must not duplicate an existing SO')

    def get_order_by_client_id(self, _pair, _client_order_id):
        return {'error': 'invalid client order id'}

    def get_trade_history(self, _pair, limit=10):
        return list(self.trade_history[:limit])


class OrderNormalizationTest(unittest.TestCase):
    def test_indodax_payload_reports_cumulative_partial_fill(self):
        client = IndodaxClient('test', 'test')
        order = client._normalize_order_payload('btcidr', {
            'order_id': '42', 'order_btc': '1.25', 'remain_btc': '0.50',
            'price': '100000000', 'status': 'open',
        })
        self.assertEqual(order['status'], 'partially_filled')
        self.assertAlmostEqual(order['filled_amount'], 0.75)
        self.assertAlmostEqual(order['filled_quote'], 75000000)

    def test_indodax_buy_payload_converts_quote_partial_fill(self):
        client = IndodaxClient('test', 'test')
        order = client._normalize_order_payload('btcidr', {
            'order_id': 'buy-42', 'type': 'buy', 'order_idr': '10000',
            'remain_idr': '4000', 'price': '10000',
        })
        self.assertEqual(order['status'], 'partially_filled')
        self.assertAlmostEqual(order['amount'], 1)
        self.assertAlmostEqual(order['filled_amount'], 0.6)
        self.assertAlmostEqual(order['filled_quote'], 6000)

    def test_cancelled_status_is_not_misclassified_as_filled(self):
        client = IndodaxClient('test', 'test')
        order = client._normalize_order_payload('btcidr', {
            'order_id': '43', 'order_btc': '1', 'remain_btc': '0',
            'price': '100', 'status': 'cancelled',
        })
        self.assertEqual(order['status'], 'cancelled')

    def test_open_payload_without_remaining_field_stays_open(self):
        client = IndodaxClient('test', 'test')
        order = client._normalize_order_payload('btcidr', {
            'order_id': '44', 'amount': '1', 'price': '100', 'status': 'open',
        })
        self.assertEqual(order['status'], 'open')
        self.assertEqual(order['filled_amount'], 0)

    def test_limit_buy_uses_official_coin_form_and_client_id(self):
        client = IndodaxClient('test', 'test')
        captured = {}

        def fake_post(method, params):
            captured.update({'method': method, 'params': params})
            return {'order_id': '1'}

        client._post_private = fake_post
        client.buy('btcidr', 10000, 1.25, 'client-order-1')
        self.assertEqual(captured['method'], 'trade')
        self.assertEqual(captured['params']['order_type'], 'limit')
        self.assertEqual(captured['params']['btc'], '1.25')
        self.assertEqual(captured['params']['client_order_id'], 'client-order-1')
        self.assertNotIn('idr', captured['params'])
        self.assertNotIn('amount', captured['params'])


class ReconciliationTest(unittest.TestCase):
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
            ('account_test', 'Test', 'Indodax', '', '', 1, now, now),
        )
        self.db.connection.execute(
            """INSERT INTO bots
               (id, account_id, name, exchange, pair, status, dry_run,
                strategy_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ('bot_test', 'account_test', 'Test Bot', 'Indodax', 'btcidr',
             'RUNNING', 0, None, now, now),
        )
        self.db.connection.commit()
        self.client = FakeExchange()
        self.worker = BotWorker(
            'account_test', 'bot_test', 'btcidr', self.client,
            {
                'base_order_amount': 10000,
                'safety_order_amount': 10000,
                'max_safety_orders': 1,
                'limit_buy_fee_percent': 0.15,
                'limit_sell_fee_percent': 0.15,
            },
            self.db, dry_run=False,
        )
        self.position = {
            'id': 'position_test', 'bot_id': 'bot_test', 'status': 'OPEN',
            'base_price': 10000, 'average_entry_price': 10000,
            'base_amount': 1, 'total_amount': 1, 'sold_amount': 0,
            'total_invested': 10000, 'take_profit_price': 10200,
            'stop_loss_price': 0, 'current_price': 10000,
            'so_entries': [], 'tp_order_id': 'tp1',
            'open_orders': [{
                'order_id': 'so1', 'type': 'so_1', 'price': 9000,
                'amount_idr': 9000, 'amount_crypto': 0.9985,
                'gross_amount_crypto': 1, 'so_number': 1,
            }],
        }
        self.db.save_position(self.position)
        self.worker._persist_order('tp1', 'sell', 10200, 1, 10200,
                                   order_type='take_profit')
        self.worker._persist_order('so1', 'buy', 9000, 1, 9000, so_number=1)

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def state(self):
        return self.worker._build_state(self.db.get_position('bot_test'))

    def trade_count(self):
        return self.db.connection.execute(
            "SELECT COUNT(*) FROM trades WHERE position_id='position_test'"
        ).fetchone()[0]

    def test_failed_stop_cancellation_keeps_ids_for_retry(self):
        self.client.cancel_failures = {'so1'}

        self.worker._cancel_all_orders()

        retained = self.db.get_position('bot_test')
        self.assertEqual(
            [order['order_id'] for order in retained['open_orders']], ['so1'])
        self.assertIsNone(retained['tp_order_id'])
        statuses = {
            row['exchange_order_id']: row['status']
            for row in self.db.connection.execute(
                "SELECT exchange_order_id, status FROM orders"
            ).fetchall()
        }
        self.assertEqual(statuses['so1'], 'OPEN')
        self.assertEqual(statuses['tp1'], 'CANCELLED')
        alert = self.db.connection.execute(
            "SELECT status FROM alerts WHERE dedupe_key='order-cancel:bot_test'"
        ).fetchone()
        self.assertEqual(alert['status'], 'OPEN')

        self.client.cancel_failures.clear()
        self.worker._cancel_all_orders()

        recovered = self.db.get_position('bot_test')
        self.assertEqual(recovered['open_orders'], [])
        self.assertEqual(self.db.connection.execute(
            "SELECT status FROM orders WHERE exchange_order_id='so1'"
        ).fetchone()['status'], 'CANCELLED')
        self.assertEqual(self.db.connection.execute(
            "SELECT status FROM alerts WHERE dedupe_key='order-cancel:bot_test'"
        ).fetchone()['status'], 'RESOLVED')

    def test_legacy_intent_recovers_only_unique_trade_history_order(self):
        intent_id = self.db.add_order({
            'id': 'legacy_intent', 'bot_id': 'bot_test',
            'account_id': 'account_test', 'position_id': 'position_test',
            'order_type': 'so_1', 'side': 'buy', 'pair': 'btcidr',
            'price': 9000, 'amount': 1, 'amount_quote': 9000,
            'status': 'OPEN',
        })
        intent = next(order for order in self.db.get_bot_orders('bot_test')
                      if order['id'] == intent_id)
        self.client.trade_history = [{
            'trade_id': 'trade-legacy', 'order_id': 'legacy-order-1',
            'type': 'buy', 'price': '9000', 'amount': '0.4',
        }]
        self.client.orders = [{
            'order_id': 'legacy-order-1', 'status': 'partially_filled',
            'price': 9000, 'filled_amount': 0.4, 'filled_quote': 3600,
        }]

        recovered = self.worker._recover_order_intent(intent)

        self.assertEqual(recovered['order_id'], 'legacy-order-1')
        stored = next(order for order in self.db.get_bot_orders('bot_test')
                      if order['id'] == intent_id)
        self.assertEqual(stored['exchange_order_id'], 'legacy-order-1')
        self.assertEqual(stored['status'], 'PARTIALLY_FILLED')

        # More than one matching order remains a manual-recovery incident.
        self.client.trade_history.append({
            'trade_id': 'trade-other', 'order_id': 'legacy-order-2',
            'type': 'buy', 'price': '9000', 'amount': '0.2',
        })
        self.assertIsNone(
            self.worker._recover_legacy_order_from_trade_history(intent))

    def test_partial_and_final_fills_are_idempotent(self):
        self.client.orders = [
            {'order_id': 'tp1', 'status': 'open', 'filled_amount': 0},
            {'order_id': 'so1', 'status': 'partially_filled', 'price': 9000,
             'filled_amount': 0.4, 'filled_quote': 3600},
        ]
        self.worker._sync_and_manage_orders(self.state(), 9500)
        partial_position = self.db.get_position('bot_test')
        self.assertAlmostEqual(partial_position['total_amount'], 1.3994)
        self.assertAlmostEqual(partial_position['total_invested'], 13600)
        self.assertEqual(self.trade_count(), 1)
        self.assertFalse(partial_position['so_entries'][0]['finalized'])

        # Replaying the exact cumulative response must not add inventory/trade.
        self.client.orders = [
            {'order_id': 'tp2', 'status': 'open', 'filled_amount': 0},
            {'order_id': 'so1', 'status': 'partially_filled', 'price': 9000,
             'filled_amount': 0.4, 'filled_quote': 3600},
        ]
        self.worker._sync_and_manage_orders(self.state(), 9500)
        replayed = self.db.get_position('bot_test')
        self.assertAlmostEqual(replayed['total_amount'], 1.3994)
        self.assertEqual(self.trade_count(), 1)

        # Completion records only the 0.6-coin delta and finalizes SO1 once.
        self.client.orders = [
            {'order_id': 'tp2', 'status': 'open', 'filled_amount': 0},
            {'order_id': 'so1', 'status': 'filled', 'price': 9000,
             'filled_amount': 1, 'filled_quote': 9000},
        ]
        self.worker._sync_and_manage_orders(self.state(), 9500)
        completed_so = self.db.get_position('bot_test')
        self.assertAlmostEqual(completed_so['total_amount'], 1.9985)
        self.assertAlmostEqual(completed_so['total_invested'], 19000)
        self.assertEqual(self.trade_count(), 2)
        self.assertTrue(completed_so['so_entries'][0]['finalized'])
        cycle = self.db.connection.execute(
            "SELECT * FROM dca_cycles WHERE id='position_test'"
        ).fetchone()
        self.assertEqual(cycle['safety_orders_filled'], 1)

        # A repeated TP partial response also produces one ledger delta only.
        self.client.orders = [
            {'order_id': 'tp3', 'status': 'partially_filled', 'price': 10200,
             'filled_amount': 0.4, 'filled_quote': 4080},
        ]
        self.worker._sync_and_manage_orders(self.state(), 10200)
        self.worker._sync_and_manage_orders(self.state(), 10200)
        partial_tp = self.db.get_position('bot_test')
        self.assertAlmostEqual(partial_tp['sold_amount'], 0.4)
        self.assertEqual(self.trade_count(), 3)

        self.client.orders = [
            {'order_id': 'tp3', 'status': 'filled', 'price': 10200,
             'filled_amount': 1.9985, 'filled_quote': 20384.7},
        ]
        self.worker._sync_and_manage_orders(self.state(), 10200)
        self.assertIsNone(self.db.get_position('bot_test'))
        self.assertEqual(self.trade_count(), 4)
        cycle = self.db.connection.execute(
            "SELECT * FROM dca_cycles WHERE id='position_test'"
        ).fetchone()
        self.assertEqual(cycle['status'], 'CLOSED')
        self.assertEqual(cycle['close_reason'], 'TAKE_PROFIT')

    def test_cancelled_partial_so_never_rounds_residual_above_target(self):
        self.client.orders = [
            {'order_id': 'tp1', 'status': 'open', 'filled_amount': 0},
            {'order_id': 'so1', 'status': 'partially_filled', 'price': 9000,
             'filled_amount': 0.4, 'filled_quote': 3600},
        ]
        self.worker._sync_and_manage_orders(self.state(), 9500)
        self.client.orders = [
            {'order_id': 'tp2', 'status': 'open', 'filled_amount': 0},
            {'order_id': 'so1', 'status': 'cancelled', 'price': 9000,
             'filled_amount': 0.4, 'filled_quote': 3600},
        ]
        self.worker._sync_and_manage_orders(self.state(), 9500)

        position = self.db.get_position('bot_test')
        self.assertEqual(position['open_orders'], [])
        self.assertTrue(position['so_entries'][0]['finalized'])
        self.assertEqual(
            position['so_entries'][0]['completion_reason'],
            'residual_below_exchange_minimum',
        )
        self.assertAlmostEqual(position['total_invested'], 13600)
        self.assertEqual(self.trade_count(), 1)

    def test_cancelled_partial_tp_is_replaced_for_remaining_inventory(self):
        self.client.orders = [
            {'order_id': 'tp1', 'status': 'cancelled', 'price': 10200,
             'filled_amount': 0.4, 'filled_quote': 4080},
            {'order_id': 'so1', 'status': 'open', 'price': 9000,
             'filled_amount': 0, 'filled_quote': 0},
        ]

        self.worker._sync_and_manage_orders(self.state(), 10200)

        position = self.db.get_position('bot_test')
        self.assertAlmostEqual(position['sold_amount'], 0.4)
        self.assertEqual(position['tp_order_id'], 'tp2')
        self.assertEqual(self.trade_count(), 1)
        old_tp = self.db.connection.execute(
            "SELECT status FROM orders WHERE exchange_order_id='tp1'"
        ).fetchone()
        self.assertEqual(old_tp['status'], 'CANCELLED')

        # The terminal cumulative response is no longer associated with the
        # active TP and cannot add a duplicate trade on the next tick.
        self.worker._sync_and_manage_orders(self.state(), 10200)
        self.assertEqual(self.trade_count(), 1)


if __name__ == '__main__':
    unittest.main()
