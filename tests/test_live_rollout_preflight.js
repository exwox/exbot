'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { evaluateLiveRolloutPreflight } = require('../live-rollout-preflight');

const environment = {
    LIVE_TRADING_ENABLED: 'true',
    LIVE_TRADING_CONFIRMATION: 'I_ACCEPT_LIVE_TRADING_RISK',
    LIVE_TRADING_BOT_IDS: 'bot_pilot',
    LIVE_MIN_DRY_RUN_CYCLES: '1',
    MAX_ACCOUNT_EXPOSURE_IDR: '100000'
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

test('rollout preflight accepts only a stopped and clean dry-run bot', () => {
    const result = evaluateLiveRolloutPreflight({
        bot: { id: 'bot_pilot', status: 'STOPPED', dry_run: 1, account_active: 1 },
        strategy,
        completedDryRunCycles: 1,
        activePositions: 0,
        recoverableOrders: 0
    }, environment);
    assert.equal(result.allowed, true);
    assert.deepEqual(result.reasons, []);
});

test('rollout preflight rejects active state even when environment gate passes', () => {
    const result = evaluateLiveRolloutPreflight({
        bot: { id: 'bot_pilot', status: 'RUNNING', dry_run: 1, account_active: 1 },
        strategy,
        completedDryRunCycles: 1,
        activePositions: 1,
        recoverableOrders: 2
    }, environment);
    assert.equal(result.allowed, false);
    assert.ok(result.reasons.includes('bot harus berstatus STOPPED'));
    assert.ok(result.reasons.includes('masih ada posisi aktif'));
    assert.ok(result.reasons.some(reason => reason.includes('direkonsiliasi')));
});
