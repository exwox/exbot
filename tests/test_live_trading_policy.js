const assert = require('node:assert/strict');
const { test } = require('node:test');
const { liveTradingGate, liveTradingReadiness } = require('../live-trading-policy');

test('real mode has no environment, evidence, or capital gate', () => {
    const environment = {
        LIVE_TRADING_ENABLED: 'false',
        LIVE_TRADING_CONFIRMATION: '',
        LIVE_TRADING_BOT_IDS: 'different_bot',
        LIVE_MIN_DRY_RUN_CYCLES: '100',
        MAX_ACCOUNT_EXPOSURE_IDR: '1'
    };
    assert.equal(liveTradingGate({}).allowed, true);
    const result = liveTradingReadiness('new_bot', 0, environment, {
        base_order_amount: 15000, safety_order_amount: 15000,
        max_safety_orders: 5, martingale_enabled: false,
        stop_loss_percent: 0, max_position_amount: 0
    });
    assert.equal(result.allowed, true);
    assert.equal(result.gate_enforced, false);
    assert.equal(result.minimum_dry_run_cycles, 0);
    assert.equal(result.completed_dry_run_cycles, 0);
    assert.equal(result.planned_capital_idr, 90000);
    assert.deepEqual(result.reasons, []);
});

test('readiness can report a bot without a persisted strategy', () => {
    const result = liveTradingReadiness('new_bot');
    assert.equal(result.allowed, true);
    assert.equal(result.planned_capital_idr, 0);
});
