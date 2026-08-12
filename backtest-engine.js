/**
 * Deterministic candle-based DCA backtester.
 *
 * OHLC does not reveal the real tick order inside a candle. We use a common,
 * deterministic path assumption:
 * - bullish/doji candle: Open -> Low -> High -> Close
 * - bearish candle: Open -> High -> Low -> Close
 *
 * This avoids the old unconditional SO/SL-first bias and keeps every
 * backtest reproducible.
 */

function cumulativeDistance(level, distance, scale, scaleEnabled) {
    let total = 0;
    for (let index = 0; index < level; index += 1) {
        total += distance * (scaleEnabled ? Math.pow(scale, index) : 1);
    }
    return total;
}

function requiredCapitalForStrategy(strategy) {
    let required = strategy.base;
    for (let level = 1; level <= strategy.maxSo; level += 1) {
        required += strategy.martingale
            ? strategy.safety * Math.pow(strategy.volumeScale, level - 1)
            : strategy.safety;
    }
    return required;
}

function runBacktest(rawCandles, rawStrategy, options = {}) {
    const candles = (Array.isArray(rawCandles) ? rawCandles : [])
        .map(candle => ({
            timestamp: Number(candle.timestamp) || 0,
            open: Number(candle.open) || 0,
            high: Number(candle.high) || 0,
            low: Number(candle.low) || 0,
            close: Number(candle.close) || 0
        }))
        .filter(candle => candle.timestamp > 0 && candle.close > 0)
        .sort((a, b) => a.timestamp - b.timestamp);
    if (candles.length < 2) throw new Error('Minimal dua candle diperlukan untuk backtest.');

    const strategy = {
        base: Math.max(Number(rawStrategy.base_order_amount) || 0, 0),
        safety: Math.max(Number(rawStrategy.safety_order_amount) || 0, 0),
        maxSo: Math.max(Math.trunc(Number(rawStrategy.max_safety_orders) || 0), 0),
        deviation: Math.max(Number(rawStrategy.price_deviation) || 0.01, 0.01),
        deviationScale: Math.max(Number(rawStrategy.deviation_scale) || 1, 1),
        stepScale: !!rawStrategy.step_scale_enabled,
        volumeScale: Math.max(Number(rawStrategy.volume_scale) || 1, 1),
        martingale: !!rawStrategy.martingale_enabled,
        tp: Math.max(Number(rawStrategy.take_profit_percent) || 0, 0),
        sl: Math.max(Number(rawStrategy.stop_loss_percent) || 0, 0),
        marketBuyFee: Math.max(Number(rawStrategy.market_buy_fee_percent) || 0, 0) / 100,
        marketSellFee: Math.max(Number(rawStrategy.market_sell_fee_percent) || 0, 0) / 100,
        limitBuyFee: Math.max(Number(rawStrategy.limit_buy_fee_percent) || 0, 0) / 100,
        limitSellFee: Math.max(Number(rawStrategy.limit_sell_fee_percent) || 0, 0) / 100,
        rsiPeriod: Math.max(Math.trunc(Number(rawStrategy.rsi_period) || 14), 2),
        rsiOversold: Math.min(Math.max(Number(rawStrategy.rsi_oversold) || 60, 0), 100)
    };
    if (strategy.base <= 0) throw new Error('Base Order harus lebih besar dari nol.');

    const initialCapital = Number(options.initialCapital);
    if (!Number.isFinite(initialCapital) || initialCapital <= 0) {
        throw new Error('Modal awal harus berupa angka lebih besar dari nol.');
    }
    if (initialCapital < strategy.base) {
        throw new Error('Modal awal tidak boleh lebih kecil dari Base Order.');
    }
    const requiredCapital = requiredCapitalForStrategy(strategy);
    let cash = initialCapital;
    let position = null;
    let totalFees = 0;
    let peakEquity = initialCapital;
    let maxDrawdown = 0;
    let maxCapitalUsed = 0;
    const cycles = [];
    const equityCurve = [];
    let hasOpenedCycle = false;
    let ambiguousCandles = 0;
    const unfundedSafetyOrders = new Set();

    const rsiAt = index => {
        if (index < strategy.rsiPeriod) return null;
        let gains = 0;
        let losses = 0;
        const start = index - strategy.rsiPeriod + 1;
        for (let cursor = start; cursor <= index; cursor += 1) {
            const change = candles[cursor].close - candles[cursor - 1].close;
            if (change > 0) gains += change;
            else losses += Math.abs(change);
        }
        if (losses === 0) return 100;
        const rs = (gains / strategy.rsiPeriod) / (losses / strategy.rsiPeriod);
        return 100 - (100 / (1 + rs));
    };

    const openCycle = candle => {
        if (cash < strategy.base) return;
        const amount = (strategy.base / candle.close) * (1 - strategy.marketBuyFee);
        const fee = strategy.base * strategy.marketBuyFee;
        cash -= strategy.base;
        totalFees += fee;
        position = {
            id: `bt_${cycles.length + 1}`,
            started_at: candle.timestamp,
            base_price: candle.close,
            total_invested: strategy.base,
            total_amount: amount,
            total_fees: fee,
            safety_orders_filled: 0,
            filledSo: new Set()
        };
        hasOpenedCycle = true;
        maxCapitalUsed = Math.max(maxCapitalUsed, position.total_invested);
    };

    const closeCycle = (candle, price, reason, feeRate) => {
        const gross = position.total_amount * price;
        const fee = gross * feeRate;
        const net = gross - fee;
        const profit = net - position.total_invested;
        const profitPercent = position.total_invested > 0
            ? profit / position.total_invested * 100 : 0;
        cash += net;
        totalFees += fee;
        cycles.push({
            id: position.id,
            status: 'CLOSED',
            started_at: position.started_at,
            closed_at: candle.timestamp,
            base_price: position.base_price,
            average_entry_price: position.total_invested / position.total_amount,
            exit_price: price,
            total_invested: position.total_invested,
            total_amount: position.total_amount,
            total_fees: position.total_fees + fee,
            safety_orders_filled: position.safety_orders_filled,
            realized_profit: profit,
            realized_profit_percent: profitPercent,
            close_reason: reason
        });
        position = null;
    };

    const safetyOrderAt = level => {
        const distance = cumulativeDistance(
            level, strategy.deviation, strategy.deviationScale,
            strategy.stepScale);
        return {
            level,
            price: position.base_price * (1 - distance / 100),
            quote: strategy.martingale
                ? strategy.safety * Math.pow(strategy.volumeScale, level - 1)
                : strategy.safety
        };
    };

    const fillSafetyOrder = order => {
        const amount = (order.quote / order.price) * (1 - strategy.limitBuyFee);
        const fee = order.quote * strategy.limitBuyFee;
        cash -= order.quote;
        totalFees += fee;
        position.total_invested += order.quote;
        position.total_amount += amount;
        position.total_fees += fee;
        position.safety_orders_filled += 1;
        position.filledSo.add(order.level);
        maxCapitalUsed = Math.max(maxCapitalUsed, position.total_invested);
    };

    const processDescendingSegment = (fromPrice, toPrice, candle, blockedLevels) => {
        let cursor = fromPrice;
        while (position && cursor >= toPrice) {
            const average = position.total_invested / position.total_amount;
            const slPrice = strategy.sl > 0
                ? average * (1 - strategy.sl / 100) : 0;
            const candidates = [];

            if (slPrice > 0 && slPrice <= cursor && slPrice >= toPrice) {
                candidates.push({ type: 'SL', price: slPrice, level: 0 });
            }
            for (let level = 1; level <= strategy.maxSo; level += 1) {
                if (position.filledSo.has(level) || blockedLevels.has(level)) continue;
                const order = safetyOrderAt(level);
                if (order.price > 0 && order.price <= cursor && order.price >= toPrice) {
                    candidates.push({ type: 'SO', ...order });
                }
            }
            if (!candidates.length) break;

            // Descending price touches the highest level first. At an exactly
            // equal level, SL wins because the worker evaluates SL before it
            // synchronizes a newly filled SO.
            candidates.sort((a, b) =>
                (b.price - a.price) || (a.type === 'SL' ? -1 : 1));
            const event = candidates[0];
            if (event.type === 'SL') {
                closeCycle(candle, event.price, 'STOP_LOSS', strategy.marketSellFee);
                return;
            }
            if (cash + 1e-9 < event.quote) {
                blockedLevels.add(event.level);
                unfundedSafetyOrders.add(`${position.id}:${event.level}`);
            } else {
                fillSafetyOrder(event);
            }
            cursor = event.price;
        }
    };

    const processAscendingSegment = (fromPrice, toPrice, candle) => {
        if (!position || toPrice < fromPrice || strategy.tp <= 0) return;
        const average = position.total_invested / position.total_amount;
        const tpPrice = average * (1 + strategy.tp / 100) /
            Math.max(1 - strategy.limitSellFee, 0.000001);
        if (tpPrice >= fromPrice && tpPrice <= toPrice) {
            closeCycle(candle, tpPrice, 'TAKE_PROFIT', strategy.limitSellFee);
        }
    };

    const processCandlePath = (candle, previousClose) => {
        if (!position) return;
        const average = position.total_invested / position.total_amount;
        const tpPrice = strategy.tp > 0
            ? average * (1 + strategy.tp / 100) /
                Math.max(1 - strategy.limitSellFee, 0.000001)
            : 0;
        const slPrice = strategy.sl > 0
            ? average * (1 - strategy.sl / 100) : 0;
        const downwardTouched = (slPrice > 0 && candle.low <= slPrice) ||
            Array.from({ length: strategy.maxSo }, (_, index) => index + 1)
                .some(level => {
                    if (position.filledSo.has(level)) return false;
                    const order = safetyOrderAt(level);
                    return order.price > 0 && candle.low <= order.price;
                });
        if (tpPrice > 0 && candle.high >= tpPrice && downwardTouched) {
            ambiguousCandles += 1;
        }

        const intrabar = candle.close >= candle.open
            ? [candle.open, candle.low, candle.high, candle.close]
            : [candle.open, candle.high, candle.low, candle.close];
        const path = [previousClose, ...intrabar]
            .filter(price => Number.isFinite(price) && price > 0)
            .filter((price, index, values) => index === 0 || price !== values[index - 1]);
        const blockedLevels = new Set();
        for (let index = 1; index < path.length && position; index += 1) {
            const fromPrice = path[index - 1];
            const toPrice = path[index];
            if (toPrice < fromPrice) {
                processDescendingSegment(fromPrice, toPrice, candle, blockedLevels);
            } else if (toPrice > fromPrice) {
                processAscendingSegment(fromPrice, toPrice, candle);
            }
        }
    };

    candles.forEach((candle, candleIndex) => {
        if (!position) {
            // The first dashboard Start forces BO. Later cycles follow the
            // production worker's RSI oversold re-entry gate.
            const rsi = rsiAt(candleIndex);
            if (!hasOpenedCycle || (rsi !== null && rsi <= strategy.rsiOversold)) {
                openCycle(candle);
            }
        } else {
            processCandlePath(candle, candles[candleIndex - 1]?.close || candle.open);
        }

        const markedValue = position ? position.total_amount * candle.close : 0;
        const equity = cash + markedValue;
        peakEquity = Math.max(peakEquity, equity);
        const drawdown = peakEquity > 0 ? (peakEquity - equity) / peakEquity * 100 : 0;
        maxDrawdown = Math.max(maxDrawdown, drawdown);
        equityCurve.push({ timestamp: candle.timestamp, equity });

        // Do not open another cycle inside the same candle after a close.
    });

    const closedProfit = cycles.reduce((sum, cycle) => sum + cycle.realized_profit, 0);
    const wins = cycles.filter(cycle => cycle.realized_profit > 0);
    const losses = cycles.filter(cycle => cycle.realized_profit <= 0);
    const grossWins = wins.reduce((sum, cycle) => sum + cycle.realized_profit, 0);
    const grossLosses = Math.abs(losses.reduce((sum, cycle) => sum + cycle.realized_profit, 0));
    const finalMark = candles[candles.length - 1].close;
    const openValue = position ? position.total_amount * finalMark : 0;
    const unrealizedProfit = position ? openValue - position.total_invested : 0;
    const estimatedExitFee = position ? openValue * strategy.marketSellFee : 0;
    const liquidationUnrealizedProfit = position
        ? openValue - estimatedExitFee - position.total_invested : 0;
    const finalEquity = cash + openValue;
    const warnings = [];
    if (initialCapital + 1e-9 < requiredCapital) {
        warnings.push(
            `Modal hanya membiayai ${(initialCapital / requiredCapital * 100).toFixed(1)}% ` +
            `dari grid DCA (butuh Rp ${Math.round(requiredCapital).toLocaleString('id-ID')}).`);
    }
    if (unfundedSafetyOrders.size > 0) {
        warnings.push(
            `${unfundedSafetyOrders.size} safety order yang tersentuh tidak dapat diisi karena saldo tunai kurang.`);
    }
    if (position) {
        warnings.push(
            'Periode berakhir dengan posisi terbuka; Net Profit mencakup floating P/L dan fee beli posisi tersebut.');
    }
    if (cycles.length === 0) {
        warnings.push(
            'Belum ada siklus tertutup. Nilai minus belum merupakan rugi terealisasi.');
    }
    if (ambiguousCandles > 0) {
        warnings.push(
            `${ambiguousCandles} candle menyentuh level atas dan bawah; urutannya diestimasi dari lintasan OHLC.`);
    }

    return {
        summary: {
            initial_capital: initialCapital,
            final_equity: finalEquity,
            net_profit: finalEquity - initialCapital,
            realized_profit: closedProfit,
            unrealized_profit: unrealizedProfit,
            liquidation_unrealized_profit: liquidationUnrealizedProfit,
            estimated_exit_fee: estimatedExitFee,
            return_percent: (finalEquity - initialCapital) / initialCapital * 100,
            max_drawdown_percent: maxDrawdown,
            total_cycles: cycles.length,
            wins: wins.length,
            losses: losses.length,
            win_rate: cycles.length ? wins.length / cycles.length * 100 : 0,
            profit_factor: grossLosses > 0 ? grossWins / grossLosses : (grossWins > 0 ? null : 0),
            total_fees: totalFees,
            max_capital_used: maxCapitalUsed,
            required_capital: requiredCapital,
            capital_coverage_percent: Math.min(initialCapital / requiredCapital * 100, 100),
            unfunded_safety_orders: unfundedSafetyOrders.size,
            ambiguous_candles: ambiguousCandles,
            open_position: !!position
        },
        cycles,
        equity_curve: equityCurve,
        open_cycle: position ? {
            started_at: position.started_at,
            base_price: position.base_price,
            average_entry_price: position.total_invested / position.total_amount,
            total_invested: position.total_invested,
            total_amount: position.total_amount,
            safety_orders_filled: position.safety_orders_filled,
            unrealized_profit: unrealizedProfit,
            liquidation_unrealized_profit: liquidationUnrealizedProfit,
            estimated_exit_fee: estimatedExitFee
        } : null,
        warnings,
        assumptions: {
            intrabar_path: 'Bullish/doji: O-L-H-C; bearish: O-H-L-C',
            slippage_percent: 0,
            entry_rule: `Siklus pertama langsung; re-entry RSI(${strategy.rsiPeriod}) <= ${strategy.rsiOversold}`,
            candles: candles.length
        }
    };
}

module.exports = { runBacktest, cumulativeDistance, requiredCapitalForStrategy };
