import os
import tempfile
import unittest
from datetime import datetime, timezone

from core.bot_worker import BotWorker
from database.database import DatabaseManager


class NoExchangeCalls:
    def __getattr__(self, name):
        raise AssertionError(f'dry-run unexpectedly called exchange method {name}')


class DryRunLedgerTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        self.db = DatabaseManager(self.path)
        self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        self.db.connection.execute(
            """INSERT INTO accounts
               (id, name, exchange, api_key_encrypted, api_secret_encrypted,
                is_active, created_at, updated_at)
               VALUES ('account_dry', 'Dry', 'Indodax', '', '', 1, ?, ?)""",
            (now, now),
        )
        self.db.connection.execute(
            """INSERT INTO bots
               (id, account_id, name, exchange, pair, status, dry_run,
                strategy_id, created_at, updated_at)
               VALUES ('bot_dry', 'account_dry', 'Dry Bot', 'Indodax',
                       'btcidr', 'RUNNING', 1, NULL, ?, ?)""",
            (now, now),
        )
        self.db.connection.commit()
        self.worker = BotWorker(
            'account_dry', 'bot_dry', 'btcidr', NoExchangeCalls(),
            {
                'base_order_amount': 15000,
                'safety_order_amount': 15000,
                'max_safety_orders': 2,
                'price_deviation': 1,
                'deviation_scale': 1,
                'take_profit_percent': 1,
            },
            self.db,
            dry_run=True,
        )

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def orders(self):
        return [dict(row) for row in self.db.connection.execute(
            'SELECT * FROM orders WHERE bot_id=? ORDER BY created_at',
            ('bot_dry',),
        ).fetchall()]

    def test_full_dry_cycle_has_consistent_order_statuses(self):
        self.worker._execute_start_bot(100_000_000)
        position = self.db.get_position('bot_dry')
        orders = self.orders()
        self.assertEqual(len(orders), 4)  # filled BO, TP, and two SOs
        self.assertEqual(
            [order['status'] for order in orders
             if order['order_type'].startswith('base')],
            ['FILLED'],
        )
        self.assertEqual(len([
            order for order in orders
            if order['order_type'] == 'take_profit'
            and order['status'] == 'OPEN'
        ]), 1)

        first_so_price = max(
            float(order['price']) for order in position['open_orders'])
        self.worker._simulate_safety_orders(
            position['open_orders'], first_so_price)
        position = self.db.get_position('bot_dry')
        self.assertEqual(len(position['so_entries']), 1)
        self.assertEqual(len([
            order for order in self.orders()
            if order['order_type'] == 'so_1' and order['status'] == 'FILLED'
        ]), 1)

        self.worker._simulate_safety_orders(
            position['open_orders'], position['take_profit_price'])
        cycle = self.db.connection.execute(
            "SELECT * FROM dca_cycles WHERE id=?", (position['id'],)
        ).fetchone()
        self.assertEqual(cycle['status'], 'CLOSED')
        self.assertEqual(
            self.db.get_completed_dry_run_cycle_count('bot_dry'), 1)
        terminal = self.orders()
        self.assertEqual(len([
            order for order in terminal
            if order['order_type'] == 'take_profit'
            and order['status'] == 'FILLED'
        ]), 1)
        self.assertFalse(any(order['status'] == 'OPEN' for order in terminal))

    def test_manual_reset_is_not_live_readiness_evidence(self):
        self.worker._execute_start_bot(100_000_000)
        self.worker.reset_active_position()

        cycle = self.db.connection.execute(
            "SELECT * FROM dca_cycles WHERE bot_id='bot_dry'"
        ).fetchone()
        self.assertEqual(cycle['status'], 'CLOSED')
        self.assertEqual(cycle['close_reason'], 'MANUALLY_RESET')
        self.assertEqual(
            self.db.get_completed_dry_run_cycle_count('bot_dry'), 0)

    def test_repairs_legacy_missing_tp_and_open_filled_base(self):
        self.worker._execute_start_bot(100_000_000)
        position = self.db.get_position('bot_dry')
        tp_id = position['tp_order_id']
        stale_so_id = position['open_orders'][0]['order_id']
        position['open_orders'] = position['open_orders'][1:]
        self.db.save_position(position)
        self.db.connection.execute(
            "DELETE FROM orders WHERE exchange_order_id=?", (tp_id,))
        self.db.connection.execute(
            "UPDATE orders SET status='OPEN' WHERE order_type LIKE 'base%'"
        )
        self.db.connection.commit()

        self.worker._repair_simulated_order_ledger(position)
        orders = self.orders()
        self.assertTrue(any(
            order['exchange_order_id'] == tp_id
            and order['order_type'] == 'take_profit'
            and order['status'] == 'OPEN'
            for order in orders
        ))
        self.assertTrue(all(
            order['status'] == 'FILLED'
            for order in orders if order['order_type'].startswith('base')
        ))
        self.assertTrue(any(
            order['exchange_order_id'] == stale_so_id
            and order['status'] == 'CANCELLED'
            for order in orders
        ))


if __name__ == '__main__':
    unittest.main()
