import json
import os
import tempfile
import unittest

from database.database import DatabaseManager


class OperationalAlertsTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        self.db = DatabaseManager(self.path)
        self.db.connect()

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_alert_is_redacted_deduplicated_and_resolved(self):
        alert_id = self.db.raise_alert(
            kind='EXCHANGE_CIRCUIT_OPEN',
            dedupe_key='circuit:test:ticker',
            severity='ERROR',
            message='api_key=FIRST_SECRET failed',
            metadata={'session_token': 'SESSION_SECRET'},
        )
        repeated_id = self.db.raise_alert(
            kind='EXCHANGE_CIRCUIT_OPEN',
            dedupe_key='circuit:test:ticker',
            severity='CRITICAL',
            message='Authorization: Bearer bearer-secret failed again',
        )
        self.assertEqual(alert_id, repeated_id)
        row = dict(self.db.connection.execute(
            'SELECT * FROM alerts WHERE id=?', (alert_id,)).fetchone())
        self.assertEqual(row['occurrences'], 2)
        self.assertEqual(row['severity'], 'CRITICAL')
        serialized = f"{row['message']} {row['metadata']}"
        for secret in ('FIRST_SECRET', 'SESSION_SECRET', 'bearer-secret'):
            self.assertNotIn(secret, serialized)
        self.assertTrue(self.db.resolve_alert('circuit:test:ticker'))
        status = self.db.connection.execute(
            'SELECT status FROM alerts WHERE id=?', (alert_id,)).fetchone()[0]
        self.assertEqual(status, 'RESOLVED')

    def test_restart_loop_alerts_only_at_threshold(self):
        self.assertEqual(self.db.record_runtime_start('python-test'), 1)
        self.assertEqual(self.db.record_runtime_start('python-test'), 2)
        self.assertIsNone(self.db.connection.execute(
            "SELECT id FROM alerts WHERE kind='RESTART_LOOP'").fetchone())
        self.assertEqual(self.db.record_runtime_start('python-test'), 3)
        row = self.db.connection.execute(
            "SELECT * FROM alerts WHERE kind='RESTART_LOOP'").fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row['metadata'])
        self.assertEqual(metadata['starts_in_window'], 3)
        self.assertIsNone(row['account_id'])


if __name__ == '__main__':
    unittest.main()
