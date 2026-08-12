import unittest
from unittest.mock import patch

from config import settings


class LiveTradingRiskPolicyTest(unittest.TestCase):
    def strategy(self, **updates):
        strategy = {
            'base_order_amount': 15000,
            'safety_order_amount': 15000,
            'max_safety_orders': 5,
            'martingale_enabled': False,
            'volume_scale': 1.5,
            'stop_loss_percent': 8,
            'max_position_amount': 90000,
        }
        strategy.update(updates)
        return strategy

    def policy(self, strategy):
        with patch.multiple(
                settings,
                LIVE_TRADING_ENABLED=True,
                LIVE_TRADING_CONFIRMATION='I_ACCEPT_LIVE_TRADING_RISK',
                MAX_ACCOUNT_EXPOSURE_IDR=100000,
                LIVE_TRADING_BOT_IDS=frozenset({'bot_a'}),
                LIVE_MIN_DRY_RUN_CYCLES=3):
            return settings.live_trading_allowed_for(
                'bot_a', 3, strategy)

    def test_bounded_strategy_is_allowed_after_operator_gates(self):
        self.assertTrue(self.policy(self.strategy()))

    def test_missing_stop_loss_or_undersized_position_is_rejected(self):
        self.assertFalse(self.policy(self.strategy(stop_loss_percent=0)))
        self.assertFalse(self.policy(self.strategy(max_position_amount=89999)))


if __name__ == '__main__':
    unittest.main()
