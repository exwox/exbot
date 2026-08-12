const assert = require('node:assert/strict');
const { test } = require('node:test');

const {
    REQUIRED_CONFIRMATION,
    liveTradingGate,
    requireLiveTrading
} = require('../live-trading-policy');

test('live trading policy fails closed unless every operator gate is set', () => {
    const disabled = liveTradingGate({});
    assert.equal(disabled.allowed, false);
    assert.equal(disabled.exposure_limit_idr, 0);
    assert.throws(
        () => requireLiveTrading('bot_a', 0, {
            LIVE_TRADING_ENABLED: 'true',
            LIVE_TRADING_CONFIRMATION: REQUIRED_CONFIRMATION,
            MAX_ACCOUNT_EXPOSURE_IDR: '0',
            LIVE_TRADING_BOT_IDS: 'bot_a'
        }),
        error => error.code === 'LIVE_TRADING_BLOCKED' && error.statusCode === 403
    );
});

test('live trading policy accepts the explicit bounded-risk configuration', () => {
    const environment = {
        LIVE_TRADING_ENABLED: 'true',
        LIVE_TRADING_CONFIRMATION: REQUIRED_CONFIRMATION,
        MAX_ACCOUNT_EXPOSURE_IDR: '250000',
        LIVE_TRADING_BOT_IDS: 'bot_a, bot_b'
    };
    const strategy = {
        base_order_amount: 15000,
        safety_order_amount: 15000,
        max_safety_orders: 5,
        martingale_enabled: false,
        volume_scale: 1.5,
        stop_loss_percent: 8,
        max_position_amount: 90000
    };
    const gate = requireLiveTrading('bot_a', 3, environment, strategy);
    assert.equal(gate.allowed, true);
    assert.equal(gate.confirmed, true);
    assert.equal(gate.bot_allowed, true);
    assert.equal(gate.allowed_bot_count, 2);
    assert.equal(gate.exposure_limit_idr, 250000);
    assert.equal(gate.strategy_risk_ready, true);
    assert.equal(gate.planned_capital_idr, 90000);
    assert.deepEqual(gate.reasons, []);
    assert.throws(
        () => requireLiveTrading('bot_c', 3, environment, strategy),
        error => error.code === 'LIVE_TRADING_BLOCKED'
            && error.gate.bot_allowed === false
    );
});

test('live trading policy rejects missing stop-loss and undersized caps', () => {
    const environment = {
        LIVE_TRADING_ENABLED: 'true',
        LIVE_TRADING_CONFIRMATION: REQUIRED_CONFIRMATION,
        MAX_ACCOUNT_EXPOSURE_IDR: '80000',
        LIVE_TRADING_BOT_IDS: 'bot_a'
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
            && error.gate.reasons.some(reason => reason.includes('stop-loss'))
    );
});
