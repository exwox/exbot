/**
 * Exbot DCA Bot - Web Dashboard (Node.js Version)
 * Dashboard web untuk monitoring dan konfigurasi bot DCA
 */

const express = require('express');
const axios = require('axios');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { redactSensitive, safeMetadata } = require('./log-redaction');
const { requireLiveTrading } = require('./live-trading-policy');
const { DEFAULT_STRATEGY } = require('./strategy-defaults');

const app = express();
const PORT = process.env.PORT || 5000;
const HOST = process.env.DASHBOARD_HOST || '127.0.0.1';
const DEBUG_LOGGING = String(process.env.LOG_LEVEL || 'INFO').toUpperCase() === 'DEBUG';

function debugLog(message, metadata = null) {
    if (!DEBUG_LOGGING) return;
    const entry = {
        timestamp: new Date().toISOString(),
        level: 'DEBUG',
        component: 'dashboard',
        message: redactSensitive(message)
    };
    if (metadata) entry.metadata = JSON.parse(safeMetadata(metadata));
    console.debug(JSON.stringify(entry));
}

// Middleware
app.disable('x-powered-by');
app.use((req, res, next) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
    res.setHeader('Content-Security-Policy', "default-src 'self' https:; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:; connect-src 'self' https:; frame-ancestors 'none'");
    next();
});
app.use(express.json({ limit: '256kb' }));
app.use(express.urlencoded({ extended: true, limit: '64kb' }));
app.use((req, res, next) => {
    req.requestId = req.headers['x-request-id'] || crypto.randomUUID();
    res.setHeader('X-Request-ID', req.requestId);
    if (req.path.startsWith('/api/') || req.path === '/login' || req.path === '/register') {
        res.setHeader('Cache-Control', 'no-store');
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method) && req.headers.origin) {
        try {
            if (new URL(req.headers.origin).host !== req.headers.host) {
                return res.status(403).json({ success: false, error: 'Cross-site request ditolak' });
            }
        } catch (_) {
            return res.status(403).json({ success: false, error: 'Origin request tidak valid' });
        }
    }
    next();
});
app.use(express.static('dashboard'));
app.get('/favicon.ico', (req, res) => {
    res.sendFile(path.join(__dirname, 'favicon.ico'));
});

// Initialize modules
const Database = require('./database');
const accounts = require('./accounts');
const auth = require('./auth');
const { IndodaxClient } = require('./indodax-client');
const apiRoutes = require('./api-endpoints');
const { TelegramService } = require('./telegram-service');
let telegramService = null;

// Session middleware
app.use(async (req, res, next) => {
    try {
        const cookies = Object.fromEntries(String(req.headers.cookie || '').split(';').map(item => {
            const index = item.indexOf('=');
            return index < 0 ? ['', ''] : [item.slice(0, index).trim(), decodeURIComponent(item.slice(index + 1))];
        }).filter(([key]) => key));
        const bearer = req.headers.authorization?.match(/^Bearer\s+(.+)$/i)?.[1];
        const sessionToken = cookies.xbot_session || bearer;


        if (sessionToken) {
            req.user = await auth.getCurrentUser(sessionToken);
            req.sessionToken = sessionToken;
        }
    } catch (e) {
        // Silently ignore session validation errors
        console.error('[AUTH] Session middleware error:', redactSensitive(e.message));
    }

    next();
});

// Auth routes
app.get('/login', (req, res) => {
    if (req.user) {
        return res.redirect('/');
    }
    res.send(fs.readFileSync('templates/login.html', 'utf8'));
});

app.get('/register', (req, res) => {
    if (req.user) {
        return res.redirect('/');
    }
    res.send(fs.readFileSync('templates/register.html', 'utf8'));
});

app.post('/logout', async (req, res) => {
    await auth.logout(req.sessionToken);
    res.setHeader('Set-Cookie', 'xbot_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0');
    res.redirect('/login');
});

// Secondary dashboard pages require the server-side HttpOnly cookie session.
for (const page of ['trades', 'logs', 'settings', 'backtest', 'admin']) {
    app.get(`/${page}`, (req, res) => {
        if (!req.user) return res.redirect('/login');
        if (page === 'admin' && !req.user.is_admin) return res.status(403).send('Akses ditolak');
        res.send(fs.readFileSync(`templates/${page}.html`, 'utf8'));
    });
}

// Use API routes
app.use('/api', apiRoutes);

// Initialize database
let db = null;
let dbInitialized = false;
async function initDatabase() {
    if (!dbInitialized) {
        const encryptionKey = process.env.ENCRYPTION_KEY ||
            (fs.existsSync('.env') ?
                fs.readFileSync('.env', 'utf8').match(/ENCRYPTION_KEY=(.+)/)?.[1]?.trim() : null);

        if (!encryptionKey) {
            console.warn('[DASHBOARD] WARNING: No ENCRYPTION_KEY found in .env');
            console.warn('[DASHBOARD] Generate with: node -e "console.log(require(\'crypto\').randomBytes(32).toString(\'hex\'))"');
        }

        db = new Database();
        db.setEncryptionKey(encryptionKey);
        db.init();
        dbInitialized = true;
        telegramService = new TelegramService(db);
        telegramService.start();

        // Wait for DB to be ready
        await new Promise(resolve => setTimeout(resolve, 500));

        // Wait for DB to be ready
        await new Promise(resolve => setTimeout(resolve, 500));
    }
}

