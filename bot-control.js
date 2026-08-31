/**
 * bot-control.js
 * Shared, ownership-scoped bot control (start / stop / reset) used by both the
 * HTTP API (api-endpoints.js) and the Telegram command service. Keeping this
 * in one place ensures every entry point applies the same fail-closed rules:
 * live gate check on start, tracked-order cancellation on stop, and no reset
 * while tracked exchange cancellations are unconfirmed.
 */
'use strict';

const { IndodaxClient } = require('./indodax-client');
const { requireLiveTrading } = require('./live-trading-policy');
const accounts = require('./accounts');

let db = null;
let initialized = false;
async function ensureInit() {
    if (!initialized) {
        const Database = require('./database');
        const encryptionKey = process.env.ENCRYPTION_KEY ||
            (require('fs').existsSync('.env') ?
                require('fs').readFileSync('.env', 'utf8').match(/ENCRYPTION_KEY=(.+)/)?.[1]?.trim() : null);
        db = new Database();
        db.setEncryptionKey(encryptionKey);
        db.init();
        initialized = true;
        await new Promise(resolve => setTimeout(resolve, 500));
    }
    return db;
}

async function ownedBot(userId, botId) {
    const bot = await db.getBot(botId);
    if (!bot) return null;
    const account = await accounts.getUserAccount(userId, bot.account_id);
    return account ? bot : null;
}

async function requireBotLiveTrading(bot) {
    const completedDryCycles = await db.getCompletedDryRunCycleCount(bot.id);
    const strategy = bot.strategy_id
        ? await db.getStrategy(bot.strategy_id) : null;
    return requireLiveTrading(bot.id, completedDryCycles, process.env, strategy);
}

async function getPositionRobust(botId) {
    return db.getPosition(botId);
}

async function cancelTrackedBotOrders(bot, position, client, ledgerOrders = null) {
    const tracked = new Map();
    for (const order of position?.open_orders || []) {
        const id = String(order.order_id || '');
        if (id) tracked.set(`exchange:${id}`, {
            exchange_order_id: id, side: 'buy', ledger_id: null
        });
    }
    for (const [id, side] of [
        [position?.tp_order_id, 'sell'],
        [position?.exit_order_id, 'sell']
    ]) {
        if (id) tracked.set(`exchange:${id}`, {
            exchange_order_id: String(id), side, ledger_id: null
        });
    }
    for (const order of ledgerOrders || await db.getOpenOrders(bot.id)) {
        const exchangeId = String(order.exchange_order_id || '');
        const clientId = String(order.client_order_id || '');
        const key = exchangeId ? `exchange:${exchangeId}` : `client:${clientId}`;
        if (!key.endsWith(':')) tracked.set(key, {
            exchange_order_id: exchangeId,
            client_order_id: clientId,
            side: order.side || 'buy',
            ledger_id: order.id
        });
    }

    const failures = [];
    for (const [key, order] of tracked.entries()) {
        if (bot.dry_run) {
            if (order.ledger_id) await db.updateOrderStatus(order.ledger_id, 'CANCELLED');
            continue;
        }
        if (!client) {
            failures.push(key);
            continue;
        }
        try {
            const result = order.exchange_order_id
                ? await client.cancel_order(bot.pair, order.exchange_order_id, order.side)
                : await client.cancel_order_by_client_id(
                    order.client_order_id, bot.pair);
            if (result?.error) {
                failures.push(key);
            } else if (order.ledger_id) {
                await db.updateOrderStatus(order.ledger_id, 'CANCELLED');
            }
        } catch (_error) {
            failures.push(key);
        }
    }
    if (failures.length) {
        const error = new Error(
            `${failures.length} pembatalan order belum terkonfirmasi; state lokal dipertahankan untuk recovery.`
        );
        error.code = 'ORDER_CANCELLATION_UNCONFIRMED';
        error.statusCode = 502;
        error.failedCount = failures.length;
        throw error;
    }
    return { tracked: tracked.size, cancelled: tracked.size };
}

async function cancellationClientForOwnedBot(userId, bot, hasTrackedOrders) {
    if (bot.dry_run || !hasTrackedOrders) return null;
    const account = await accounts.getUserAccount(userId, bot.account_id);
    if (!account?.api_key_encrypted || !account?.api_secret_encrypted) {
        const error = new Error(
            'Credential exchange tidak tersedia; pembatalan order belum dapat dikonfirmasi.'
        );
        error.code = 'ORDER_CANCELLATION_UNCONFIRMED';
        error.statusCode = 409;
        throw error;
    }
    const apiKey = db.decrypt(account.api_key_encrypted, account.id);
    const apiSecret = db.decrypt(account.api_secret_encrypted, account.id);
    if (!apiKey || !apiSecret) {
        const error = new Error(
            'Credential exchange tidak dapat didekripsi; state lokal dipertahankan untuk recovery.'
        );
        error.code = 'ORDER_CANCELLATION_UNCONFIRMED';
        error.statusCode = 409;
        throw error;
    }
    return new IndodaxClient(
        apiKey, apiSecret, account.api_version || 'v1');
}

