import os
import tempfile
import time
import unittest
from datetime import datetime

from config.settings import API_CIRCUIT_FAILURE_THRESHOLD
from core.bot_worker import BotWorker
from database.database import DatabaseManager


class FailingTickerExchange:
    def __init__(self):
        self.error = 'temporary gateway failure'

    def get_ticker(self, _pair):
        return {'error': self.error}


class ApiCircuitBreakerTest(unittest.TestCase):
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
            ('account_cb', 'Circuit Test', 'Indodax', '', '', 1, now, now),
        )
        self.db.connection.execute(
            """INSERT INTO bots
               (id, account_id, name, exchange, pair, status, dry_run,
                strategy_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ('bot_cb', 'account_cb', 'Circuit Bot', 'Indodax', 'btcidr',
             'RUNNING', 0, None, now, now),
        )
        self.db.connection.commit()
        self.client = FailingTickerExchange()
        self.worker = BotWorker(
            'account_cb', 'bot_cb', 'btcidr', self.client,
            {'base_order_amount': 10000, 'safety_order_amount': 10000,
             'max_safety_orders': 0},
            self.db, dry_run=False,
        )

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_repeated_api_failures_open_then_release_circuit(self):
        for _ in range(API_CIRCUIT_FAILURE_THRESHOLD):
            self.assertIsNone(self.worker._get_current_price())

        self.assertTrue(self.worker._circuit_is_open())
        self.assertIn('ticker', self.worker._circuit_reason)
        alert = self.db.connection.execute(
            "SELECT * FROM alerts WHERE dedupe_key=?",
            ('circuit:bot_cb:ticker',),
        ).fetchone()
        self.assertIsNotNone(alert)
        self.assertEqual(alert['status'], 'OPEN')
        self.worker._circuit_open_until = time.monotonic() - 1
        self.assertFalse(self.worker._circuit_is_open())
        self.assertEqual(self.worker._api_failure_counts, {})
        status = self.db.connection.execute(
            "SELECT status FROM alerts WHERE dedupe_key=?",
            ('circuit:bot_cb:ticker',),
        ).fetchone()[0]
        self.assertEqual(status, 'RESOLVED')

    def test_timestamp_error_trips_immediately(self):
        self.worker._record_api_failure(
            'base_submit', 'Invalid timestamp from exchange')

        self.assertTrue(self.worker._circuit_is_open())
        self.assertIn('timestamp', self.worker._circuit_reason.lower())

    def test_success_resets_only_the_matching_operation(self):
        for _ in range(API_CIRCUIT_FAILURE_THRESHOLD - 1):
            self.worker._record_api_failure('balance', 'temporary failure')
        self.worker._record_api_failure('open_orders', 'temporary failure')
        self.worker._record_api_success('balance')

        self.assertNotIn('balance', self.worker._api_failure_counts)
        self.assertEqual(self.worker._api_failure_counts['open_orders'], 1)
        self.assertFalse(self.worker._circuit_is_open())


if __name__ == '__main__':
    unittest.main()
