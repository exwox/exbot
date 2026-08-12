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
    if (bot && !Boolean(bot.dry_run)) reasons.push('bot sudah bukan dry-run');
    if (bot && String(bot.status).toUpperCase() !== 'STOPPED') {
        reasons.push('bot harus berstatus STOPPED');
    }
    if (bot && !Boolean(bot.account_active)) reasons.push('account bot tidak aktif');
    if (Number(snapshot?.activePositions) > 0) reasons.push('masih ada posisi aktif');
    if (Number(snapshot?.recoverableOrders) > 0) {
        reasons.push('masih ada order atau intent yang harus direkonsiliasi');
    }

    return {
        allowed: readiness.allowed && reasons.length === 0,
        bot_id: bot?.id || null,
        bot_status: bot?.status || null,
        bot_dry_run: bot ? Boolean(bot.dry_run) : null,
        account_active: bot ? Boolean(bot.account_active) : null,
        active_positions: Number(snapshot?.activePositions) || 0,
        recoverable_orders: Number(snapshot?.recoverableOrders) || 0,
        readiness,
        reasons: [...new Set(reasons)]
    };
}

module.exports = { evaluateLiveRolloutPreflight };
