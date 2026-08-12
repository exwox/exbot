import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.set_bot_risk import set_bot_risk


class SetBotRiskTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / 'risk.db'
        connection = sqlite3.connect(self.database)
        connection.executescript("""
            CREATE TABLE strategies (
                id TEXT PRIMARY KEY, base_order_amount REAL,
                safety_order_amount REAL, max_safety_orders INTEGER,
                martingale_enabled INTEGER, volume_scale REAL,
                stop_loss_percent REAL, max_position_amount REAL,
                updated_at TEXT
            );
            CREATE TABLE bots (
                id TEXT PRIMARY KEY, strategy_id TEXT, dry_run INTEGER,
                status TEXT
            );
            INSERT INTO strategies VALUES
                ('strategy',15000,15000,5,0,1.5,0,0,'now');
            INSERT INTO bots VALUES ('bot','strategy',1,'RUNNING');
        """)
        connection.commit()
        connection.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def values(self):
        connection = sqlite3.connect(self.database)
        row = connection.execute(
            'SELECT stop_loss_percent,max_position_amount FROM strategies'
        ).fetchone()
        connection.close()
        return row

    def test_preview_does_not_write_and_apply_updates_bounded_values(self):
        preview = set_bot_risk(self.database, 'bot', 8, 90000)
        self.assertFalse(preview['applied'])
        self.assertEqual(preview['planned_capital_idr'], 90000)
        self.assertEqual(self.values(), (0, 0))

        applied = set_bot_risk(
            self.database, 'bot', 8, 90000, apply=True)
        self.assertTrue(applied['applied'])
        self.assertEqual(self.values(), (8, 90000))

    def test_rejects_undersized_limit_and_live_bot(self):
        with self.assertRaises(ValueError):
            set_bot_risk(self.database, 'bot', 8, 89999, apply=True)
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE bots SET dry_run=0 WHERE id='bot'")
        connection.commit()
        connection.close()
        with self.assertRaises(ValueError):
            set_bot_risk(self.database, 'bot', 8, 90000, apply=True)


if __name__ == '__main__':
    unittest.main()
