/**
 * API Endpoints - Account Management & Bot Control
 */
const express = require('express');
const accounts = require('./accounts');
const auth = require('./auth');
const { IndodaxClient } = require('./indodax-client');
const crypto = require('crypto');
const { runBacktest } = require('./backtest-engine');
const { redactSensitive } = require('./log-redaction');
const {
    liveTradingGate,
    liveTradingReadiness,
    requireLiveTrading
} = require('./live-trading-policy');

const MIN_SAFETY_ORDER_DISTANCE = 0.01;

function parseSafetyOrderDistance(value, fallback = 1.2) {
    if (value === undefined || value === null || value === '') return fallback;
    const distance = Number(value);
    if (!Number.isFinite(distance) || distance < MIN_SAFETY_ORDER_DISTANCE) {
        const error = new Error(`Jarak SO minimum adalah ${MIN_SAFETY_ORDER_DISTANCE}%`);
        error.statusCode = 400;
        throw error;
    }
    return distance;
}

function sanitizeName(value, fallback = '') {
    const name = String(value || '').trim();
    if (!name || name.length > 100 || /[<>"'&\x00-\x1f]/.test(name)) {
        const error = new Error('Nama tidak valid atau mengandung karakter berbahaya');
        error.statusCode = 400;
        throw error;
    }
    return name || fallback;
}

const STRATEGY_FIELDS = new Set([
    'name', 'base_order_amount', 'safety_order_amount', 'max_safety_orders',
    'price_deviation', 'deviation_scale', 'step_scale_enabled', 'volume_scale',
    'take_profit_percent', 'stop_loss_percent', 'max_position_amount',
    'cooldown_seconds', 'martingale_enabled', 'rsi_period', 'rsi_oversold',
    'rsi_overbought', 'limit_buy_fee_percent', 'limit_sell_fee_percent',
    'market_buy_fee_percent', 'market_sell_fee_percent', 'initial_entry_mode', 'enabled'
]);

function boundedNumber(value, name, minimum, maximum, integer = false) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < minimum || number > maximum || (integer && !Number.isInteger(number))) {
        const error = new Error(`${name} harus berada di antara ${minimum} dan ${maximum}`);
        error.statusCode = 400;
        throw error;
    }
    return number;
}

function sanitizeStrategyUpdates(input) {
    const updates = {};
    for (const [field, value] of Object.entries(input || {})) {
        if (!STRATEGY_FIELDS.has(field)) continue;
        if (field === 'name') updates.name = sanitizeName(value, 'Default');
        else if (field === 'base_order_amount' || field === 'safety_order_amount') updates[field] = boundedNumber(value, field, 10000, 1_000_000_000);
        else if (field === 'max_safety_orders') updates[field] = boundedNumber(value, field, 0, 20, true);
        else if (field === 'price_deviation') updates[field] = boundedNumber(value, field, MIN_SAFETY_ORDER_DISTANCE, 50);
        else if (field === 'deviation_scale' || field === 'volume_scale') updates[field] = boundedNumber(value, field, 0.1, 10);
        else if (field === 'take_profit_percent') updates[field] = boundedNumber(value, field, 0.01, 100);
        else if (field === 'stop_loss_percent') updates[field] = boundedNumber(value, field, 0, 100);
        else if (field.endsWith('_fee_percent')) updates[field] = boundedNumber(value, field, 0, 10);
        else if (field === 'max_position_amount') updates[field] = boundedNumber(value, field, 0, 100_000_000_000);
        else if (field === 'cooldown_seconds') updates[field] = boundedNumber(value, field, 0, 604800, true);
        else if (field === 'rsi_period') updates[field] = boundedNumber(value, field, 2, 200, true);
        else if (field === 'rsi_oversold' || field === 'rsi_overbought') updates[field] = boundedNumber(value, field, 0, 100);
        else if (field === 'initial_entry_mode') {
            const mode = String(value).toUpperCase();
            if (!['MARKET', 'LIMIT', 'RSI', 'RSI_LIMIT'].includes(mode)) {
                const error = new Error('initial_entry_mode tidak valid');
                error.statusCode = 400;
                throw error;
            }
            updates[field] = mode;
        } else updates[field] = !!value;
    }
    return updates;
}

const router = express.Router();

const rateBuckets = new Map();
function rateLimit(name, limit, windowMs) {
    return (req, res, next) => {
        const key = `${name}:${req.ip || req.socket.remoteAddress || 'unknown'}`;
        const now = Date.now();
        const bucket = rateBuckets.get(key);
        if (!bucket || bucket.resetAt <= now) {
            rateBuckets.set(key, { count: 1, resetAt: now + windowMs });
            return next();
        }
        bucket.count += 1;
        if (bucket.count > limit) {
            res.setHeader('Retry-After', String(Math.ceil((bucket.resetAt - now) / 1000)));
            return res.status(429).json({ success: false, error: 'Terlalu banyak percobaan. Coba lagi nanti.' });
        }
        next();
    };
}

function sessionCookie(req, token) {
    const secure = req.secure || req.headers['x-forwarded-proto'] === 'https';
    return `xbot_session=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${7 * 24 * 60 * 60}${secure ? '; Secure' : ''}`;
}

// Initialize database
let db = null;
let dbInitialized = false;
async function ensureInit() {
    if (!dbInitialized) {
        const Database = require('./database');
        const encryptionKey = process.env.ENCRYPTION_KEY ||
            (require('fs').existsSync('.env') ?
                require('fs').readFileSync('.env', 'utf8').match(/ENCRYPTION_KEY=(.+)/)?.[1]?.trim() : null);
        db = new Database();
        db.setEncryptionKey(encryptionKey);
        db.init();
        dbInitialized = true;
        await new Promise(resolve => setTimeout(resolve, 500));
    }
}

// Every resource endpoint is authenticated.  `req.user` is populated by the
// session middleware in dashboard.js; keeping authorization here ensures a
// caller cannot select another user's account by guessing an ID.
router.use((req, res, next) => {
    if (req.path.startsWith('/auth/')) return next();
    if (!req.user) return res.status(401).json({ success: false, error: 'Authentication required' });
    next();
});

router.use((req, res, next) => {
    if (['GET', 'HEAD', 'OPTIONS'].includes(req.method)) return next();
    return rateLimit('mutation', 120, 60 * 1000)(req, res, next);
});

async function ownedAccount(userId, accountId) {
    return accounts.getUserAccount(userId, accountId);
}

async function ownedBot(userId, botId) {
    const bot = await db.getBot(botId);
    return bot && await ownedAccount(userId, bot.account_id) ? bot : null;
}

async function selectedBot(userId, botId) {
    if (botId) return ownedBot(userId, botId);
    const activeAccounts = await accounts.getUserActiveAccounts(userId);
    for (const account of activeAccounts) {
        const bots = await db.getAccountBots(account.id);
        if (bots.length) return bots[0];
    }
    return null;
}

async function ownedStrategy(userId, strategyId) {
    const strategy = await db.getStrategy(strategyId);
    if (!strategy) return null;
    const storedId = String(strategy.user_id);
    const requestedId = String(userId);
    const bothNumeric = /^\d+(?:\.0+)?$/.test(storedId) && /^\d+(?:\.0+)?$/.test(requestedId);
    return storedId === requestedId || (bothNumeric && Number(storedId) === Number(requestedId)) ? strategy : null;
}