// Load config from database
async function loadConfig(userId = null) {
    await initDatabase();
    let activeAccounts = userId
        ? await accounts.getUserActiveAccounts(userId)
        : await accounts.getActiveAccounts();

    if (activeAccounts.length === 0) {
        return {
            TRADING_PAIR: 'btcidr', BASE_ORDER_IDR: DEFAULT_STRATEGY.base_order_amount,
            SAFETY_ORDER_IDR: DEFAULT_STRATEGY.safety_order_amount,
            MAX_SAFETY_ORDERS: DEFAULT_STRATEGY.max_safety_orders,
            SAFETY_ORDER_DISTANCE: DEFAULT_STRATEGY.price_deviation,
            TAKE_PROFIT_PERCENT: DEFAULT_STRATEGY.take_profit_percent,
            STOP_LOSS_PERCENT: DEFAULT_STRATEGY.stop_loss_percent,
            MARTINGALE_ENABLED: DEFAULT_STRATEGY.martingale_enabled,
            VOLUME_SCALE: DEFAULT_STRATEGY.volume_scale,
            STEP_SCALE: DEFAULT_STRATEGY.deviation_scale,
            DCA_INTERVAL_HOURS: 24, DCA_AMOUNT_IDR: 10000,
            RSI_PERIOD: DEFAULT_STRATEGY.rsi_period,
            RSI_OVERSOLD: DEFAULT_STRATEGY.rsi_oversold,
            RSI_OVERBOUGHT: DEFAULT_STRATEGY.rsi_overbought,
            DRY_RUN: DEFAULT_STRATEGY.dry_run,
            LOG_FILE: 'dca_bot.log', DATA_FILE: 'dca_data.json'
        };
    }

    // Use the first active account owned by this user.
    const account = activeAccounts[0];
    const creds = await accounts.getDecryptedCredentials(account.id);

    // Get bot configuration
    const bots = await db.getAccountBots(account.id);
    const bot = bots.find(b => b.account_id === account.id) || bots[0];

    // Get strategy configuration
    let strategy = null;
    if (bot && bot.strategy_id) {
        strategy = await db.getStrategy(bot.strategy_id);
    }
    if (!strategy) {
        const strategies = userId ? await db.getUserStrategies(userId) : await db.getAllStrategies();
        strategy = strategies[0];
    }

    const config = {
        INDODAX_API_KEY: creds?.api_key || '',
        INDODAX_SECRET_KEY: creds?.api_secret || '',
        TRADING_PAIR: bot?.pair || 'btcidr',
        BASE_ORDER_IDR: strategy?.base_order_amount ?? DEFAULT_STRATEGY.base_order_amount,
        SAFETY_ORDER_IDR: strategy?.safety_order_amount ?? DEFAULT_STRATEGY.safety_order_amount,
        MAX_SAFETY_ORDERS: strategy?.max_safety_orders ?? DEFAULT_STRATEGY.max_safety_orders,
        SAFETY_ORDER_DISTANCE: strategy?.price_deviation ?? DEFAULT_STRATEGY.price_deviation,
        TAKE_PROFIT_PERCENT: strategy?.take_profit_percent ?? DEFAULT_STRATEGY.take_profit_percent,
        STOP_LOSS_PERCENT: strategy?.stop_loss_percent ?? DEFAULT_STRATEGY.stop_loss_percent,
        LIMIT_BUY_FEE_PERCENT: strategy?.limit_buy_fee_percent ?? DEFAULT_STRATEGY.limit_buy_fee_percent,
        LIMIT_SELL_FEE_PERCENT: strategy?.limit_sell_fee_percent ?? DEFAULT_STRATEGY.limit_sell_fee_percent,
        MARKET_BUY_FEE_PERCENT: strategy?.market_buy_fee_percent ?? DEFAULT_STRATEGY.market_buy_fee_percent,
        MARKET_SELL_FEE_PERCENT: strategy?.market_sell_fee_percent ?? DEFAULT_STRATEGY.market_sell_fee_percent,
        MARTINGALE_ENABLED: strategy?.martingale_enabled ?? DEFAULT_STRATEGY.martingale_enabled,
        VOLUME_SCALE: strategy?.volume_scale ?? DEFAULT_STRATEGY.volume_scale,
        STEP_SCALE: strategy?.deviation_scale ?? DEFAULT_STRATEGY.deviation_scale,
        DCA_INTERVAL_HOURS: 24,
        DCA_AMOUNT_IDR: 10000,
        RSI_PERIOD: strategy?.rsi_period ?? DEFAULT_STRATEGY.rsi_period,
        RSI_OVERSOLD: strategy?.rsi_oversold ?? DEFAULT_STRATEGY.rsi_oversold,
        RSI_OVERBOUGHT: strategy?.rsi_overbought ?? DEFAULT_STRATEGY.rsi_overbought,
        DRY_RUN: bot?.dry_run ?? true,
        LOG_FILE: 'dca_bot.log',
        DATA_FILE: `data/dca_data_${account.id.replace(/[^a-zA-Z0-9_-]/g, '_')}.json`
    };

    return config;
}

