'use strict';

const { calculateRequiredCapital } = require('./dashboard/dca-capital');

const REQUIRED_CONFIRMATION = 'I_ACCEPT_LIVE_TRADING_RISK';

function allowedBotIds(environment = process.env) {
    return new Set(String(environment.LIVE_TRADING_BOT_IDS || '')
        .split(',')
        .map(value => value.trim())
        .filter(Boolean));
}

function liveTradingGate(environment = process.env) {
    const enabled = String(environment.LIVE_TRADING_ENABLED || '').toLowerCase() === 'true';
    const confirmed = String(environment.LIVE_TRADING_CONFIRMATION || '') === REQUIRED_CONFIRMATION;
    const exposureLimit = Number(environment.MAX_ACCOUNT_EXPOSURE_IDR);
    const exposureConfigured = Number.isFinite(exposureLimit) && exposureLimit > 0;
    const botIds = allowedBotIds(environment);
    const configuredMinimum = Number(environment.LIVE_MIN_DRY_RUN_CYCLES ?? 3);
    const minimumDryRunCycles = Number.isInteger(configuredMinimum)
        && configuredMinimum >= 1 && configuredMinimum <= 100
        ? configuredMinimum
        : 3;
    const reasons = [];
    if (!enabled) reasons.push('LIVE_TRADING_ENABLED bukan true');
    if (!confirmed) reasons.push('konfirmasi risiko live belum valid');
    if (!exposureConfigured) reasons.push('MAX_ACCOUNT_EXPOSURE_IDR harus lebih dari 0');
    if (botIds.size === 0) reasons.push('LIVE_TRADING_BOT_IDS belum diisi');
    return {
        allowed: reasons.length === 0,
        enabled,
        confirmed,
        allowlist_configured: botIds.size > 0,
        allowed_bot_count: botIds.size,
        minimum_dry_run_cycles: minimumDryRunCycles,
        exposure_limit_idr: exposureConfigured ? exposureLimit : 0,
        reasons
    };
}

function liveTradingReadiness(botId, completedDryRunCycles = 0,
                              environment = process.env, strategy = null) {
    const gate = liveTradingGate(environment);
    const botAllowed = !!botId && allowedBotIds(environment).has(String(botId));
    const completedCycles = Math.max(Number(completedDryRunCycles) || 0, 0);
    const dryRunEvidenceReady = completedCycles >= gate.minimum_dry_run_cycles;
    const reasons = [...gate.reasons];
    if (!botAllowed) reasons.push('bot tidak termasuk LIVE_TRADING_BOT_IDS');
    if (!dryRunEvidenceReady) {
        reasons.push(`siklus dry-run selesai ${completedCycles}/${gate.minimum_dry_run_cycles}`);
    }
    const plannedCapital = strategy ? calculateRequiredCapital(strategy) : 0;
    const stopLossPercent = Number(strategy?.stop_loss_percent) || 0;
    const maxPositionAmount = Number(strategy?.max_position_amount) || 0;
    const strategyRiskReady = Boolean(strategy)
        && plannedCapital > 0
        && stopLossPercent > 0
        && maxPositionAmount >= plannedCapital
        && gate.exposure_limit_idr >= plannedCapital;
    if (!strategy) reasons.push('strategi bot tidak tersedia');
    else {
        if (!(stopLossPercent > 0)) reasons.push('stop-loss strategi harus lebih dari 0%');
        if (!(maxPositionAmount >= plannedCapital)) {
            reasons.push(`batas posisi Rp${maxPositionAmount} di bawah modal siklus Rp${plannedCapital}`);
        }
        if (!(gate.exposure_limit_idr >= plannedCapital)) {
            reasons.push(`exposure akun Rp${gate.exposure_limit_idr} di bawah modal siklus Rp${plannedCapital}`);
        }
    }
    return {
        ...gate,
        allowed: gate.allowed && botAllowed && dryRunEvidenceReady && strategyRiskReady,
        bot_allowed: botAllowed,
        completed_dry_run_cycles: completedCycles,
        dry_run_evidence_ready: dryRunEvidenceReady,
        strategy_risk_ready: strategyRiskReady,
        planned_capital_idr: plannedCapital,
        stop_loss_percent: stopLossPercent,
        max_position_amount: maxPositionAmount,
        reasons
    };
}

function requireLiveTrading(botId, completedDryRunCycles = 0,
                            environment = process.env, strategy = null) {
    const readiness = liveTradingReadiness(
        botId, completedDryRunCycles, environment, strategy);
    if (!readiness.allowed) {
        const error = new Error(`Live trading diblokir: ${readiness.reasons.join('; ')}`);
        error.code = 'LIVE_TRADING_BLOCKED';
        error.statusCode = 403;
        error.gate = readiness;
        throw error;
    }
    return readiness;
}

module.exports = {
    REQUIRED_CONFIRMATION,
    allowedBotIds,
    liveTradingGate,
    liveTradingReadiness,
    requireLiveTrading
};
