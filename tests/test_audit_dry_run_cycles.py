import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from database.database import DatabaseManager
from scripts.audit_dry_run_cycles import audit_dry_run_cycles


class DryRunCycleAuditTest(unittest.TestCase):
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
               VALUES ('account', 'Test', 'Indodax', '', '', 1, ?, ?)""",
            (now, now))
        self.db.connection.execute(
            """INSERT INTO bots
               (id, account_id, name, exchange, pair, status, dry_run,
                strategy_id, created_at, updated_at)
               VALUES ('bot', 'account', 'Bot', 'Indodax', 'btcidr',
                       'STOPPED', 1, NULL, ?, ?)""", (now, now))
        self.db.connection.commit()
        self.db.add_trade({
            'account_id': 'account', 'bot_id': 'bot',
            'position_id': 'cycle', 'order_id': 'bo', 'pair': 'btcidr',
            'side': 'buy', 'trade_type': 'base', 'price': 100,
            'amount': 1, 'amount_quote': 100, 'fee': 0.3, 'dry_run': True,
        })
        self.db.add_trade({
            'account_id': 'account', 'bot_id': 'bot',
            'position_id': 'cycle', 'order_id': 'tp', 'pair': 'btcidr',
            'side': 'sell', 'trade_type': 'take_profit', 'price': 110,
            'amount': 1, 'amount_quote': 109, 'fee': 1, 'cost_basis': 100,
            'realized_profit': 9, 'realized_profit_percent': 9,
            'close_reason': 'TAKE_PROFIT', 'dry_run': True,
        })

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_valid_cycle_matches_trade_ledger(self):
        report = audit_dry_run_cycles(Path(self.path), 'bot', 1)
        self.assertTrue(report['valid'])
        self.assertEqual(report['valid_closed_cycles'], 1)

    def test_cycle_mismatch_is_fail_closed(self):
        self.db.connection.execute(
            "UPDATE dca_cycles SET realized_profit=99 WHERE id='cycle'")
        self.db.connection.commit()
        report = audit_dry_run_cycles(Path(self.path), 'bot', 1)
        self.assertFalse(report['valid'])
        self.assertFalse(report['cycles'][0]['checks']['realized_profit'])


if __name__ == '__main__':
    unittest.main()