// Global state
let config = {};
let botState = {
    running: false,
    lastUpdate: null,
    botData: {}
};

// Load bot data
function loadBotData(dataFile = config.DATA_FILE || 'dca_data.json') {
    try {
        const data = fs.readFileSync(dataFile, 'utf8');
        return JSON.parse(data);
    } catch (err) {
        return {
            last_trade: null,
            next_trade: null,
            total_trades: 0,
            total_invested: 0,
            total_crypto_bought: 0,
            trades: []
        };
    }
}

// Save bot data
function saveBotData(data, dataFile = config.DATA_FILE || 'dca_data.json') {
    fs.writeFileSync(dataFile, JSON.stringify(data, null, 4));
}

// Crypto icon mapping
const CRYPTO_ICONS = {
    'btc': '₿', 'eth': 'Ξ', 'bch': 'Ƀ', 'doge': 'Ð', 'xrp': '✕',
    'idr': 'Rp', 'usdt': '₮', 'bnb': 'BNB', 'sol': '◎', 'ada': '₳',
    'dot': 'DOT', 'matic': 'MATIC', 'link': 'LINK', 'uni': '🦄',
    'avax': 'AVAX', 'ltc': 'Ł', 'xlm': '*', 'trx': 'TRX', 'etc': 'Ξ'
};

function getCryptoIcon(symbol) {
    const symbolLower = symbol.toLowerCase();
    return CRYPTO_ICONS[symbolLower] || symbol.toUpperCase().slice(0, 2);
}

// Get all balances with current prices
async function getAllBalances(balanceData, accountId = null) {
    if (balanceData.error || !balanceData.balance) {
        debugLog('getAllBalances: invalid balance data');
        return [];
    }

    const balances = [];
    const balanceDict = balanceData.balance || {};

    debugLog('getAllBalances: balance items loaded', {
        count: Object.keys(balanceDict).length
    });

    // Get all ticker prices at once
    const account = accountId ? await accounts.getAccount(accountId) : (await accounts.getActiveAccounts())[0];
    if (!account || !account.is_active) {
        return [];
    }

    const creds = await accounts.getDecryptedCredentials(account.id);
    if (!creds) {
        return [];
    }

    const client = new IndodaxClient(creds.api_key, creds.api_secret);
    const allTickers = await client.get_ticker_all();

    debugLog('getAllBalances: ticker response received', {
        response_type: typeof allTickers
    });
    if (allTickers.error) {
        debugLog('getAllBalances: ticker request failed', {
            error: allTickers.error
        });
    }

    // Build price lookup dictionary
    const priceMap = {};
    if (!allTickers.error && allTickers.tickers) {
        const tickersCount = Object.keys(allTickers.tickers).length;
        debugLog('getAllBalances: tickers loaded', { count: tickersCount });

        const idrPairs = [];
        const otherPairs = [];

        for (const [pair, tickerData] of Object.entries(allTickers.tickers)) {
            if (tickerData && typeof tickerData === 'object' && tickerData.last) {
                try {
                    const price = parseFloat(tickerData.last);
                    const pairLower = pair.toLowerCase();

                    priceMap[pairLower] = price;

                    const parts = pairLower.split('_');
                    const symbolFromPair = parts[0];
                    const quoteCurrency = parts[1];

                    if (quoteCurrency === 'idr') {
                        idrPairs.push({ symbol: symbolFromPair, price: price });
                    } else {
                        otherPairs.push({ symbol: symbolFromPair, price: price });
                    }
                } catch (e) {
                    // Skip invalid data
                }
            }
        }

        for (const { symbol, price } of idrPairs) {
            priceMap[symbol] = price;
        }

        for (const { symbol, price } of otherPairs) {
            if (!(symbol in priceMap)) {
                priceMap[symbol] = price;
            }
        }
        debugLog('getAllBalances: price map built', {
            count: Object.keys(priceMap).length
        });
    } else if (!allTickers.error) {
        debugLog('getAllBalances: response has no tickers key');
    }

    for (const [symbol, amount] of Object.entries(balanceDict)) {
        if (symbol === 'idr' || parseFloat(amount) === 0) {
            continue;
        }

        const amountFloat = parseFloat(amount);
        if (amountFloat < 0.00000001) {
            continue;
        }

        const icon = getCryptoIcon(symbol);
        const symbolLower = symbol.toLowerCase();
        const price = priceMap[symbolLower] || 0;

        if (price === 0) {
            debugLog('getAllBalances: asset price unavailable', {
                symbol, lookup_key: symbolLower
            });
        } else {
            debugLog('getAllBalances: asset price resolved', { symbol, price });
        }

        const valueIdr = amountFloat * price;

        balances.push({
            symbol: symbol.toUpperCase(),
            icon: icon,
            amount: amountFloat,
            price: price,
            value_idr: valueIdr,
            pair: symbolLower + 'idr'
        });
    }

    balances.sort((a, b) => b.value_idr - a.value_idr);

    debugLog('getAllBalances: balances returned', { count: balances.length });
    return balances;
}

