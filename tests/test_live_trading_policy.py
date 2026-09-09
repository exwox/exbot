"""Upgrade compatibility: retired gate environment values cannot block startup."""
import os
import subprocess
import sys
import unittest


class LiveTradingSettingsTest(unittest.TestCase):
    def test_invalid_legacy_gate_values_are_ignored(self):
        result = subprocess.run(
            [sys.executable, '-c', 'import config.settings; import core.bot_manager'],
            env={**os.environ, 'LIVE_TRADING_ENABLED': 'false',
                 'LIVE_MIN_DRY_RUN_CYCLES': 'invalid',
                 'MAX_ACCOUNT_EXPOSURE_IDR': 'invalid'},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
