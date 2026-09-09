'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { evaluateLiveRolloutPreflight } = require('../live-rollout-preflight');

test('preflight reports running simulations and orders without blocking real mode', () => {
    const result = evaluateLiveRolloutPreflight({
        bot: { id: 'bot', status: 'RUNNING', dry_run: 1, account_active: 1 },
        strategy: { base_order_amount: 15000, safety_order_amount: 15000,
            max_safety_orders: 5, max_position_amount: 0 },
        completedDryRunCycles: 0, activePositions: 1, recoverableOrders: 3
    }, { LIVE_TRADING_ENABLED: 'false', LIVE_MIN_DRY_RUN_CYCLES: '100' });
    assert.equal(result.allowed, true);
    assert.equal(result.readiness.gate_enforced, false);
    assert.equal(result.active_positions, 1);
    assert.equal(result.recoverable_orders, 3);
    assert.equal(result.notes.length, 2);
    assert.deepEqual(result.reasons, []);
});

test('preflight also reports existing real bots; only missing bots fail', () => {
    assert.equal(evaluateLiveRolloutPreflight({
        bot: { id: 'bot', status: 'RUNNING', dry_run: 0, account_active: 1 }
    }).allowed, true);
    const missing = evaluateLiveRolloutPreflight({});
    assert.equal(missing.allowed, false);
    assert.deepEqual(missing.reasons, ['bot tidak ditemukan']);
});
