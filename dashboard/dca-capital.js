(function exposeDcaCapital(root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.DcaCapital = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createDcaCapital() {
    const numberAtLeast = (value, minimum = 0) => {
        const number = Number(value);
        return Number.isFinite(number) ? Math.max(number, minimum) : minimum;
    };

    const integerAtLeast = (value, minimum = 0) =>
        Math.max(Math.trunc(numberAtLeast(value, minimum)), minimum);

    const enabled = value =>
        value === true || value === 1 || value === '1' || value === 'true';

    function normalizeStrategy(raw = {}) {
        return {
            baseOrder: numberAtLeast(raw.base_order_amount),
            safetyOrder: numberAtLeast(raw.safety_order_amount),
            maxSafetyOrders: integerAtLeast(raw.max_safety_orders),
            martingaleEnabled: enabled(raw.martingale_enabled),
            volumeScale: numberAtLeast(raw.volume_scale, 1)
        };
    }

    function safetyOrderAmount(strategy, level) {
        return strategy.martingaleEnabled
            ? strategy.safetyOrder * Math.pow(strategy.volumeScale, level - 1)
            : strategy.safetyOrder;
    }

    function calculateRequiredCapital(rawStrategy) {
        const strategy = normalizeStrategy(rawStrategy);
        let required = strategy.baseOrder;
        for (let level = 1; level <= strategy.maxSafetyOrders; level += 1) {
            required += safetyOrderAmount(strategy, level);
        }
        return required;
    }

    function maximumAffordableSafetyOrders(budget, rawStrategy) {
        const strategy = normalizeStrategy(rawStrategy);
        let remaining = numberAtLeast(budget) - strategy.baseOrder;
        if (remaining < 0) return 0;

        let affordable = 0;
        for (let level = 1; level <= strategy.maxSafetyOrders; level += 1) {
            const amount = safetyOrderAmount(strategy, level);
            if (remaining + 1e-9 < amount) break;
            remaining -= amount;
            affordable += 1;
        }
        return affordable;
    }

    function scaleOrdersToBudget(budget, rawStrategy, options = {}) {
        const strategy = normalizeStrategy(rawStrategy);
        const step = numberAtLeast(options.step, 1);
        const minimumOrder = numberAtLeast(options.minimumOrder, step);
        const required = calculateRequiredCapital(rawStrategy);
        const available = numberAtLeast(budget);

        if (required <= available) {
            return {
                feasible: true,
                changed: false,
                base_order_amount: strategy.baseOrder,
                safety_order_amount: strategy.safetyOrder,
                max_safety_orders: strategy.maxSafetyOrders,
                required_capital: required
            };
        }
        if (required <= 0 || available <= 0) {
            return { feasible: false, changed: false };
        }

        const scale = available / required;
        const roundDown = value => Math.floor((value * scale) / step) * step;
        const baseOrder = roundDown(strategy.baseOrder);
        const safetyOrder = roundDown(strategy.safetyOrder);
        if (baseOrder < minimumOrder ||
            (strategy.maxSafetyOrders > 0 && safetyOrder < minimumOrder)) {
            return { feasible: false, changed: false };
        }

        const scaled = {
            base_order_amount: baseOrder,
            safety_order_amount: safetyOrder,
            max_safety_orders: strategy.maxSafetyOrders,
            martingale_enabled: strategy.martingaleEnabled,
            volume_scale: strategy.volumeScale
        };
        return {
            feasible: calculateRequiredCapital(scaled) <= available + 1e-9,
            changed: true,
            ...scaled,
            required_capital: calculateRequiredCapital(scaled)
        };
    }

    function buildCapitalPlan(balance, rawStrategy, options = {}) {
        const reservePercent = Math.min(
            Math.max(numberAtLeast(options.reservePercent, 10), 0), 95);
        const availableBalance = numberAtLeast(balance);
        const safeBudget = availableBalance * (1 - reservePercent / 100);
        const requiredCapital = calculateRequiredCapital(rawStrategy);
        const normalized = normalizeStrategy(rawStrategy);
        const coveragePercent = requiredCapital > 0
            ? availableBalance / requiredCapital * 100 : 0;
        const safeCoveragePercent = requiredCapital > 0
            ? safeBudget / requiredCapital * 100 : 0;
        const affordableSafetyOrders =
            maximumAffordableSafetyOrders(safeBudget, rawStrategy);
        const scaledOrders = scaleOrdersToBudget(safeBudget, rawStrategy, {
            step: options.step ?? 1000,
            minimumOrder: options.minimumOrder ?? 1000
        });

        return {
            available_balance: availableBalance,
            reserve_percent: reservePercent,
            safe_budget: safeBudget,
            required_capital: requiredCapital,
            coverage_percent: coveragePercent,
            safe_coverage_percent: safeCoveragePercent,
            fully_covered: safeBudget + 1e-9 >= requiredCapital,
            base_order_affordable: safeBudget + 1e-9 >= normalized.baseOrder,
            affordable_safety_orders: affordableSafetyOrders,
            configured_safety_orders: normalized.maxSafetyOrders,
            scaled_orders: scaledOrders
        };
    }

    return {
        normalizeStrategy,
        calculateRequiredCapital,
        maximumAffordableSafetyOrders,
        scaleOrdersToBudget,
        buildCapitalPlan
    };
}));