// Routes
app.get('/', async (req, res) => {
    try {
        await initDatabase();
        if (!req.user) return res.redirect('/login');
        const userConfig = await loadConfig(req.user.id);
        const botData = loadBotData(userConfig.DATA_FILE);
        const selectedUserBot = await getCurrentUserBot(req.user.id);
        const userBotRunning = selectedUserBot?.status === 'RUNNING';
        const activeAccounts = await accounts.getUserActiveAccounts(req.user.id);

        let currentPrice = 0;
        let idrBalance = 0;
        let cryptoBalance = 0;
        let allCryptoBalances = [];

        if (activeAccounts.length > 0) {
            const creds = await accounts.getDecryptedCredentials(activeAccounts[0].id);
            if (creds) {
                const client = new IndodaxClient(creds.api_key, creds.api_secret);

                const ticker = await client.get_ticker(userConfig.TRADING_PAIR || 'btcidr');
                currentPrice = !ticker.error ? parseFloat(ticker.last) || 0 : 0;

                const balance = await client.get_balance();
                if (!balance.error) {
                    idrBalance = parseFloat(balance.balance?.idr || 0);
                    const cryptoSymbol = (userConfig.TRADING_PAIR || 'btcidr').replace('idr', '');
                    cryptoBalance = parseFloat(balance.balance?.[cryptoSymbol] || 0);
                    allCryptoBalances = await getAllBalances(balance, activeAccounts[0].id);
                }
            }
        }

        // Calculate next trade time
        let nextTrade = null;
        if (botData.last_trade) {
            const lastTrade = new Date(botData.last_trade);
            const nextTradeDate = new Date(lastTrade.getTime() + (userConfig.DCA_INTERVAL_HOURS || 24) * 60 * 60 * 1000);
            nextTrade = nextTradeDate.toISOString().replace('T', ' ').slice(0, 19);
        }

        // Calculate average price and P/L
        let avgPrice = 0;
        let profitLoss = 0;
        if (botData.total_crypto_bought > 0) {
            avgPrice = botData.total_invested / botData.total_crypto_bought;
            if (currentPrice > 0) {
                profitLoss = ((currentPrice - avgPrice) / avgPrice) * 100;
            }
        }

        // Calculate total portfolio value
        const totalCryptoValue = allCryptoBalances.reduce((sum, b) => sum + b.value_idr, 0);
        const totalPortfolioValue = idrBalance + totalCryptoValue;

        // Trading pair display
        const tradingPairDisplay = (userConfig.TRADING_PAIR || 'btcidr').toUpperCase().replace('IDR', '/IDR');

        // Render the template
        let html = fs.readFileSync('templates/index.html', 'utf8');
        debugLog('dashboard template loaded', { length: html.length });

        // Helper function to format numbers as Indonesian Rupiah
        const formatRupiah = (num) => {
            if (num === null || num === undefined || num === 0) return '0';
            return new Intl.NumberFormat('id-ID', { maximumFractionDigits: 0 }).format(num);
        };

        const formatFloat8 = (num) => {
            if (num === null || num === undefined) return '0';
            return num.toFixed(8);
        };

        // Replace Jinja2 conditional expressions
        html = html.replace(/{{\s*'status-running'\s+if\s+bot_running\s+else\s+'status-stopped'\s*}}/g,
            userBotRunning ? 'status-running' : 'status-stopped');
        html = html.replace(/{{\s*'Running'\s+if\s+bot_running\s+else\s+'Stopped'\s*}}/g,
            userBotRunning ? 'Running' : 'Stopped');
        html = html.replace(/{{\s*'DRY RUN'\s+if\s+current_config\.dry_run\s+else\s+'LIVE MODE'\s*}}/g,
            userConfig.DRY_RUN ? 'DRY RUN' : 'LIVE MODE');
        html = html.replace(/{{\s*'bg-success'\s+if\s+current_config\.dry_run\s+else\s+'bg-warning text-dark'\s*}}/g,
            userConfig.DRY_RUN ? 'bg-success' : 'bg-warning text-dark');

        // Create a context object with all template variables
        const templateVars = {
            current_price: currentPrice,
            idr_balance: idrBalance,
            crypto_balance: cryptoBalance,
            bot_data: botData,
            total_trades: botData.total_trades || 0,
            total_invested: botData.total_invested || 0,
            total_crypto_bought: botData.total_crypto_bought || 0,
            avg_price: avgPrice,
            profit_loss: profitLoss,
            next_trade: nextTrade,
            bot_running: userBotRunning,
            current_config: userConfig,
            trading_pair_display: tradingPairDisplay,
            all_crypto_balances: allCryptoBalances,
            total_crypto_value: totalCryptoValue,
            total_portfolio_value: totalPortfolioValue
        };

        // Replace filter syntax: {{ (value|format_rupiah) if value else '0' }}
        html = html.replace(/{{\s*\(([^|}]+)\|format_rupiah\)\s+if\s+([^()|}]+)\s+else\s+'([^']*)'\s*}}/g, (match, varName, condition, defaultVal) => {
            try {
                const varNameTrimmed = varName.trim();
                const value = templateVars[varNameTrimmed];
                if (value && value !== 0) {
                    return formatRupiah(value);
                }
                return defaultVal;
            } catch (e) {
                console.log('[ERROR] format_rupiah if/else:', e.message, 'varName:', varName);
                return defaultVal;
            }
        });

        // Replace filter syntax: {{ value|format_rupiah }}
        html = html.replace(/{{\s*([^|}]+)\|format_rupiah\s*}}/g, (match, varName) => {
            try {
                const varNameTrimmed = varName.trim();
                const value = templateVars[varNameTrimmed];
                return formatRupiah(value);
            } catch (e) {
                console.log('[ERROR] format_rupiah:', e.message, 'varName:', varName);
                return '0';
            }
        });

        // Replace filter syntax: {{ value|format_float }}
        html = html.replace(/{{\s*([^|}]+)\|format_float\s*}}/g, (match, varName) => {
            try {
                const varNameTrimmed = varName.trim();
                const value = templateVars[varNameTrimmed];
                return value.toFixed(8);
            } catch (e) {
                console.log('[ERROR] format_float:', e.message, 'varName:', varName);
                return '0';
            }
        });

        // Replace simple variable references
        html = html.replace(/{{\s*bot_running\s*}}/g, userBotRunning ? 'Running' : 'Stopped');
        html = html.replace(/{{\s*bot_data\.total_trades\s*}}/g, String(botData.total_trades || 0));

        // Replace next_trade
        html = html.replace(/{{\s*next_trade\s*}}/g, nextTrade || '');

        // Replace trading pair display
        html = html.replace(/{{\s*current_config\.trading_pair\.upper\(\)\.replace\([^)]+\)\s*}}/g, tradingPairDisplay);

        // Replace Python-style format strings
        html = html.replace(/{{\s*"[^"]*"\.format\(([^}]+)\)\s*}}/g, (match, varName) => {
            try {
                const varNameTrimmed = varName.trim();
                const value = templateVars[varNameTrimmed];
                if (value === undefined || value === null) return '0';
                return formatRupiah(parseFloat(value) || 0);
            } catch (e) {
                return '0';
            }
        });

        // Replace conditional format strings
        html = html.replace(/{{\s*"[^"]*"\.format\(([^}]+)\)\s+if\s+([^}]+)\s+else\s+'([^']*)'\s*}}/g, (match, varName, condition, defaultVal) => {
            try {
                const varNameTrimmed = varName.trim();
                const value = templateVars[varNameTrimmed];
                if (value && value !== 0) {
                    return formatRupiah(parseFloat(value) || 0);
                }
                return defaultVal;
            } catch (e) {
                return defaultVal;
            }
        });

        res.send(html);
    } catch (e) {
        console.error('[DASHBOARD] Error:', e);
        res.status(500).send('Error loading dashboard: ' + e.message);
    }
});

