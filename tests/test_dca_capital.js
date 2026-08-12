const assert = require('assert');
const {
    calculateRequiredCapital,
    maximumAffordableSafetyOrders,
    scaleOrdersToBudget,
    buildCapitalPlan
} = require('../dashboard/dca-capital');

const fixedGrid = {
    base_order_amount: 15000,
    safety_order_amount: 15000,
    max_safety_orders: 5,
    martingale_enabled: false,
    volume_scale: 1.5
};
assert.strictEqual(calculateRequiredCapital(fixedGrid), 90000);
assert.strictEqual(maximumAffordableSafetyOrders(45000, fixedGrid), 2);

const martingaleGrid = {
    base_order_amount: 160000,
    safety_order_amount: 160000,
    max_safety_orders: 10,
    martingale_enabled: true,
    volume_scale: 2
};
assert.strictEqual(calculateRequiredCapital(martingaleGrid), 163840000);

const plan = buildCapitalPlan(1000000, martingaleGrid);
assert.strictEqual(plan.safe_budget, 900000);
assert.strictEqual(plan.affordable_safety_orders, 2);
assert(Math.abs(plan.coverage_percent - 0.6103515625) < 1e-10);
assert.strictEqual(plan.fully_covered, false);
assert.strictEqual(plan.scaled_orders.feasible, false);

const scalable = scaleOrdersToBudget(90000, {
    base_order_amount: 100000,
    safety_order_amount: 100000,
    max_safety_orders: 2,
    martingale_enabled: false,
    volume_scale: 1
});
assert.strictEqual(scalable.feasible, true);
assert.strictEqual(scalable.base_order_amount, 30000);
assert.strictEqual(scalable.safety_order_amount, 30000);
assert.strictEqual(scalable.required_capital, 90000);

console.log('DCA capital planner: OK');
