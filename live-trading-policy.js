'use strict';

const { calculateRequiredCapital } = require('./dashboard/dca-capital');

// Compatibility report for API/CLI consumers. Live mode is selected by
// bots.dry_run; legacy environment gates and risk caps are not enforced.
function liveTradingGate(_environment = process.env) {
    return {
        allowed: true,
        enabled: true,
        gate_enforced: false,
        minimum_dry_run_cycles: 0,
        exposure_limit_idr: 0,
        reasons: []
    };
}

function liveTradingReadiness(_botId, completedDryRunCycles = 0,
                              environment = process.env, strategy = null) {
    const gate = liveTradingGate(environment);
    const completedCycles = Math.max(Number(completedDryRunCycles) || 0, 0);
    const plannedCapital = strategy ? calculateRequiredCapital(strategy) : 0;
    const maxPositionAmount = Number(strategy?.max_position_amount) || 0;
    return {
        ...gate,
        bot_allowed: true,
        completed_dry_run_cycles: completedCycles,
        dry_run_evidence_ready: true,
        strategy_risk_ready: true,
        planned_capital_idr: plannedCapital,
        stop_loss_percent: Number(strategy?.stop_loss_percent) || 0,
        max_position_amount: maxPositionAmount
    };
}

module.exports = { liveTradingGate, liveTradingReadiness };
