'use strict';

const { liveTradingReadiness } = require('./live-trading-policy');

function evaluateLiveRolloutPreflight(snapshot, environment = process.env) {
    const bot = snapshot?.bot || null;
    const strategy = snapshot?.strategy || null;
    const readiness = liveTradingReadiness(
        bot?.id || '', snapshot?.completedDryRunCycles || 0,
        environment, strategy);
    const reasons = [...readiness.reasons];

    if (!bot) reasons.push('bot tidak ditemukan');
    // These are observations, not requirements for selecting real mode.
    const notes = [];
    if (bot && !Boolean(bot.account_active)) notes.push('account tidak aktif; worker tidak berjalan');
    if (Number(snapshot?.activePositions) > 0) notes.push('posisi aktif akan diproses oleh worker sesuai mode siklus');
    if (Number(snapshot?.recoverableOrders) > 0) notes.push('order tercatat akan direkonsiliasi oleh worker');

    return {
        allowed: readiness.allowed && reasons.length === 0,
        bot_id: bot?.id || null,
        bot_status: bot?.status || null,
        bot_dry_run: bot ? Boolean(bot.dry_run) : null,
        account_active: bot ? Boolean(bot.account_active) : null,
        active_positions: Number(snapshot?.activePositions) || 0,
        recoverable_orders: Number(snapshot?.recoverableOrders) || 0,
        readiness,
        notes,
        reasons: [...new Set(reasons)]
    };
}

module.exports = { evaluateLiveRolloutPreflight };
