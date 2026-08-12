'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const rawDefaults = require('../config/strategy_defaults.json');
const { DEFAULT_STRATEGY, strategyDefaults } = require('../strategy-defaults');

test('Node strategy defaults use the shared cross-runtime contract', () => {
    assert.deepEqual(DEFAULT_STRATEGY, rawDefaults);
    assert.deepEqual(strategyDefaults(), rawDefaults);
    assert.equal(DEFAULT_STRATEGY.dry_run, true);
    assert.notEqual(strategyDefaults(), strategyDefaults());
});