async function recordCancellationFailure(bot, error) {
    try {
        await db.raiseAlert({
            account_id: bot.account_id,
            bot_id: bot.id,
            severity: 'CRITICAL',
            kind: 'ORDER_CANCELLATION_FAILED',
            dedupe_key: `order-cancel:${bot.id}`,
            message: 'Pembatalan order belum terkonfirmasi; bot dihentikan dan state lokal dipertahankan.',
            metadata: { failed_count: Number(error.failedCount || 0) }
        });
    } catch (alertError) {
        console.warn('[BOT-CONTROL] Alert persistence warning:', alertError.message);
    }
}

/**
 * Start a bot, scoped to the owner. Throws {statusCode, message, ...} on
 * failure, matching the HTTP semantics used by the dashboard.
 */
async function startBot(userId, botId) {
    await ensureInit();
    const bot = await ownedBot(userId, botId);
    if (!bot) {
        const error = new Error('Bot tidak ditemukan');
        error.statusCode = 404;
        throw error;
    }
    if (bot.status === 'RUNNING') {
        const error = new Error('Bot sudah berjalan.');
        error.statusCode = 409;
        throw error;
    }
    if (!bot.dry_run) {
        await requireBotLiveTrading(bot);
    }
    bot.status = 'RUNNING';
    await db.updateBot(bot);
    return bot;
}

/**
 * Stop a bot, scoped to the owner. Cancels tracked working orders (exchange +
 * ledger) but preserves the open position so it can resume with updated
 * settings on start. Fails closed if a tracked cancellation is unconfirmed.
 */
async function stopBot(userId, botId) {
    await ensureInit();
    const bot = await ownedBot(userId, botId);
    if (!bot) {
        const error = new Error('Bot tidak ditemukan');
        error.statusCode = 404;
        throw error;
    }
    if (bot.status === 'STOPPED') {
        const error = new Error('Bot sudah berhenti.');
        error.statusCode = 409;
        throw error;
    }
    bot.status = 'STOPPED';
    await db.updateBot(bot);

    const position = await getPositionRobust(bot.id);
    const ledgerOrders = await db.getOpenOrders(bot.id);
    const hasPositionOrders = Boolean(
        position?.open_orders?.length || position?.tp_order_id || position?.exit_order_id
    );
    try {
        const client = await cancellationClientForOwnedBot(
            userId, bot, hasPositionOrders || ledgerOrders.length > 0
        );
        await cancelTrackedBotOrders(bot, position, client, ledgerOrders);
        await db.resolveAlert(`order-cancel:${bot.id}`);
    } catch (cancelError) {
        console.warn(`[BOT-CONTROL] Exchange cancel warning: ${cancelError.message}`);
        await recordCancellationFailure(bot, cancelError);
        cancelError.botStopped = true;
        throw cancelError;
    }
    if (position) {
        position.open_orders = [];
        position.tp_order_id = null;
        position.exit_order_id = null;
        position.exit_reason = '';
        await db.savePosition(position);
    }
    return bot;
}

/**
 * Reset the current cycle position, scoped to the owner. Only allowed when the
 * bot is stopped and no tracked exchange cancellation remains unconfirmed.
 */
async function resetPosition(userId, botId) {
    await ensureInit();
    const bot = await ownedBot(userId, botId);
    if (!bot) {
        const error = new Error('Bot tidak ditemukan');
        error.statusCode = 404;
        throw error;
    }
    if (bot.status !== 'STOPPED') {
        const error = new Error('Hentikan bot terlebih dahulu sebelum mereset siklus.');
        error.statusCode = 409;
        throw error;
    }
    const position = await getPositionRobust(bot.id);
    const ledgerOrders = await db.getOpenOrders(bot.id);
    const hasPositionOrders = Boolean(
        position?.open_orders?.length || position?.tp_order_id || position?.exit_order_id
    );
    await db.addLog(
        bot.account_id, 'INFO', 'CYCLE_RESET_MANUAL',
        `Reset siklus diminta untuk ${bot.pair} oleh kontrol (web/Telegram).`,
        bot.id, { origin: 'control' }
    );
    try {
        const client = await cancellationClientForOwnedBot(
            userId, bot, hasPositionOrders || ledgerOrders.length > 0
        );
        await cancelTrackedBotOrders(bot, position, client, ledgerOrders);
        await db.resolveAlert(`order-cancel:${bot.id}`);
    } catch (cancelError) {
        console.warn(`[BOT-CONTROL] Reset cancel warning: ${cancelError.message}`);
        await recordCancellationFailure(bot, cancelError);
        throw cancelError;
    }
    await db.closePosition(bot.id, 'MANUALLY_RESET');
    await db.addLog(
        bot.account_id, 'INFO', 'CYCLE_RESET',
        `Siklus DCA dan order BO/SO untuk ${bot.pair} dibersihkan.`,
        bot.id, { origin: 'control' }
    );
    return bot;
}

module.exports = {
    ensureInit,
    ownedBot,
    startBot,
    stopBot,
    resetPosition,
    getPositionRobust
};
