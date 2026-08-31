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
            'stop_loss_percent': 0,
            'max_position_amount': 90000,
        }
        strategy.update(updates)
        return strategy

    def policy(self, strategy, bot_id='bot_a'):
        with patch.multiple(
                settings,
                LIVE_TRADING_ENABLED=True,
                MAX_ACCOUNT_EXPOSURE_IDR=100000,
                LIVE_MIN_DRY_RUN_CYCLES=3):
            return settings.live_trading_allowed_for(bot_id, 3, strategy)

    def test_bounded_strategy_is_allowed_without_confirmation_or_allowlist(self):
        self.assertTrue(self.policy(self.strategy()))

    def test_stop_loss_zero_is_allowed_when_position_is_bounded(self):
        # Stop-loss 0 diperbolehkan; yang wajib adalah modal & batas posisi.
        self.assertTrue(self.policy(self.strategy(stop_loss_percent=0)))

    def test_any_bot_id_is_allowed_without_allowlist(self):
        self.assertTrue(self.policy(self.strategy(), bot_id='bot_unknown_123'))

    def test_zero_minimum_disables_dry_run_evidence(self):
        # LIVE_MIN_DRY_RUN_CYCLES=0 sah: real trade tanpa bukti dry-run.
        with patch.multiple(
                settings,
                LIVE_TRADING_ENABLED=True,
                MAX_ACCOUNT_EXPOSURE_IDR=100000,
                LIVE_MIN_DRY_RUN_CYCLES=0):
            self.assertTrue(
                settings.live_trading_allowed_for('bot_a', 0, self.strategy()))

    def test_undersized_position_or_exposure_is_rejected(self):
        self.assertFalse(self.policy(self.strategy(max_position_amount=89999)))


if __name__ == '__main__':
    unittest.main()
