'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { detectStructure, plotlyOverlay } = require('../dashboard/smc-overlay');

test('SMC detects swing classifications and structure breaks', () => {
    const candles = [
        [8, 10, 7, 9], [9, 12, 8, 11], [11, 15, 10, 14],
        [14, 13, 9, 10], [10, 12, 6, 7], [7, 16, 8, 16],
        [13, 17, 11, 15], [15, 14, 10, 11], [11, 13, 8, 9],
        [9, 17, 9, 16], [16, 18, 12, 17], [17, 16, 7, 8],
        [8, 10, 5, 6]
    ].map(([open, high, low, close], timestamp) => ({ open, high, low, close, timestamp }));
    const result = detectStructure(candles, 1);
    assert.ok(result.swings.some(item => item.label === 'HH'));
    assert.ok(result.swings.some(item => item.label === 'HL'));
    assert.ok(result.breaks.some(item => item.label === 'BOS'));
    assert.ok(result.breaks.some(item => item.label === 'CHoCH'));

    const overlay = plotlyOverlay(candles, candles.map(item => new Date(item.timestamp)));
    assert.equal(overlay.annotations.length,
        overlay.swings.length + overlay.breaks.length);
    assert.equal(overlay.shapes.length, overlay.breaks.length);
});