// API Routes

// Liveness intentionally has no database dependency. Readiness additionally
// proves that SQLite responds and the independently supervised Python manager
// has completed a reconciliation loop recently.
app.get('/healthz', (_req, res) => {
    res.json({ status: 'ok', service: 'xbot-dashboard', uptime_seconds: Math.floor(process.uptime()) });
});

app.get('/readyz', async (_req, res) => {
    try {
        await initDatabase();
        await new Promise((resolve, reject) => {
            db.db.get('SELECT 1 AS ok', [], (error, row) => {
                if (error || row?.ok !== 1) reject(error || new Error('SQLite check failed'));
                else resolve();
            });
        });
        const heartbeatPath = process.env.MANAGER_HEARTBEAT_PATH || '/tmp/xbot-manager-heartbeat';
        const heartbeatAge = (Date.now() - fs.statSync(heartbeatPath).mtimeMs) / 1000;
        if (heartbeatAge > 15) throw new Error(`Bot Manager heartbeat stale (${heartbeatAge.toFixed(1)}s)`);
        res.json({ status: 'ready', database: 'ok', bot_manager: 'ok' });
    } catch (error) {
        res.status(503).json({ status: 'not_ready', error: error.message });
    }
});

// Get status
app.get('/api/status', async (req, res) => {
    try {
        await initDatabase();
        if (!req.user) {
            return res.status(401).json({ success: false, error: 'Authentication required' });
        }
        const config = await loadConfig(req.user.id);
        const selectedBot = await getCurrentUserBot(req.user.id, req.query.bot_id);
        if (req.query.bot_id && !selectedBot) {
            return res.status(404).json({ success: false, error: 'Bot tidak ditemukan' });
        }
        if (selectedBot) {
            config.TRADING_PAIR = selectedBot.pair || config.TRADING_PAIR;
            config.DRY_RUN = !!selectedBot.dry_run;
            if (selectedBot.strategy_id) {
                const strategy = await db.getStrategy(selectedBot.strategy_id);
                if (strategy) {
                    config.MAX_SAFETY_ORDERS = strategy.max_safety_orders;
                    config.SAFETY_ORDER_DISTANCE = strategy.price_deviation;
                    config.STEP_SCALE = strategy.deviation_scale;
                    config.STEP_SCALE_ENABLED = !!strategy.step_scale_enabled;
                    config.TAKE_PROFIT_PERCENT = strategy.take_profit_percent;
                    config.STOP_LOSS_PERCENT = strategy.stop_loss_percent;
                    config.LIMIT_BUY_FEE_PERCENT = strategy.limit_buy_fee_percent ?? 0.15;
                    config.LIMIT_SELL_FEE_PERCENT = strategy.limit_sell_fee_percent ?? 0.15;
                    config.MARKET_BUY_FEE_PERCENT = strategy.market_buy_fee_percent ?? 0.30;
                    config.MARKET_SELL_FEE_PERCENT = strategy.market_sell_fee_percent ?? 0.30;
                }
            }
        }
        const position = selectedBot ? await db.getPosition(selectedBot.id) : null;
        // Python workers persist their state in SQLite. Prefer it over the
        // legacy JSON file so dry-run and live bots render the same state.
        const botData = position ? {
            active_position: position.status === 'OPEN',
            position_status: position.status,
            pending_base: position.status === 'PENDING_BASE',
            base_price: Number(position.base_price) || 0,
            total_invested: Number(position.total_invested) || 0,
            total_crypto_bought: Math.max(
                (Number(position.total_amount) || 0) -
                (Number(position.sold_amount) || 0), 0),
            reserved_capital: Number(position.reserved_capital) || 0,
            tp_price: Number(position.take_profit_price) || 0,
            sl_price: Number(position.stop_loss_price) || 0,
            exit_order_id: position.exit_order_id || null,
            exit_reason: position.exit_reason || '',
            so_entries: position.so_entries || [],
            open_orders: position.open_orders || [],
            last_trade: position.updated_at,
            trades: []
        } : loadBotData(config.DATA_FILE);
        const isBotRunning = selectedBot?.status === 'RUNNING';
        const activeAccounts = await accounts.getUserActiveAccounts(req.user.id);

        let currentPrice = 0;
        let idrBalance = 0;
        let cryptoBalance = 0;
        let allCryptoBalances = [];
        let apiKey = '';
        let secretKeyMasked = '';

        const cleanPair = (config.TRADING_PAIR || 'btcidr').toLowerCase().replace(/[\/\-\s]/g, '');
        const cryptoSymbol = cleanPair.replace(/_?idr$/, '').replace(/_?usdt$/, '');

        const selectedAccount = selectedBot
            ? activeAccounts.find(account => account.id === selectedBot.account_id)
            : activeAccounts[0];
        if (selectedAccount) {
            const creds = await accounts.getDecryptedCredentials(selectedAccount.id);
            if (creds) {
                apiKey = creds.api_key;
                secretKeyMasked = accounts.maskCredential(creds.api_secret);

                const client = new IndodaxClient(creds.api_key, creds.api_secret);

                const ticker = await client.get_ticker(cleanPair);
                currentPrice = !ticker.error ? parseFloat(ticker.last) || 0 : 0;

                const balance = await client.get_balance();
                if (!balance.error) {
                    idrBalance = parseFloat(balance.balance?.idr || 0);
                    cryptoBalance = parseFloat(balance.balance?.[cryptoSymbol] || 0);
                    allCryptoBalances = await getAllBalances(balance, selectedAccount.id);
                }
            }
        }

        let nextTradeStr = '-';
        let tpPriceStr = '-';

        const formatPrice = (val) => {
            if (!val) return '0';
            const v = Number(val);
            if (v > 0 && v < 1) return v.toFixed(6);
            if (v >= 1 && v < 100) return v.toFixed(2);
            return Math.round(v).toLocaleString('id-ID');
        };

        if (isBotRunning) {
            if (botData.active_position) {
                const filledSoCount = (botData.so_entries || []).length;
                const nextSoLevel = filledSoCount + 1;
                const maxSo = config.MAX_SAFETY_ORDERS || 5;

                if (nextSoLevel <= maxSo) {
                    const nextSoOrder = (botData.open_orders || []).find(o => o.so_number === nextSoLevel);
                    let nextSoPrice = 0;

                    if (nextSoOrder) {
                        nextSoPrice = parseFloat(nextSoOrder.price);
                    } else {
                        const stepScale = config.STEP_SCALE || 1;
                        const basePrice = parseFloat(botData.base_price) || currentPrice;
                        const distance = (config.SAFETY_ORDER_DISTANCE || 1.2) * Math.pow(stepScale, nextSoLevel - 1);
                        nextSoPrice = Math.round(basePrice * (1 - distance / 100));
                    }

                    nextTradeStr = `SO${nextSoLevel}, Rp ${formatPrice(nextSoPrice)}`;
                } else {
                    nextTradeStr = 'Max SO Reached';
                }

                if (botData.tp_price > 0) {
                    tpPriceStr = `Rp ${formatPrice(botData.tp_price)}`;
                }
            } else {
                const pendingReentry = botData.pending_new_entry === true || botData.pending_new_entry === undefined;
                const rsiOversold = config.RSI_OVERSOLD || 30;

                if (pendingReentry) {
                    nextTradeStr = `BASE, RSI <=${rsiOversold}`;
                    tpPriceStr = 'Waiting Base Order';
                } else {
                    nextTradeStr = `BASE, Rp ${formatPrice(currentPrice)}`;
                    const tpPrice = Math.round(currentPrice * (1 + (config.TAKE_PROFIT_PERCENT || 1) / 100));
                    tpPriceStr = `Rp ${formatPrice(tpPrice)}`;
                }
            }
        } else {
            nextTradeStr = 'Bot Stopped';
        }

        let avgPrice = 0;
        let profitLoss = 0;
        let profitLossIdr = 0;
        if (botData.total_crypto_bought > 0) {
            avgPrice = botData.total_invested / botData.total_crypto_bought;
            if (currentPrice > 0) {
                profitLossIdr = (botData.total_crypto_bought * currentPrice *
                    (1 - (config.MARKET_SELL_FEE_PERCENT || 0) / 100)) - botData.total_invested;
                profitLoss = botData.total_invested > 0
                    ? (profitLossIdr / botData.total_invested) * 100
                    : 0;
            }
        }

        const tradeStats = selectedBot
            ? await db.getBotTradeStats(selectedBot.id)
            : { total_trades: 0, realized_profit: 0, completed_cycles: 0 };
        const realizedProfit = Number(tradeStats.realized_profit) || 0;
        const totalTradesCount = Number(tradeStats.total_trades) || 0;

        const totalCryptoValue = allCryptoBalances.reduce((sum, b) => sum + b.value_idr, 0);
        const totalPortfolioValue = idrBalance + totalCryptoValue;

        res.json({
            success: true,
            data: {
                current_price: currentPrice,
                idr_balance: idrBalance,
                crypto_balance: cryptoBalance,
                all_crypto_balances: allCryptoBalances,
                total_crypto_value: totalCryptoValue,
                total_portfolio_value: totalPortfolioValue,
                total_trades: totalTradesCount || botData.total_trades || 0,
                total_invested: botData.total_invested || 0,
                total_crypto_bought: botData.total_crypto_bought || 0,
                avg_price: avgPrice,
                profit_loss: profitLoss,
                profit_loss_idr: profitLossIdr,
                realized_profit: realizedProfit,
                completed_cycles: Number(tradeStats.completed_cycles) || 0,
                next_trade: nextTradeStr,
                last_trade: botData.last_trade,
                bot_running: isBotRunning,
                dry_run: config.DRY_RUN,
                api_key_masked: accounts.maskCredential(apiKey),
                secret_key_masked: secretKeyMasked,
                rsi_period: config.RSI_PERIOD || 14,
                rsi_oversold: config.RSI_OVERSOLD || 30,
                rsi_overbought: config.RSI_OVERBOUGHT || 70,
                active_position: botData.active_position || false,
                selected_bot_id: selectedBot?.id || null,
                selected_pair: config.TRADING_PAIR,
                crypto_symbol: cryptoSymbol.toUpperCase(),
                base_price: botData.base_price || 0,
                tp_price: botData.tp_price || 0,
                sl_price: botData.sl_price || 0,
                so_entries: botData.so_entries || [],
                open_orders: botData.open_orders || [],
                so_distance: config.SAFETY_ORDER_DISTANCE,
                step_scale: config.STEP_SCALE,
                step_scale_enabled: !!config.STEP_SCALE_ENABLED,
                max_so: config.MAX_SAFETY_ORDERS,
                take_profit: config.TAKE_PROFIT_PERCENT,
                stop_loss: config.STOP_LOSS_PERCENT,
                next_trade_desc: nextTradeStr,
                tp_price_desc: tpPriceStr
            }
        });
    } catch (e) {
        console.error('[API] Status error:', e);
        res.json({ success: false, error: e.message });
    }
});

