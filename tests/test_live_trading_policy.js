const assert = require('node:assert/strict');
const { test } = require('node:test');

const {
    liveTradingGate,
    requireLiveTrading
} = require('../live-trading-policy');

test('live trading policy fails closed unless the master gate is set', () => {
    const disabled = liveTradingGate({});
    assert.equal(disabled.allowed, false);
    assert.equal(disabled.exposure_limit_idr, 0);
    assert.equal(disabled.minimum_dry_run_cycles, 1);
    assert.throws(
        () => requireLiveTrading('bot_a', 0, {
            LIVE_TRADING_ENABLED: 'true',
            MAX_ACCOUNT_EXPOSURE_IDR: '0'
        }, null),
        error => error.code === 'LIVE_TRADING_BLOCKED' && error.statusCode === 403
    );
});

test('live trading policy accepts a bounded strategy without confirmation or allowlist', () => {
    const environment = {
        LIVE_TRADING_ENABLED: 'true',
        MAX_ACCOUNT_EXPOSURE_IDR: '250000'
    };
    const strategy = {
        base_order_amount: 15000,
        safety_order_amount: 15000,
        max_safety_orders: 5,
        martingale_enabled: false,
        volume_scale: 1.5,
        stop_loss_percent: 0,
        max_position_amount: 90000
    };
    const gate = requireLiveTrading('bot_a', 3, environment, strategy);
    assert.equal(gate.allowed, true);
    assert.equal(gate.enabled, true);
    assert.equal(gate.exposure_limit_idr, 250000);
    assert.equal(gate.strategy_risk_ready, true);
    assert.equal(gate.planned_capital_idr, 90000);
    assert.equal(gate.bot_allowed, true);
    assert.deepEqual(gate.reasons, []);
});

test('live trading policy honours zero dry-run cycle requirement', () => {
    const environment = {
        LIVE_TRADING_ENABLED: 'true',
        MAX_ACCOUNT_EXPOSURE_IDR: '250000',
        LIVE_MIN_DRY_RUN_CYCLES: '0'
    };
    const gate = requireLiveTrading('bot_a', 0, environment, {
        base_order_amount: 15000,
        safety_order_amount: 15000,
        max_safety_orders: 5,
        martingale_enabled: false,
        volume_scale: 1.5,
        stop_loss_percent: 0,
        max_position_amount: 90000
    });
    assert.equal(gate.allowed, true);
    assert.equal(gate.minimum_dry_run_cycles, 0);
    assert.equal(gate.dry_run_evidence_ready, true);
    // Nilai negatif tidak valid: kembali ke default 1.
    assert.equal(liveTradingGate({ LIVE_MIN_DRY_RUN_CYCLES: '-3' })
        .minimum_dry_run_cycles, 1);
});

test('live trading policy rejects undersized position caps and exposure', () => {
    const environment = {
        LIVE_TRADING_ENABLED: 'true',
        MAX_ACCOUNT_EXPOSURE_IDR: '80000'
    };
    assert.throws(
        () => requireLiveTrading('bot_a', 3, environment, {
            base_order_amount: 15000,
            safety_order_amount: 15000,
            max_safety_orders: 5,
            martingale_enabled: false,
            volume_scale: 1.5,
            stop_loss_percent: 0,
            max_position_amount: 80000
        }),
        error => error.code === 'LIVE_TRADING_BLOCKED'
            && error.gate.strategy_risk_ready === false
            && error.gate.planned_capital_idr === 90000
            && error.gate.reasons.some(reason => reason.includes('batas posisi'))
    );
});
