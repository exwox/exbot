import unittest

from config.strategy_defaults import strategy_defaults
from core.bot_worker import BotWorker


class LogCaptureDatabase:
    def __init__(self):
        self.logs = []

    def add_log(self, **entry):
        self.logs.append(entry)


class MarketNotificationSignalTest(unittest.TestCase):
    def setUp(self):
        self.db = LogCaptureDatabase()
        self.worker = BotWorker(
            'account', 'bot', 'btcidr', object(), strategy_defaults(),
            self.db, dry_run=True)

    def events(self, name):
        return [row for row in self.db.logs if row['event'] == name]

    def test_price_signal_uses_material_change_reference(self):
        self.worker._emit_market_signals(100_000, 65)
        self.worker._emit_market_signals(104_900, 65)
        self.assertEqual(self.events('PRICE_SIGNAL'), [])

        self.worker._emit_market_signals(105_000, 65)
        self.assertEqual(len(self.events('PRICE_SIGNAL')), 1)
        self.worker._emit_market_signals(106_000, 65)
        self.assertEqual(len(self.events('PRICE_SIGNAL')), 1)

    def test_rsi_signal_only_when_entering_extreme_zone(self):
        self.worker._emit_market_signals(100_000, 59)
        self.worker._emit_market_signals(100_000, 58)
        self.assertEqual(len(self.events('RSI_SIGNAL')), 1)

        self.worker._emit_market_signals(100_000, 65)
        self.worker._emit_market_signals(100_000, 71)
        self.worker._emit_market_signals(100_000, 72)
        self.assertEqual(len(self.events('RSI_SIGNAL')), 2)


if __name__ == '__main__':
    unittest.main()