// Start server
const server = http.createServer(app);
server.listen(PORT, HOST, async () => {
    console.log('='.repeat(50));
    console.log('🌐 Exbot DCA Bot Dashboard (Node.js)');
    console.log('='.repeat(50));

    // Initialize database and load config
    await initDatabase();
    await db.recordRuntimeStart('node-dashboard');
    // Do not decrypt a tenant's credentials during process startup. Request
    // handlers load configuration only after authentication and ownership.
    config = await loadConfig('__system__');

    console.log(`📍 Dashboard berjalan di: http://${HOST}:${PORT}`);
    console.log('⏹️  Tekan Ctrl+C untuk menghentikan server');
    console.log('='.repeat(50));
});

async function getCurrentUserBot(userId, requestedBotId = null) {
    const activeAccounts = await accounts.getUserActiveAccounts(userId);
    for (const account of activeAccounts) {
        const bots = await db.getAccountBots(account.id);
        if (requestedBotId) {
            const match = bots.find(bot => bot.id === requestedBotId);
            if (match) return match;
        } else if (bots.length) {
            return bots[0];
        }
    }
    return null;
}

// Graceful shutdown must be registered at module scope. Keeping it outside
// request handlers ensures Docker SIGTERM always stops the Telegram loops and
// closes SQLite before the process exits.
let shuttingDown = false;
function handleShutdown(signal) {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`\n[DASHBOARD] Menerima sinyal ${signal}. Menghentikan service...`);
    if (telegramService) telegramService.stop();
    server.close(() => {
        if (db) db.close();
        console.log('[DASHBOARD] Web server dihentikan.');
        process.exit(0);
    });
    setTimeout(() => process.exit(1), 40000).unref();
}
process.on('SIGINT', () => handleShutdown('SIGINT'));
process.on('SIGTERM', () => handleShutdown('SIGTERM'));

