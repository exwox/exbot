const assert = require('assert');
const { runBacktest, cumulativeDistance } = require('../backtest-engine');

const candle = (timestamp, open, high, low, close) =>
    ({ timestamp, open, high, low, close });
const baseStrategy = {
    base_order_amount: 100,
    safety_order_amount: 100,
    max_safety_orders: 0,
    price_deviation: 10,
    deviation_scale: 1,
    step_scale_enabled: false,
    volume_scale: 1,
    martingale_enabled: false,
    take_profit_percent: 10,
    stop_loss_percent: 0,
    market_buy_fee_percent: 0,
    market_sell_fee_percent: 0,
    limit_buy_fee_percent: 0,
    limit_sell_fee_percent: 0
};

assert.strictEqual(cumulativeDistance(3, 2, 1.5, false), 6);
assert.strictEqual(cumulativeDistance(3, 2, 1.5, true), 9.5);

const takeProfit = runBacktest([
    candle(1, 100, 101, 99, 100),
    candle(2, 100, 111, 99, 105)
], baseStrategy, { initialCapital: 100 });
assert.strictEqual(takeProfit.summary.total_cycles, 1);
assert.strictEqual(takeProfit.summary.wins, 1);
assert(Math.abs(takeProfit.summary.net_profit - 10) < 1e-8);
assert.strictEqual(takeProfit.cycles[0].close_reason, 'TAKE_PROFIT');

const withSafetyOrder = runBacktest([
    candle(1, 100, 101, 99, 100),
    // Bullish path is O -> L -> H -> C, so SO fills before TP.
    candle(2, 100, 101, 89, 101)
], {
    ...baseStrategy,
    max_safety_orders: 1,
    take_profit_percent: 5
}, { initialCapital: 200 });
assert.strictEqual(withSafetyOrder.summary.total_cycles, 1);
assert.strictEqual(withSafetyOrder.cycles[0].safety_orders_filled, 1);
assert(Math.abs(withSafetyOrder.cycles[0].realized_profit - 10) < 1e-8);

const stopLoss = runBacktest([
    candle(1, 100, 101, 99, 100),
    candle(2, 100, 101, 89, 90)
], {
    ...baseStrategy,
    take_profit_percent: 50,
    stop_loss_percent: 10
}, { initialCapital: 100 });
assert.strictEqual(stopLoss.cycles[0].close_reason, 'STOP_LOSS');
assert.strictEqual(stopLoss.summary.losses, 1);
assert(Math.abs(stopLoss.summary.net_profit + 10) < 1e-8);

const bearishTakesProfitBeforeLow = runBacktest([
    candle(1, 100, 101, 99, 100),
    // Bearish path is O -> H -> L -> C. The position closes at TP before
    // the later low can fill an SO.
    candle(2, 100, 111, 89, 95)
], {
    ...baseStrategy,
    max_safety_orders: 1
}, { initialCapital: 200 });
assert.strictEqual(bearishTakesProfitBeforeLow.cycles[0].close_reason, 'TAKE_PROFIT');
assert.strictEqual(bearishTakesProfitBeforeLow.cycles[0].safety_orders_filled, 0);
assert.strictEqual(bearishTakesProfitBeforeLow.summary.ambiguous_candles, 1);

const underfundedGrid = runBacktest([
    candle(1, 100, 101, 99, 100),
    candle(2, 100, 100, 89, 90)
], {
    ...baseStrategy,
    max_safety_orders: 1,
    take_profit_percent: 50
}, { initialCapital: 100 });
assert.strictEqual(underfundedGrid.summary.required_capital, 200);
assert.strictEqual(underfundedGrid.summary.capital_coverage_percent, 50);
assert.strictEqual(underfundedGrid.summary.unfunded_safety_orders, 1);
assert(underfundedGrid.warnings.some(message => message.includes('grid DCA')));

const feeAwareOpenPosition = runBacktest([
    candle(1, 100, 100, 100, 100),
    candle(2, 100, 100, 100, 100)
], {
    ...baseStrategy,
    market_buy_fee_percent: 0.3,
    market_sell_fee_percent: 0.3
}, { initialCapital: 100 });
assert.strictEqual(feeAwareOpenPosition.summary.realized_profit, 0);
assert(Math.abs(feeAwareOpenPosition.summary.unrealized_profit + 0.3) < 1e-8);
assert(Math.abs(
    feeAwareOpenPosition.summary.net_profit -
    feeAwareOpenPosition.summary.realized_profit -
    feeAwareOpenPosition.summary.unrealized_profit
) < 1e-8);
assert(feeAwareOpenPosition.summary.liquidation_unrealized_profit <
    feeAwareOpenPosition.summary.unrealized_profit);

const feeAwareTakeProfit = runBacktest([
    candle(1, 100, 100, 100, 100),
    candle(2, 100, 103, 100, 102)
], {
    ...baseStrategy,
    take_profit_percent: 1,
    market_buy_fee_percent: 0.3,
    limit_sell_fee_percent: 0.15
}, { initialCapital: 100 });
assert.strictEqual(feeAwareTakeProfit.summary.total_cycles, 1);
assert(Math.abs(feeAwareTakeProfit.summary.realized_profit - 1) < 1e-8);
assert(Math.abs(feeAwareTakeProfit.cycles[0].realized_profit_percent - 1) < 1e-8);

console.log('Backtest engine: OK');
