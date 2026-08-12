import json
import os
import tempfile
import unittest
from datetime import datetime

from config.constants import LogEvent
from core.bot_worker import BotWorker
from database.database import DatabaseManager


class LogRedactionTest(unittest.TestCase):
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
            ('account_log', 'Log Test', 'Indodax', '', '', 1, now, now),
        )
        self.db.connection.execute(
            """INSERT INTO bots
               (id, account_id, name, exchange, pair, status, dry_run,
                strategy_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ('bot_log', 'account_log', 'Log Bot', 'Indodax', 'btcidr',
             'RUNNING', 1, None, now, now),
        )
        self.db.connection.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_database_redacts_message_and_json_metadata(self):
        self.db.add_log(
            'account_log', 'ERROR', 'API_ERROR',
            'api_key=VISIBLE secret_key: "SECRET" '
            'Authorization: Bearer bearer-value',
            'bot_log',
            json.dumps({'session_token': 'SESSION-VALUE'}),
        )
        row = self.db.get_logs('account_log')[0]
        combined = f"{row['message']} {row['metadata']}"
        for secret in ('VISIBLE', 'SECRET', 'bearer-value', 'SESSION-VALUE'):
            self.assertNotIn(secret, combined)
        self.assertIn('[REDACTED]', combined)
        self.assertEqual(
            json.loads(row['metadata'])['session_token'], '[REDACTED]')

    def test_worker_log_contains_tick_cycle_and_order_correlation(self):
        worker = BotWorker(
            'account_log', 'bot_log', 'btcidr', object(),
            {'base_order_amount': 10000, 'safety_order_amount': 10000,
             'max_safety_orders': 0},
            self.db, dry_run=True,
        )
        worker._current_tick_id = 'tick_test'
        worker._current_cycle_id = 'cycle_test'
        worker._current_order_client_id = 'client_test'
        worker._log(LogEvent.RECONCILIATION, 'correlated event')

        metadata = json.loads(self.db.get_logs('account_log')[0]['metadata'])
        self.assertEqual(metadata, {
            'tick_id': 'tick_test',
            'cycle_id': 'cycle_test',
            'client_order_id': 'client_test',
        })


if __name__ == '__main__':
    unittest.main()