// Available chart contexts are deliberately limited to the authenticated
// user's bots.  The browser selects a bot id, so the same pair in dry-run and
// real mode can never share markers or open orders.
app.get('/api/chart-options', async (req, res) => {
    try {
        await initDatabase();
        if (!req.user) return res.status(401).json({ success: false, error: 'Authentication required' });
        const activeAccounts = await accounts.getUserActiveAccounts(req.user.id);
        const bots = [];
        for (const account of activeAccounts) {
            const accountBots = await db.getAccountBots(account.id);
            for (const bot of accountBots) {
                bots.push({
                    id: bot.id,
                    name: bot.name,
                    pair: bot.pair || 'btcidr',
                    dry_run: !!bot.dry_run,
                    status: bot.status
                });
            }
        }
        res.json({ success: true, data: bots });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Dashboard compatibility APIs.  The browser UI uses these endpoints for the
// run controls and chart annotations; each operation is scoped to its owner.
app.post('/api/start', async (req, res) => {
    try {
        await initDatabase();
        if (!req.user) return res.status(401).json({ success: false, message: 'Authentication required' });
        const bot = await getCurrentUserBot(req.user.id);
        if (!bot) return res.status(404).json({ success: false, message: 'Belum ada bot DCA untuk akun Anda.' });
        if (bot.status === 'RUNNING') return res.json({ success: false, message: 'Bot sudah berjalan.' });
        if (!bot.dry_run) {
            const completedDryCycles = await db.getCompletedDryRunCycleCount(bot.id);
            const strategy = bot.strategy_id
                ? await db.getStrategy(bot.strategy_id) : null;
            requireLiveTrading(
                bot.id, completedDryCycles, process.env, strategy);
        }
        bot.status = 'RUNNING';
        await db.updateBot(bot);
        botState.running = true;
        botState.lastUpdate = new Date().toISOString();
        res.json({ success: true, message: `Bot ${bot.name} dijalankan.` });
    } catch (e) {
        res.status(e.statusCode || 500).json({ success: false, message: e.message });
    }
});

app.post('/api/stop', async (req, res) => {
    try {
        await initDatabase();
        if (!req.user) return res.status(401).json({ success: false, message: 'Authentication required' });
        const bot = await getCurrentUserBot(req.user.id);
        if (!bot) return res.status(404).json({ success: false, message: 'Bot tidak ditemukan.' });
        bot.status = 'STOPPED';
        await db.updateBot(bot);
        botState.running = false;
        botState.lastUpdate = new Date().toISOString();
        res.json({ success: true, message: `Bot ${bot.name} dihentikan.` });
    } catch (e) {
        res.status(500).json({ success: false, message: e.message });
    }
});

app.get('/api/trade-markers', async (req, res) => {
    try {
        if (!req.user) return res.status(401).json({ success: false, error: 'Authentication required' });
        const bot = await getCurrentUserBot(req.user.id, req.query.bot_id);
        if (req.query.bot_id && !bot) return res.status(404).json({ success: false, error: 'Bot tidak ditemukan' });
        const ledger = bot ? await db.getBotTrades(bot.id, 500) : [];
        let markers = ledger.map(trade => ({
            timestamp: new Date(trade.executed_at || trade.created_at).getTime() / 1000,
            price: Number(trade.price) || 0,
            type: trade.trade_type || trade.side,
            amount_idr: Number(trade.amount_quote) || 0,
            crypto_amount: Number(trade.amount) || 0,
            profit_idr: Number(trade.realized_profit) || 0,
            profit_percent: Number(trade.realized_profit_percent) || 0,
            historical: !!trade.position_status && trade.position_status !== 'OPEN',
            dry_run: !!trade.dry_run
        }));

        // Compatibility for an open position created before the ledger
        // migration. Closed cycles are preserved by ledger rows going forward.
        if (markers.length === 0 && bot) {
            const position = await db.getPosition(bot.id);
            if (position) {
                markers = [{
                    timestamp: new Date(position.created_at).getTime() / 1000,
                    price: Number(position.base_price) || 0,
                    type: 'base',
                    amount_idr: Number(position.total_invested) || 0,
                    crypto_amount: Number(position.base_amount) || 0,
                    historical: false,
                    dry_run: !!bot.dry_run
                }, ...(position.so_entries || []).map(entry => ({
                    timestamp: new Date(entry.timestamp).getTime() / 1000,
                    price: Number(entry.price) || 0,
                    type: `so_${entry.number || entry.step || 0}`,
                    amount_idr: Number(entry.amount_idr) || 0,
                    crypto_amount: Number(entry.amount_crypto) || 0,
                    historical: false,
                    dry_run: !!bot.dry_run
                }))];
            }
        }
        markers = markers.filter(marker =>
            Number.isFinite(marker.timestamp) && marker.price > 0);
        res.json({
            success: true,
            bot_id: bot?.id || null,
            pair: bot?.pair || null,
            data: markers
        });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});