function publicAccount(account) {
    return {
        id: account.id,
        name: account.name,
        exchange: account.exchange,
        is_active: !!account.is_active,
        last_connected_at: account.last_connected_at || null,
        last_error: account.last_error || null,
        created_at: account.created_at,
        updated_at: account.updated_at,
        api_key_masked: accounts.maskCredential(account.api_key_encrypted ? db.decrypt(account.api_key_encrypted) : ''),
        api_secret_masked: accounts.maskCredential(account.api_secret_encrypted ? db.decrypt(account.api_secret_encrypted) : ''),
    };
}

// ============================================================
// Account Management API
// ============================================================

// Get all accounts
router.get('/accounts', async (req, res) => {
    try {
        await ensureInit();

        const allAccounts = await accounts.getUserAccounts(req.user.id);

        res.json({
            success: true,
            data: allAccounts.map(publicAccount)
        });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Create account
router.post('/accounts', async (req, res) => {
    try {
        await ensureInit();
        const { name, api_key, api_secret, exchange } = req.body;

        if (!name || !api_key || !api_secret) {
            return res.json({ success: false, error: 'Name, API key, and secret are required' });
        }

        const account = await accounts.createAccount(req.user.id, sanitizeName(name), api_key, api_secret, exchange);
        res.json({
            success: true,
            data: {
                id: account.id,
                name: account.name,
                exchange: account.exchange,
                is_active: account.is_active
            },
            message: 'Account created successfully'
        });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Update account
router.put('/accounts/:id', async (req, res) => {
    try {
        await ensureInit();
        const { id } = req.params;
        const updates = { ...req.body };
        if (updates.name !== undefined) updates.name = sanitizeName(updates.name);

        const existing = await ownedAccount(req.user.id, id);
        if (!existing) return res.status(404).json({ success: false, error: 'Account not found' });
        const account = await accounts.updateAccount(id, updates);
        res.json({
            success: true,
            data: {
                id: account.id,
                name: account.name,
                exchange: account.exchange,
                is_active: account.is_active
            },
            message: 'Account updated successfully'
        });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Delete account
router.delete('/accounts/:id', async (req, res) => {
    try {
        await ensureInit();
        const { id } = req.params;
        if (!await ownedAccount(req.user.id, id)) return res.status(404).json({ success: false, error: 'Account not found' });
        await accounts.deleteAccount(id);
        res.json({ success: true, message: 'Account deleted successfully' });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Test account connection
router.post('/accounts/:id/test', async (req, res) => {
    try {
        await ensureInit();
        const { id } = req.params;
        if (!await ownedAccount(req.user.id, id)) return res.status(404).json({ success: false, error: 'Account not found' });
        const result = await accounts.testConnection(id);
        res.json(result);
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Read only the IDR funds needed by the DCA capital planner. Keep the raw
// exchange payload and all credentials out of the browser response.
router.get('/accounts/:id/balance', async (req, res) => {
    try {
        await ensureInit();
        const account = await ownedAccount(req.user.id, req.params.id);
        if (!account) {
            return res.status(404).json({ success: false, error: 'Account not found' });
        }
        const creds = await accounts.getDecryptedCredentials(account.id);
        if (!creds) {
            return res.status(400).json({
                success: false,
                error: 'Credential akun tidak tersedia'
            });
        }

        const result = await new IndodaxClient(
            creds.api_key, creds.api_secret).get_balance();
        if (!result || result.error) {
            return res.status(502).json({
                success: false,
                error: result?.error || 'Saldo Indodax tidak tersedia'
            });
        }
        const availableIdr = Math.max(Number(result.balance?.idr) || 0, 0);
        const heldIdr = Math.max(Number(result.balance_hold?.idr) || 0, 0);
        res.set('Cache-Control', 'no-store');
        res.json({
            success: true,
            data: {
                account_id: account.id,
                available_idr: availableIdr,
                held_idr: heldIdr,
                total_idr: availableIdr + heldIdr,
                fetched_at: new Date().toISOString()
            }
        });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

router.get('/accounts/:id/exposure', async (req, res) => {
    try {
        await ensureInit();
        const account = await ownedAccount(req.user.id, req.params.id);
        if (!account) {
            return res.status(404).json({ success: false, error: 'Account not found' });
        }
        const exposure = await db.getAccountExposure(account.id);
        const limit = Math.max(
            Number(process.env.MAX_ACCOUNT_EXPOSURE_IDR) || 0, 0);
        res.set('Cache-Control', 'no-store');
        res.json({
            success: true,
            data: {
                account_id: account.id,
                reserved_exposure_idr: exposure,
                limit_idr: limit,
                remaining_capacity_idr: limit > 0
                    ? Math.max(limit - exposure, 0)
                    : null,
                enforced: limit > 0,
                fetched_at: new Date().toISOString()
            }
        });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// ============================================================
// Bot Management API
// ============================================================

// Get all bots
router.get('/bots', async (req, res) => {
    try {
        await ensureInit();
        const userAccounts = await accounts.getUserAccounts(req.user.id);
        const allowedIds = new Set(userAccounts.map(account => account.id));
        const allBots = (await db.getAllBots()).filter(bot => allowedIds.has(bot.account_id));
        const botsWithStrategy = await Promise.all(allBots.map(async bot => ({
            ...bot,
            strategy: bot.strategy_id ? await db.getStrategy(bot.strategy_id) : null
        })));
        res.json({ success: true, data: botsWithStrategy });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

function sanitizePair(pair) {
    if (!pair) return 'btcidr';
    const clean = String(pair).trim().toLowerCase().replace(/[\/\-\s]/g, '');
    if (!/^[a-z0-9_]{5,24}$/.test(clean)) {
        const error = new Error('Trading pair tidak valid');
        error.statusCode = 400;
        throw error;
    }
    return clean;
}

function parseDryRun(value, fallback = true) {
    if (value === undefined) return fallback;
    if (typeof value !== 'boolean') {
        const error = new Error('dry_run harus berupa boolean');
        error.statusCode = 400;
        throw error;
    }
    return value;
}

async function requireBotLiveTrading(bot) {
    const completedDryCycles = await db.getCompletedDryRunCycleCount(bot.id);
    const strategy = bot.strategy_id
        ? await db.getStrategy(bot.strategy_id) : null;
    return requireLiveTrading(bot.id, completedDryCycles, process.env, strategy);
}

router.get('/live-readiness', async (req, res) => {
    await ensureInit();
    let readiness = liveTradingGate();
    if (req.query.bot_id) {
        const bot = await ownedBot(req.user.id, req.query.bot_id);
        if (!bot) {
            return res.status(404).json({ success: false, error: 'Bot not found' });
        }
        const completedDryCycles = await db.getCompletedDryRunCycleCount(bot.id);
        const strategy = bot.strategy_id
            ? await db.getStrategy(bot.strategy_id) : null;
        readiness = liveTradingReadiness(
            bot.id, completedDryCycles, process.env, strategy);
    }
    res.set('Cache-Control', 'no-store');
    res.json({ success: true, data: readiness });
});

// Create bot
router.post('/bots', async (req, res) => {
    try {
        await ensureInit();
        const { account_id, name, pair, dry_run } = req.body;

        if (!account_id || !name) {
            return res.json({ success: false, error: 'Account ID and name are required' });
        }
        if (!await ownedAccount(req.user.id, account_id)) {
            return res.status(404).json({ success: false, error: 'Account not found' });
        }
        const requestedDryRun = parseDryRun(dry_run, true);
        if (!requestedDryRun) {
            return res.status(409).json({
                success: false,
                error: 'Buat bot dalam dry-run, validasi, lalu tambahkan ID bot ke allowlist sebelum live'
            });
        }

        const botId = `bot_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
        const strategyId = `strat_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
        const strategy = {
            id: strategyId, user_id: req.user.id, name: `${sanitizeName(name)} DCA`,
            base_order_amount: 15000, safety_order_amount: 15000,
            max_safety_orders: 5, price_deviation: 1.2, deviation_scale: 1.5, step_scale_enabled: false,
            volume_scale: 1.5, take_profit_percent: 1, stop_loss_percent: 0,
            limit_buy_fee_percent: 0.15, limit_sell_fee_percent: 0.15,
            market_buy_fee_percent: 0.30, market_sell_fee_percent: 0.30,
            max_position_amount: 0, cooldown_seconds: 0, martingale_enabled: false,
            rsi_period: 14, rsi_oversold: 60, rsi_overbought: 70, enabled: true
        };
        const bot = {
            id: botId,
            account_id,
            name: sanitizeName(name),
            exchange: 'Indodax',
            pair: sanitizePair(pair),
            status: 'STOPPED',
            dry_run: requestedDryRun,
            strategy_id: strategyId
        };

        await db.addStrategy(strategy);
        await db.addBot(bot);
        res.json({ success: true, data: bot, message: 'Bot created successfully' });
    } catch (e) {
        res.status(e.statusCode || 500).json({ success: false, error: e.message });
    }
});

// Update bot
router.put('/bots/:id', async (req, res) => {
    try {
        await ensureInit();
        const { id } = req.params;
        const requested = req.body || {};

        const bot = await ownedBot(req.user.id, id);
        if (!bot) {
            return res.json({ success: false, error: 'Bot not found' });
        }

        if (requested.status === 'RUNNING') {
            const user = await db.getUser(req.user.id);
            if (!user || !user.is_active) {
                return res.status(403).json({ success: false, error: 'Akun Anda belum diaktifkan oleh admin.' });
            }
            if (user.expired_at) {
                const expTime = user.expired_at.includes('T') ? new Date(user.expired_at).getTime() : new Date(user.expired_at + 'T23:59:59').getTime();
                if (Date.now() > expTime) {
                    return res.status(403).json({ success: false, error: `Masa sewa bot Anda telah berakhir pada ${user.expired_at}. Silakan hubungi admin untuk perpanjangan.` });
                }
            }
            if (requested.dry_run === undefined && !bot.dry_run) {
                await requireBotLiveTrading(bot);
            }
        }

        const updates = {};
        if (requested.name !== undefined) updates.name = sanitizeName(requested.name);
        if (requested.dry_run !== undefined) {
            updates.dry_run = parseDryRun(requested.dry_run);
            if (updates.dry_run !== !!bot.dry_run && bot.status !== 'STOPPED') {
                return res.status(409).json({
                    success: false,
                    error: 'Hentikan bot sebelum mengubah mode dry-run/live'
                });
            }
            if (!updates.dry_run) await requireBotLiveTrading(bot);
        }
        if (requested.status !== undefined && ['RUNNING', 'STOPPED'].includes(requested.status)) updates.status = requested.status;
        if (requested.strategy_id !== undefined) {
            if (!await ownedStrategy(req.user.id, requested.strategy_id)) return res.status(404).json({ success: false, error: 'Strategy not found' });
            updates.strategy_id = requested.strategy_id;
        }
        if (requested.pair) {
            const cleanPair = sanitizePair(requested.pair);
            if (bot.pair !== cleanPair) {
                await db.closePosition(bot.id, 'PAIR_CHANGED');
            }
            updates.pair = cleanPair;
        }
        Object.assign(bot, updates);
        await db.updateBot(bot);
        res.json({ success: true, data: bot, message: 'Bot updated successfully' });
    } catch (e) {
        res.status(e.statusCode || 500).json({ success: false, error: e.message });
    }
});

// Delete bot
router.delete('/bots/:id', async (req, res) => {
    try {
        await ensureInit();
        const { id } = req.params;
        if (!await ownedBot(req.user.id, id)) return res.status(404).json({ success: false, error: 'Bot not found' });
        await db.deleteBot(id);
        res.json({ success: true, message: 'Bot deleted successfully' });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// ============================================================
// Strategy Management API
// ============================================================

// Get all strategies
router.get('/strategies', async (req, res) => {
    try {
        await ensureInit();
        const strategies = await db.getUserStrategies(req.user.id);
        res.json({ success: true, data: strategies });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Create strategy
router.post('/strategies', async (req, res) => {
    try {
        await ensureInit();
        const values = sanitizeStrategyUpdates(req.body);
        const strategy = {
            id: `strat_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`,
            user_id: req.user.id,
            name: 'Default', base_order_amount: 15000, safety_order_amount: 15000,
            max_safety_orders: 5, price_deviation: 1.2, deviation_scale: 1.5,
            step_scale_enabled: false, volume_scale: 1.5, take_profit_percent: 1.0,
            stop_loss_percent: 0, max_position_amount: 0, cooldown_seconds: 0,
            martingale_enabled: false, rsi_period: 14, rsi_oversold: 60,
            rsi_overbought: 70, limit_buy_fee_percent: 0.15,
            limit_sell_fee_percent: 0.15, market_buy_fee_percent: 0.30,
            market_sell_fee_percent: 0.30, initial_entry_mode: 'MARKET', enabled: true,
            ...values,
        };

        await db.addStrategy(strategy);
        res.json({ success: true, data: strategy, message: 'Strategy created successfully' });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Update strategy
router.put('/strategies/:id', async (req, res) => {
    try {
        await ensureInit();
        const { id } = req.params;
        const updates = sanitizeStrategyUpdates(req.body);

        const strategy = await ownedStrategy(req.user.id, id);
        if (!strategy) {
            return res.json({ success: false, error: 'Strategy not found' });
        }

        Object.assign(strategy, updates);
        await db.updateStrategy(strategy);
        res.json({ success: true, data: strategy, message: 'Strategy updated successfully' });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Delete strategy
router.delete('/strategies/:id', async (req, res) => {
    try {
        await ensureInit();
        const { id } = req.params;
        if (!await ownedStrategy(req.user.id, id)) return res.status(404).json({ success: false, error: 'Strategy not found' });
        await db.deleteStrategy(id);
        res.json({ success: true, message: 'Strategy deleted successfully' });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// ============================================================
// Settings API
// ============================================================

// Get settings
router.get('/settings', async (req, res) => {
    try {
        await ensureInit();
        const activeAccounts = await accounts.getUserActiveAccounts(req.user.id);
        const accountIds = new Set((await accounts.getUserAccounts(req.user.id)).map(account => account.id));
        const allBots = (await db.getAllBots()).filter(bot => accountIds.has(bot.account_id));
        const allStrategies = await db.getUserStrategies(req.user.id);
        res.json({
            success: true,
            data: {
                accounts: activeAccounts.map(publicAccount),
                bots: allBots,
                strategies: allStrategies
            }
        });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Update settings
router.post('/settings', async (req, res) => {
    try {
        await ensureInit();
        const { account_id, bot_id, strategy_id, settings } = req.body;

        if (!settings || typeof settings !== 'object') {
            return res.status(400).json({ success: false, error: 'Settings are required' });
        }
        if (Object.prototype.hasOwnProperty.call(settings, 'price_deviation')) {
            settings.price_deviation = parseSafetyOrderDistance(settings.price_deviation);
        }

        // Update account if provided
        if (account_id && settings.api_key) {
            if (!await ownedAccount(req.user.id, account_id)) return res.status(404).json({ success: false, error: 'Account not found' });
            await accounts.updateAccount(account_id, {
                api_key: settings.api_key,
                api_secret: settings.api_secret
            });
        }

        // Update bot if provided
        if (bot_id) {
            const bot = await ownedBot(req.user.id, bot_id);
            if (!bot) return res.status(404).json({ success: false, error: 'Bot not found' });
            const requestedDryRun = parseDryRun(settings.dry_run, !!bot.dry_run);
            if (requestedDryRun !== !!bot.dry_run && bot.status !== 'STOPPED') {
                return res.status(409).json({
                    success: false,
                    error: 'Hentikan bot sebelum mengubah mode dry-run/live'
                });
            }
            if (!requestedDryRun) await requireBotLiveTrading(bot);
            const newPair = settings.pair ? sanitizePair(settings.pair) : bot.pair;
            if (bot.pair !== newPair) {
                await db.closePosition(bot.id, 'PAIR_CHANGED');
            }
            Object.assign(bot, {
                pair: newPair,
                dry_run: requestedDryRun
            });
            await db.updateBot(bot);
        }

        // Update strategy if provided
        if (strategy_id) {
            const strategy = await ownedStrategy(req.user.id, strategy_id);
            if (!strategy) return res.status(404).json({ success: false, error: 'Strategy not found' });
            Object.assign(strategy, sanitizeStrategyUpdates(settings));
            await db.updateStrategy(strategy);
        }

        res.json({ success: true, message: 'Settings updated successfully' });
    } catch (e) {
        res.status(e.statusCode || 500).json({ success: false, error: e.message });
    }
});

// ============================================================
// Authentication API
// ============================================================

// Register
router.post('/auth/register', rateLimit('register', 5, 60 * 60 * 1000), async (req, res) => {
    try {
        await ensureInit();
        const { username, password, email } = req.body;

        if (!username || !password || !email) {
            return res.status(400).json({ success: false, error: 'Username, email, and password are required' });
        }

        if (password.length < 10) {
            return res.status(400).json({ success: false, error: 'Password minimal 10 karakter' });
        }

        const result = await auth.register(username, password, email);
        res.json(result);
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Login
router.post('/auth/login', rateLimit('login', 10, 15 * 60 * 1000), async (req, res) => {
    try {
        await ensureInit();
        const { username, password } = req.body;

        if (!username || !password) {
            return res.status(400).json({ success: false, error: 'Username and password are required' });
        }

        const result = await auth.login(username, password);
        if (result.success && result.session_token) {
            res.setHeader('Set-Cookie', sessionCookie(req, result.session_token));
            delete result.session_token;
        }
        res.json(result);
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Logout
router.post('/auth/logout', async (req, res) => {
    try {
        await auth.logout(req.sessionToken);
        res.setHeader('Set-Cookie', 'xbot_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0');
        res.json({ success: true });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Get current user
router.get(['/auth/me', '/user/me'], async (req, res) => {
    try {
        const user = req.user;

        if (!user) {
            return res.json({ success: false, error: 'Not authenticated' });
        }

        res.json({ success: true, user: user, data: user });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// ============================================================
// Legacy UI Support APIs
// ============================================================

// Get candlestick data
router.get('/candlestick', async (req, res) => {
    try {
        await ensureInit();
        const timeframe = req.query.timeframe || '1h';
        const limit = parseInt(req.query.limit) || 100;
        const bot = await selectedBot(req.user.id, req.query.bot_id);
        if (!bot) return res.status(404).json({ success: false, error: 'Bot tidak ditemukan' });

        const creds = await accounts.getDecryptedCredentials(bot.account_id);
        if (!creds) {
            return res.json({ success: false, error: 'Failed to decrypt credentials' });
        }

        const client = new IndodaxClient(creds.api_key, creds.api_secret);
        const pair = bot.pair || 'btcidr';

        const candles = await client.get_ohlc(pair, timeframe, limit);
        if (Array.isArray(candles)) {
            res.json({
                success: true,
                bot_id: bot.id,
                data: candles,
                pair: pair,
                timeframe: timeframe
            });
        } else {
            res.json({ success: false, error: candles.error || 'Failed to get candlestick data' });
        }
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Get trades
router.get('/trades', async (req, res) => {
    try {
        await ensureInit();
        const bot = await selectedBot(req.user.id, req.query.bot_id);
        if (!bot) return res.status(404).json({ success: false, error: 'Bot tidak ditemukan' });
        const ledger = await db.getBotTrades(bot.id, 200);
        const formatted = ledger.map(trade => ({
            id: trade.id,
            timestamp: trade.executed_at || trade.created_at,
            price: Number(trade.price) || 0,
            amount_idr: Number(trade.amount_quote) || 0,
            crypto_amount: Number(trade.amount) || 0,
            order_id: trade.exchange_trade_id || trade.order_id || '',
            type: trade.side,
            trade_type: trade.trade_type || trade.side,
            fee: Number(trade.fee) || 0,
            cost_basis: Number(trade.cost_basis) || 0,
            realized_profit: Number(trade.realized_profit) || 0,
            realized_profit_percent: Number(trade.realized_profit_percent) || 0,
            close_reason: trade.close_reason || '',
            dry_run: !!trade.dry_run
        }));

        res.json({ success: true, data: formatted });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Get open orders
router.get('/open-orders', async (req, res) => {
    try {
        await ensureInit();
        const bot = await selectedBot(req.user.id, req.query.bot_id);
        if (!bot) return res.status(404).json({ success: false, error: 'Bot tidak ditemukan' });
        if (bot?.dry_run) {
            const position = await db.getPosition(bot.id);
            // Pseudo orders are stored locally by the Python dry-run worker.
            // Do not call Indodax for these: they do not exist on the exchange.
            const localOrders = Array.isArray(position?.open_orders) ? position.open_orders : [];
            const simulatedOrders = localOrders.map(order => {
                const rawType = String(order.type || 'buy');
                const soMatch = rawType.match(/^so_(\d+)$/i);
                const soNum = order.so_number !== undefined ? order.so_number : (soMatch ? soMatch[1] : '');
                const tag = soNum !== '' ? `SO${soNum}` : '';
                const displayType = tag ? `BUY[${tag}] (Simulasi)` : 'BUY (Simulasi)';
                return {
                    order_id: String(order.order_id || ''),
                    type: 'buy',
                    display_type: displayType,
                    price: Number(order.price) || 0,
                    amount: Number(order.amount_crypto) || 0,
                    amount_remaining: Number(order.amount_crypto) || 0,
                    amount_idr: Number(order.amount_idr) ||
                        ((Number(order.price) || 0) * (Number(order.amount_crypto) || 0)),
                    time: position?.updated_at || '',
                    dry_run: true
                };
            });
            if (position?.tp_order_id && Number(position.take_profit_price) > 0) {
                simulatedOrders.push({
                    order_id: String(position.tp_order_id),
                    type: 'sell',
                    display_type: 'SELL[TP] (Simulasi)',
                    price: Number(position.take_profit_price),
                    amount: Number(position.total_amount) || 0,
                    amount_remaining: Number(position.total_amount) || 0,
                    amount_idr: Number(position.take_profit_price) * (Number(position.total_amount) || 0),
                    time: position.updated_at || '',
                    dry_run: true
                });
            }
            return res.json({ success: true, data: simulatedOrders, dry_run: true });
        }

        const creds = await accounts.getDecryptedCredentials(bot.account_id);
        if (!creds) {
            return res.json({ success: false, error: 'Failed to decrypt credentials' });
        }

        const client = new IndodaxClient(creds.api_key, creds.api_secret);
        let pair = bot.pair || 'btcidr';

        // Normalize pair format
        let apiPair = pair.toLowerCase();
        if (!apiPair.includes('_') && apiPair.includes('idr')) {
            const idx = apiPair.indexOf('idr');
            apiPair = apiPair.substring(0, idx) + '_' + apiPair.substring(idx);
        }

        const orders = await client.get_open_orders(apiPair);
        if (orders && orders.error) {
            return res.json({ success: false, error: orders.error });
        }

        const position = await db.getPosition(bot.id);
        const localOrders = Array.isArray(position?.open_orders) ? position.open_orders : [];
        const tpOrderId = String(position?.tp_order_id || '');

        const formatted = [];
        if (Array.isArray(orders)) {
            // Sort buy orders by price descending to establish rank (SO1 = highest price, SO2 = 2nd highest, etc)
            const buyOrdersByPrice = orders
                .filter(o => String(o.type || '').toLowerCase() === 'buy')
                .sort((a, b) => parseFloat(b.price || 0) - parseFloat(a.price || 0));

            for (const o of orders) {
                try {
                    const submit = o.submit_time || o.time || '';
                    const numericTime = Number(submit);
                    const orderDate = Number.isFinite(numericTime) && numericTime > 0
                        ? new Date(numericTime < 100000000000 ? numericTime * 1000 : numericTime)
                        : new Date(submit);
                    const timeStr = submit && !Number.isNaN(orderDate.getTime())
                        ? orderDate.toLocaleString('id-ID')
                        : '-';
                    const price = parseFloat(o.price || 0);
                    const cryptoAmount = parseFloat(o.amount || 0);
                    const cryptoRemaining = parseFloat(o.amount_remaining || 0);
                    const orderId = String(o.order_id || '');
                    const rawType = String(o.type || 'buy').toLowerCase();

                    let displayType = rawType.toUpperCase();
                    if (rawType === 'sell' || (tpOrderId && orderId === tpOrderId)) {
                        displayType = 'SELL[TP]';
                    } else if (rawType === 'buy') {
                        // 1. Try matching by exact order_id in SQLite open_orders
                        const matchedLoc = localOrders.find(l => String(l.order_id) === orderId);
                        if (matchedLoc && matchedLoc.so_number !== undefined) {
                            displayType = `BUY[SO${matchedLoc.so_number}]`;
                        } else {
                            // 2. Try matching by price in SQLite open_orders
                            const matchedPrice = localOrders.find(l => Math.abs(Number(l.price) - price) < 1.0);
                            if (matchedPrice && matchedPrice.so_number !== undefined) {
                                displayType = `BUY[SO${matchedPrice.so_number}]`;
                            } else {
                                // 3. Fallback: match by price rank among active buy orders
                                const rankIndex = buyOrdersByPrice.findIndex(b => String(b.order_id) === orderId);
                                if (rankIndex !== -1) {
                                    displayType = `BUY[SO${rankIndex + 1}]`;
                                } else {
                                    displayType = 'BUY';
                                }
                            }
                        }
                    }

                    formatted.push({
                        order_id: orderId,
                        type: rawType,
                        display_type: displayType,
                        price,
                        amount: cryptoAmount,
                        amount_remaining: cryptoRemaining,
                        amount_idr: parseFloat(o.amount_idr || 0) || (price * cryptoAmount),
                        amount_remaining_idr: parseFloat(o.amount_remaining_idr || 0) || (price * cryptoRemaining),
                        time: timeStr
                    });
                } catch (e) {
                    continue;
                }
            }
        }

        res.json({ success: true, data: formatted });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Get exchange trades
router.get('/exchange-trades', async (req, res) => {
    try {
        await ensureInit();
        const bot = await selectedBot(req.user.id, req.query.bot_id);
        if (!bot) return res.status(404).json({ success: false, error: 'Bot tidak ditemukan' });

        // This panel is intentionally a direct Indodax history feed. It must
        // remain available even when the selected bot is dry-run or when no
        // local orders/trades have been persisted yet.
        const creds = await accounts.getDecryptedCredentials(bot.account_id);
        if (!creds) {
            return res.json({ success: false, error: 'Failed to decrypt credentials' });
        }

        const client = new IndodaxClient(creds.api_key, creds.api_secret);
        const pair = bot.pair || 'btcidr';

        // Trade API v1 (/tapi) is deprecated by Indodax and now returns 404.
        // Use the current v2 feed so history is fetched directly from the
        // exchange and is independent of local bot persistence.
        const trades = await client.get_trade_history_v2(pair, 50);
        if (trades && trades.error) {
            return res.json({ success: false, error: trades.error });
        }

        const formatted = [];
        if (Array.isArray(trades)) {
            for (const trade of trades) {
                try {
                    const price = parseFloat(trade.price || 0);
                    const cryptoAmount = parseFloat(trade.qty || 0);
                    const quoteAmount = parseFloat(trade.quoteQty || (price * cryptoAmount));
                    const tradeTime = parseInt(trade.time || 0);
                    const formattedTime = tradeTime ? new Date(tradeTime).toLocaleString('id-ID') : '';

                    formatted.push({
                        order_id: trade.orderId || trade.tradeId || '',
                        type: trade.isBuyer ? 'buy' : 'sell',
                        price: price,
                        amount: quoteAmount,
                        amount_crypto: cryptoAmount,
                        time: formattedTime,
                        submit_time: formattedTime,
                        status: 'filled',
                        fee: parseFloat(trade.commission || 0),
                        fee_currency: trade.commissionAsset || 'IDR',
                        maker: !!trade.isMaker
                    });
                } catch (e) {
                    continue;
                }
            }
        }

        res.json({ success: true, data: formatted, source: 'indodax' });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

router.get('/cycles', async (req, res) => {
    try {
        await ensureInit();
        const bot = await selectedBot(req.user.id, req.query.bot_id);
        if (!bot) return res.status(404).json({ success: false, error: 'Bot tidak ditemukan' });
        const limit = Math.min(Math.max(Number(req.query.limit) || 100, 1), 500);
        const [cycles, stats] = await Promise.all([
            db.getBotCycles(bot.id, limit),
            db.getBotCycleStats(bot.id)
        ]);
        res.json({ success: true, bot_id: bot.id, data: cycles, stats });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

router.post('/backtest', async (req, res) => {
    try {
        await ensureInit();
        const bot = await ownedBot(req.user.id, req.body?.bot_id);
        if (!bot) return res.status(404).json({ success: false, error: 'Bot tidak ditemukan' });
        const strategy = bot.strategy_id ? await ownedStrategy(req.user.id, bot.strategy_id) : null;
        if (!strategy) return res.status(404).json({ success: false, error: 'Strategi bot tidak ditemukan' });

        const timeframe = String(req.body?.timeframe || '1h');
        const allowedTimeframes = new Set(['1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d']);
        if (!allowedTimeframes.has(timeframe)) {
            return res.status(400).json({ success: false, error: 'Timeframe tidak valid' });
        }
        const limit = Math.min(Math.max(Math.trunc(Number(req.body?.limit) || 500), 50), 2000);
        const initialCapital = Number(req.body?.initial_capital);
        if (!Number.isFinite(initialCapital) || initialCapital < 1000) {
            return res.status(400).json({
                success: false,
                error: 'Modal awal minimal Rp 1.000.'
            });
        }
        if (initialCapital < Number(strategy.base_order_amount || 0)) {
            return res.status(400).json({
                success: false,
                error: `Modal awal harus minimal sebesar Base Order: Rp ${Number(strategy.base_order_amount).toLocaleString('id-ID')}`
            });
        }
        const creds = await accounts.getDecryptedCredentials(bot.account_id);
        if (!creds) return res.status(400).json({ success: false, error: 'Credential akun tidak tersedia' });

        const client = new IndodaxClient(creds.api_key, creds.api_secret);
        const candles = await client.get_ohlc(bot.pair || 'btcidr', timeframe, limit);
        if (!Array.isArray(candles)) {
            return res.status(502).json({
                success: false,
                error: candles?.error || 'Data candle tidak tersedia'
            });
        }
        const result = runBacktest(candles, strategy, { initialCapital });
        res.json({
            success: true,
            bot: { id: bot.id, name: bot.name, pair: bot.pair },
            strategy,
            timeframe,
            requested_candles: limit,
            ...result
        });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Destructive reset for the authenticated user's bot data only. Login users
// and other users' resources are deliberately preserved.
router.post('/settings/reset-all', async (req, res) => {
    try {
        await ensureInit();
        if (req.body?.confirmation !== 'RESET ALL') {
            return res.status(400).json({
                success: false,
                error: 'Ketik RESET ALL untuk mengonfirmasi penghapusan.'
            });
        }
        const deleted = await db.resetUserData(req.user.id);
        res.json({
            success: true,
            data: deleted,
            message: 'Seluruh data bot milik Anda berhasil dihapus.'
        });
    } catch (e) {
        const status = e.code === 'BOTS_NOT_SAFE_TO_RESET' ? 409 : 500;
        res.status(status).json({ success: false, error: e.message });
    }
});

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
                : await client.cancel_order_by_client_id(order.client_order_id);
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
    const account = await ownedAccount(userId, bot.account_id);
    if (!account?.api_key_encrypted || !account?.api_secret_encrypted) {
        const error = new Error(
            'Credential exchange tidak tersedia; pembatalan order belum dapat dikonfirmasi.'
        );
        error.code = 'ORDER_CANCELLATION_UNCONFIRMED';
        error.statusCode = 409;
        throw error;
    }
    const apiKey = db.decrypt(account.api_key_encrypted);
    const apiSecret = db.decrypt(account.api_secret_encrypted);
    if (!apiKey || !apiSecret) {
        const error = new Error(
            'Credential exchange tidak dapat didekripsi; state lokal dipertahankan untuk recovery.'
        );
        error.code = 'ORDER_CANCELLATION_UNCONFIRMED';
        error.statusCode = 409;
        throw error;
    }
    return new IndodaxClient(apiKey, apiSecret);
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
        console.warn(`[STOP-BOT] Alert persistence warning: ${redactSensitive(alertError.message)}`);
    }
}

async function setBotRunState(req, res, status) {
    try {
        await ensureInit();
        const bot = await ownedBot(req.user.id, req.params.id);
        if (!bot) return res.status(404).json({ success: false, error: 'Bot not found' });
        if (status === 'RUNNING' && !bot.dry_run) {
            await requireBotLiveTrading(bot);
        }
        bot.status = status;
        await db.updateBot(bot);

        // When stopping a bot, cancel working open orders (on exchange & local state),
        // but preserve the active position in DB so it can resume with updated strategy on start.
        if (status === 'STOPPED') {
            const position = await db.getPosition(bot.id);
            const ledgerOrders = await db.getOpenOrders(bot.id);
            const hasPositionOrders = Boolean(
                position?.open_orders?.length || position?.tp_order_id || position?.exit_order_id
            );
            try {
                const client = await cancellationClientForOwnedBot(
                    req.user.id, bot, hasPositionOrders || ledgerOrders.length > 0
                );
                await cancelTrackedBotOrders(bot, position, client, ledgerOrders);
                await db.resolveAlert(`order-cancel:${bot.id}`);
            } catch (cancelError) {
                console.warn(`[STOP-BOT] Exchange cancel warning: ${redactSensitive(cancelError.message)}`);
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
        }

        res.json({
            success: true,
            data: bot,
            message: status === 'RUNNING'
                ? 'Bot started. Menggunakan settingan & perhitungan TP terbaru.'
                : 'Bot stopped. Seluruh open order dibatalkan & posisi aktif disimpan.'
        });
    } catch (e) {
        res.status(e.statusCode || 500).json({
            success: false,
            error: e.message,
            ...(e.botStopped ? {
                bot_stopped: true,
                order_cancellation_confirmed: false
            } : {})
        });
    }
}

// Python BotManager polls this status and owns the actual worker lifecycle.
router.post('/bots/:id/start', (req, res) => setBotRunState(req, res, 'RUNNING'));
router.post('/bots/:id/stop', (req, res) => setBotRunState(req, res, 'STOPPED'));

// Manual reset/clearing for stuck DCA cycle position and open BO/SO
router.post('/bots/:id/reset-position', async (req, res) => {
    try {
        await ensureInit();
        const bot = await ownedBot(req.user.id, req.params.id);
        if (!bot) return res.status(404).json({ success: false, error: 'Bot tidak ditemukan' });
        if (bot.status !== 'STOPPED') {
            return res.status(409).json({
                success: false,
                error: 'Hentikan bot terlebih dahulu sebelum mereset siklus.'
            });
        }

        // Fail closed: never archive the position/ledger while a tracked
        // exchange cancellation remains unconfirmed.
        const position = await db.getPosition(bot.id);
        const ledgerOrders = await db.getOpenOrders(bot.id);
        const hasPositionOrders = Boolean(
            position?.open_orders?.length || position?.tp_order_id || position?.exit_order_id
        );
        try {
            const client = await cancellationClientForOwnedBot(
                req.user.id, bot, hasPositionOrders || ledgerOrders.length > 0
            );
            await cancelTrackedBotOrders(bot, position, client, ledgerOrders);
            await db.resolveAlert(`order-cancel:${bot.id}`);
        } catch (cancelError) {
            console.warn(`[RESET-POS] Exchange cancel attempt failed: ${redactSensitive(cancelError.message)}`);
            await recordCancellationFailure(bot, cancelError);
            throw cancelError;
        }

        // Archive/close active position, open DCA cycles, and pending orders in database
        await db.closePosition(bot.id, 'MANUALLY_RESET');

        // Add log entry
        await db.addLog(
            bot.account_id,
            'INFO',
            'CYCLE_RESET',
            `Siklus DCA (open) dan order BO/SO untuk ${bot.pair} dibersihkan secara manual dari dashboard.`,
            bot.id,
            { request_id: req.requestId }
        );

        res.json({
            success: true,
            message: `Siklus DCA dan order BO/SO untuk ${bot.pair.toUpperCase()} berhasil dibersihkan.`
        });
    } catch (e) {
        res.status(e.statusCode || 500).json({ success: false, error: e.message });
    }
});

// ============================================================
// Admin Panel API
// ============================================================

function requireAdmin(req, res, next) {
    if (!req.user || !req.user.is_admin) {
        return res.status(403).json({ success: false, error: 'Akses ditolak. Fitur ini hanya untuk admin.' });
    }
    next();
}

function getPythonLogFilePath() {
    const fs = require('fs');
    const path = require('path');
    const primary = path.join(__dirname, 'logs', 'dca_bot.log');
    if (fs.existsSync(primary)) return primary;
    const fallback = path.join(__dirname, 'dca_bot.log');
    if (fs.existsSync(fallback)) return fallback;
    return primary;
}

// Get user bot logs
router.get('/logs', async (req, res) => {
    try {
        await ensureInit();
        const userAccounts = await accounts.getUserAccounts(req.user.id);
        const accountIds = userAccounts.map(a => a.id);
        if (accountIds.length === 0) {
            return res.json({ success: true, data: [] });
        }

        const limit = parseInt(req.query.limit) || 100;
        const level = req.query.level || null;

        const allLogs = [];
        for (const accountId of accountIds) {
            const accountLogs = await db.getLogs(accountId, null, level, limit);
            allLogs.push(...accountLogs);
        }

        allLogs.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        const sliced = allLogs.slice(0, limit);

        res.json({ success: true, data: sliced });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Persistent operational alerts are scoped through account ownership. Admins
// additionally see process-level alerts whose account_id is NULL.
router.get('/alerts', async (req, res) => {
    try {
        await ensureInit();
        const alerts = await db.getUserAlerts(
            req.user.id,
            req.query.status || 'OPEN',
            req.query.limit || 100,
            !!req.user.is_admin
        );
        res.json({ success: true, data: alerts });
    } catch (e) {
        res.status(500).json({ success: false, error: 'Gagal membaca alert' });
    }
});

router.post('/alerts/:id/acknowledge', async (req, res) => {
    try {
        await ensureInit();
        if (!/^\d+$/.test(String(req.params.id))) {
            return res.status(400).json({ success: false, error: 'ID alert tidak valid' });
        }
        const changed = await db.acknowledgeAlert(
            Number(req.params.id), req.user.id, !!req.user.is_admin);
        if (!changed) {
            return res.status(404).json({
                success: false,
                error: 'Alert terbuka tidak ditemukan'
            });
        }
        await db.addSystemLog(
            'INFO',
            'alerts',
            `Alert ${req.params.id} acknowledged by user ${req.user.id}; request_id=${req.requestId}`
        );
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ success: false, error: 'Gagal mengakui alert' });
    }
});

// Get live Python log lines (Admin only)
router.get('/admin/python-logs', requireAdmin, async (req, res) => {
    try {
        const fs = require('fs');
        const logPath = getPythonLogFilePath();

        if (!fs.existsSync(logPath)) {
            return res.json({
                success: true,
                data: {
                    lines: ['[SYSTEM] Belum ada file log dca_bot.log yang ditemukan. Pastikan python app.py --no-dashboard sedang berjalan.'],
                    total_lines: 0,
                    file_size_bytes: 0,
                    file_path: logPath,
                    updated_at: new Date().toISOString()
                }
            });
        }

        const stats = fs.statSync(logPath);
        const limit = Math.min(Math.max(parseInt(req.query.limit) || 200, 10), 2000);
        const levelFilter = (req.query.level || '').toUpperCase().trim();
        const searchQuery = (req.query.search || '').toLowerCase().trim();

        // Read file content (up to last 2MB for efficiency)
        const maxReadBytes = 2 * 1024 * 1024;
        const readSize = Math.min(stats.size, maxReadBytes);
        const buffer = Buffer.alloc(readSize);
        const fd = fs.openSync(logPath, 'r');
        fs.readSync(fd, buffer, 0, readSize, Math.max(0, stats.size - readSize));
        fs.closeSync(fd);

        let content = buffer.toString('utf8');
        // If truncated, drop potential partial first line
        if (stats.size > maxReadBytes) {
            const firstNewline = content.indexOf('\n');
            if (firstNewline !== -1) content = content.substring(firstNewline + 1);
        }

        let allLines = content.split(/\r?\n/).filter(line => line.trim().length > 0);

        // Apply level filter
        if (levelFilter && levelFilter !== 'ALL') {
            allLines = allLines.filter(line => {
                const upper = line.toUpperCase();
                return upper.includes(`- ${levelFilter} -`) || upper.includes(`[${levelFilter}]`);
            });
        }

        // Apply text search query
        if (searchQuery) {
            allLines = allLines.filter(line => line.toLowerCase().includes(searchQuery));
        }

        const slicedLines = allLines.slice(-limit);

        res.json({
            success: true,
            data: {
                lines: slicedLines,
                total_lines: allLines.length,
                file_size_bytes: stats.size,
                file_path: logPath,
                updated_at: new Date().toISOString()
            }
        });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Clear Python log file (Admin only)
router.post('/admin/python-logs/clear', requireAdmin, async (req, res) => {
    try {
        const fs = require('fs');
        const logPath = getPythonLogFilePath();
        if (fs.existsSync(logPath)) {
            fs.writeFileSync(logPath, `[${new Date().toISOString()}] - Main - INFO - Python Log file cleared by admin\n`, 'utf8');
        }
        res.json({ success: true, message: 'File log Python berhasil dibersihkan.' });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// System Status Overview (Admin only)
router.get('/admin/status', requireAdmin, async (req, res) => {
    try {
        await ensureInit();
        const fs = require('fs');
        const allBots = await db.getAllBots();
        const allAccounts = await db.getAllAccounts();
        const logPath = getPythonLogFilePath();
        let logExists = false;
        let logSizeBytes = 0;
        let logMtime = null;

        if (fs.existsSync(logPath)) {
            const stats = fs.statSync(logPath);
            logExists = true;
            logSizeBytes = stats.size;
            logMtime = stats.mtime.toISOString();
        }

        const runningBots = allBots.filter(b => b.status === 'RUNNING').length;
        const stoppedBots = allBots.filter(b => b.status !== 'RUNNING').length;

        res.json({
            success: true,
            data: {
                total_accounts: allAccounts.length,
                total_bots: allBots.length,
                running_bots: runningBots,
                stopped_bots: stoppedBots,
                python_log: {
                    exists: logExists,
                    size_bytes: logSizeBytes,
                    path: logPath,
                    last_modified: logMtime
                }
            }
        });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// ============================================================
// User Management API (Admin only)
// ============================================================

// Get all users (including their owned bots and running statuses)
router.get('/admin/users', requireAdmin, async (req, res) => {
    try {
        await ensureInit();
        const usersList = await db.getAllUsers();
        const allAccounts = await db.getAllAccounts();
        const allBots = await db.getAllBots();

        // Map account_id -> user_id
        const accountUserMap = {};
        allAccounts.forEach(acc => {
            accountUserMap[acc.id] = acc.user_id;
        });

        // Group bots by user_id
        const userBotsMap = {};
        allBots.forEach(bot => {
            const userId = accountUserMap[bot.account_id];
            if (userId) {
                if (!userBotsMap[userId]) {
                    userBotsMap[userId] = [];
                }
                userBotsMap[userId].push({
                    id: bot.id,
                    name: bot.name,
                    pair: String(bot.pair || '').toUpperCase(),
                    status: bot.status || 'STOPPED',
                    dry_run: !!bot.dry_run
                });
            }
        });

        const enrichedUsers = usersList.map(user => ({
            ...user,
            bots: userBotsMap[user.id] || []
        }));

        res.json({ success: true, data: enrichedUsers });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Create new user
router.post('/admin/users', requireAdmin, async (req, res) => {
    try {
        await ensureInit();
        const { username, password, email, is_active, expired_at } = req.body;

        const cleanUsername = String(username || '').trim();
        const cleanEmail = String(email || '').trim().toLowerCase();

        if (!/^[A-Za-z0-9_.-]{3,64}$/.test(cleanUsername)) {
            return res.status(400).json({ success: false, error: 'Username minimal 3-64 karakter (huruf, angka, dot, dash, underscore)' });
        }
        if (typeof password !== 'string' || password.length < 10) {
            return res.status(400).json({ success: false, error: 'Password minimal 10 karakter' });
        }

        const existing = await db.getUserByUsername(cleanUsername);
        if (existing) {
            return res.status(400).json({ success: false, error: 'Username sudah digunakan' });
        }

        const { salt, password_hash } = auth.hashPassword(password);
        const userId = Date.now();
        const now = new Date().toISOString();

        await db.addUser({
            id: userId,
            username: cleanUsername,
            email: cleanEmail,
            password_hash,
            salt,
            is_active: is_active !== undefined ? !!is_active : false,
            is_admin: false,
            expired_at: expired_at ? String(expired_at).trim() : null,
            created_at: now,
            updated_at: now
        });

        res.json({ success: true, message: `User '${cleanUsername}' berhasil ditambahkan`, user_id: userId });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Update user (email, is_active, password)
router.put('/admin/users/:id', requireAdmin, async (req, res) => {
    try {
        await ensureInit();
        const { id } = req.params;
        const { email, password, is_active, expired_at } = req.body;

        const existingUser = await db.getUser(id);
        if (!existingUser) {
            return res.status(404).json({ success: false, error: 'User tidak ditemukan' });
        }

        if (email !== undefined) {
            existingUser.email = String(email).trim().toLowerCase();
        }

        if (expired_at !== undefined) {
            existingUser.expired_at = expired_at ? String(expired_at).trim() : null;
        }

        if (is_active !== undefined) {
            if (existingUser.is_admin && !is_active) {
                return res.status(400).json({ success: false, error: 'Akun administrator tidak dapat dinonaktifkan' });
            }
            existingUser.is_active = !!is_active;
        }

        if (password && typeof password === 'string' && password.length >= 10) {
            Object.assign(existingUser, auth.hashPassword(password));
        } else if (password && password.length < 10) {
            return res.status(400).json({ success: false, error: 'Password baru minimal 10 karakter' });
        }

        await db.updateUser(existingUser);
        if (password || is_active === false || expired_at !== undefined) {
            await auth.revokeUserSessions(existingUser.id);
        }
        res.json({ success: true, message: `User '${existingUser.username}' berhasil diperbarui` });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Delete user
router.delete('/admin/users/:id', requireAdmin, async (req, res) => {
    try {
        await ensureInit();
        const { id } = req.params;

        const existingUser = await db.getUser(id);
        if (!existingUser) {
            return res.status(404).json({ success: false, error: 'User tidak ditemukan' });
        }

        if (existingUser.is_admin || String(existingUser.id) === String(req.user.id)) {
            return res.status(400).json({ success: false, error: 'Akun administrator tidak dapat dihapus' });
        }

        await auth.revokeUserSessions(id);
        await db.deleteUser(id);
        res.json({ success: true, message: `User '${existingUser.username}' berhasil dihapus` });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// ============================================================
// Database Backup & Restore API (Admin only)
// ============================================================

// Export Database JSON
router.get('/admin/backup/export', requireAdmin, async (req, res) => {
    try {
        await ensureInit();
        const backupData = await db.exportDatabaseJSON();
        const filename = `exbot_backup_${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}.json`;
        res.setHeader('Content-Type', 'application/json');
        res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
        res.send(JSON.stringify(backupData, null, 2));
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Import Database JSON
router.post('/admin/backup/import', requireAdmin, async (req, res) => {
    try {
        await ensureInit();
        const { backup_data, mode } = req.body;

        let backupObj = backup_data;
        if (typeof backup_data === 'string') {
            try {
                backupObj = JSON.parse(backup_data);
            } catch (err) {
                return res.status(400).json({ success: false, error: 'Format JSON tidak valid' });
            }
        }

        if (!backupObj) {
            return res.status(400).json({ success: false, error: 'Data backup JSON tidak ditemukan dalam request body' });
        }

        const counts = await db.importDatabaseJSON(backupObj, mode || 'merge');
        res.json({
            success: true,
            message: 'Database berhasil diimport dan dipulihkan',
            mode: mode || 'merge',
            counts
        });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Download Raw SQLite DB File
router.get('/admin/backup/download-db', requireAdmin, async (req, res) => {
    try {
        const fs = require('fs');
        const path = require('path');
        const defaultDbPath = process.env.DB_PATH || path.resolve(__dirname, 'data/dca_bot.db');
        const altDbPath = path.resolve(__dirname, 'database.sqlite');
        const dbPath = fs.existsSync(defaultDbPath) ? defaultDbPath : (fs.existsSync(altDbPath) ? altDbPath : null);

        if (!dbPath || !fs.existsSync(dbPath)) {
            return res.status(404).json({ success: false, error: 'File database SQLite tidak ditemukan' });
        }

        const filename = `dca_bot_${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}.db`;
        res.setHeader('Content-Type', 'application/x-sqlite3');
        res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
        res.sendFile(dbPath);
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

module.exports = router;
