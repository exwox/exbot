import os
import tempfile
import unittest
from datetime import datetime

from database.database import DatabaseManager


class TradeLedgerTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DatabaseManager(self.path)
        self.db.connect()
        now = datetime.now().isoformat()
        self.db.connection.execute(
            """INSERT INTO accounts
               (id, name, exchange, api_key_encrypted, api_secret_encrypted,
                is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("account_test", "Test", "Indodax", "", "", 1, now, now),
        )
        self.db.connection.execute(
            """INSERT INTO bots
               (id, account_id, name, exchange, pair, status, dry_run,
                strategy_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "bot_test", "account_test", "Test Bot", "Indodax", "btcidr",
                "RUNNING", 1, None, now, now,
            ),
        )
        self.db.connection.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_filled_trade_and_realized_profit_are_persisted(self):
        self.db.add_trade({
            "account_id": "account_test",
            "bot_id": "bot_test",
            "position_id": "position_test",
            "order_id": "base_test",
            "pair": "btcidr",
            "side": "buy",
            "trade_type": "base",
            "price": 100,
            "amount": 1,
            "amount_quote": 100,
            "fee": 0.3,
            "dry_run": True,
        })
        trade_id = self.db.add_trade({
            "account_id": "account_test",
            "bot_id": "bot_test",
            "position_id": "position_test",
            "order_id": "order_test",
            "pair": "btcidr",
            "side": "sell",
            "trade_type": "take_profit",
            "price": 110,
            "amount": 1,
            "amount_quote": 109,
            "fee": 1,
            "cost_basis": 100,
            "realized_profit": 9,
            "realized_profit_percent": 9,
            "close_reason": "TAKE_PROFIT",
            "dry_run": True,
        })

        row = self.db.connection.execute(
            "SELECT * FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        self.assertEqual(row["trade_type"], "take_profit")
        self.assertEqual(row["close_reason"], "TAKE_PROFIT")
        self.assertEqual(row["realized_profit"], 9)
        self.assertEqual(row["amount_quote"], 109)
        self.assertEqual(row["dry_run"], 1)
        self.assertTrue(row["executed_at"].endswith("Z"))
        self.assertTrue(row["created_at"].endswith("Z"))
        cycle = self.db.connection.execute(
            "SELECT * FROM dca_cycles WHERE id='position_test'"
        ).fetchone()
        self.assertEqual(cycle["status"], "CLOSED")
        self.assertEqual(cycle["total_invested"], 100)
        self.assertEqual(cycle["realized_profit"], 9)


if __name__ == "__main__":
    unittest.main()
