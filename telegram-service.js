/**
 * telegram-service.js
 *
 * Outbound notifier and minimal command listener for the Telegram
 * integration. Design rules (mirroring bot-control.js):
 *   - Every credential lookup flows through the Database layer, which keeps
 *     bot tokens encrypted with an account-bound context (owner user id).
 *   - Notifications are only pushed to chats explicitly linked through a
 *     short-lived link code that the owner generated in the web dashboard,
 *     so receiving requires proof of both account and chat ownership.
 *   - Commands are read-only: status summaries and linking helpers only.
 *     Trading control stays web-only for now (bot-control.js is shared-ready).
 */
'use strict';

const axios = require('axios');
const { IndodaxClient } = require('./indodax-client');

const DEFAULT_POLL_INTERVAL_MS = Number(process.env.TELEGRAM_POLL_MS) || 5000;
const FEED_SCAN_INTERVAL_MS = Number(process.env.TELEGRAM_FEED_SCAN_MS) || 15000;
const SEND_TIMEOUT_MS = Number(process.env.TELEGRAM_SEND_TIMEOUT_MS) || 10000;
const TOKEN_CACHE_TTL_MS = 60000;
const MAX_MESSAGES_PER_RUN = Math.min(
    Math.max(Number(process.env.TELEGRAM_MAX_MESSAGES_PER_RUN) || 1, 1), 5);
const DIGEST_HOUR = Math.min(Math.max(Number(process.env.TELEGRAM_DIGEST_HOUR) || 8, 0), 23);
const TELEGRAM_TIMEZONE = process.env.TELEGRAM_TIMEZONE || 'Asia/Jakarta';

function toTelegramHtml(value) {
    const escaped = String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    return escaped.replace(/\*([^*\n]+)\*/g, '<b>$1</b>');
}

class TelegramService {
    /**
     * @param {import('./database')} db Initialized Database instance.
     * @param {{sender?: Function, apiBase?: string}} hooks test seams.
     */
    constructor(db, hooks = {}) {
        this.db = db;
        this.apiBase = hooks.apiBase || process.env.TELEGRAM_API_BASE || 'https://api.telegram.org';
        // `sender` receives (token, payload) and resolves like axios would.
        // Production path goes through axios; unit tests stub the seam.
        this.sender = hooks.sender || ((token, payload) =>
            axios.post(`${this.apiBase}/bot${token}/sendMessage`, payload, {
                timeout: SEND_TIMEOUT_MS
            }));
        this.clock = hooks.clock || (() => new Date());
        this.timers = [];
        this.busy = { outbox: false, feeds: false, commands: false };
        this.tokenCache = new Map(); // userId -> { token, fetched_at }
        this.updatesOffsets = new Map(); // userId -> next offset
        this.lastPollErrorLogAt = 0;
        this.stopped = true;
    }

    /** True when a user can receive right now (armed + at least one chat). */
    async isDeliverable(userId) {
        try {
            const config = await this.db.getTelegramConfig(String(userId));
            return Boolean(config.enabled && config.has_token && config.linked_chats.length > 0);
        } catch {
            return false;
        }
    }

    async resolveToken(userId) {
        const key = String(userId);
        const cached = this.tokenCache.get(key);
        if (cached && Date.now() - cached.fetched_at < TOKEN_CACHE_TTL_MS) {
            return cached.token;
        }
        try {
            const token = await this.db.getTelegramBotToken(key);
            if (token) {
                this.tokenCache.set(key, { token, fetched_at: Date.now() });
            }
            return token;
        } catch (error) {
            return '';
        }
    }

    async reply(senderInfo, text) {
        try {
            const token = await this.resolveToken(senderInfo.userId);
            if (!token) return;
            await this.sender(token, {
                chat_id: String(senderInfo.chat_id),
                text: text
            });
        } catch (error) {
            this.throttledLog(`Reply to chat ${senderInfo.chat_id} failed: ${error.message}`);
        }
    }

    throttledLog(message) {
        const now = Date.now();
        if (now - this.lastPollErrorLogAt > 60000) {
            console.warn(`[TELEGRAM] ${message}`);
            this.lastPollErrorLogAt = now;
        }
    }

    // === Outbox worker ====================================================
    async processOutbox() {
        if (this.stopped || this.busy.outbox) return;
        this.busy.outbox = true;
        try {
            const pending = await this.db.getPendingTelegramNotifications(MAX_MESSAGES_PER_RUN);
            for (const item of pending) {
                if (this.stopped) break;
                // Deliver to every bound chat for this tenant (fan-out design).
                const config = await this.db.getTelegramConfig(item.user_id);
                if (!config.enabled || !config.has_token || !config.linked_chats.length) {
                    // Integration was disarmed or unlinked after enqueue: discard
                    await this.db.failTelegramNotification(item.id, 'Integration disabled or no chat bindings');
                    continue;
                }
                const token = await this.resolveToken(item.user_id);
                if (!token) {
                    await this.db.failTelegramNotification(item.id, 'Unable to decrypt token');
                    continue;
                }
                let deliveredAny = false;
                let lastError = null;
                for (const binding of config.linked_chats) {
                    try {
                        const formatted = `🔔 <b>${toTelegramHtml(item.title)}</b>\n\n${toTelegramHtml(item.message)}`;
                        await this.sender(token, {
                            chat_id: String(binding.chat_id),
                            text: formatted,
                            parse_mode: 'HTML'
                        });
                        deliveredAny = true;
                        // Keep track of the first successful chat ID for the queue log
                        await this.db.markTelegramNotificationSent(item.id, binding.chat_id);
                    } catch (error) {
                        lastError = error;
                        this.throttledLog(`Notification ${item.id} delivery to chat ${binding.chat_id} failed: ${error.message}`);
                    }
                }
                if (!deliveredAny) {
                    const message = lastError ? lastError.message : 'no chats reached';
                    await this.db.failTelegramNotification(item.id, message);
                }
            }
        } catch (error) {
            console.error('[TELEGRAM] Outbox worker failed:', error.message);
        } finally {
            this.busy.outbox = false;
        }
    }

    // === Feed scanner =====================================================
    /**
     * Look up alerts, closed DCA cycles, and filled base orders that haven't
     * been notified yet. When found, push a notification into the outbound
     * queue and mark the source record notified.
     */
    async scanFeeds() {
        if (this.stopped || this.busy.feeds) return;
        this.busy.feeds = true;
        try {
            const users = await this.db.listTelegramRecipientUsers();
            for (const user of users) {
                if (this.stopped) break;
                // Keep source rows untouched until at least one owned chat is
                // linked. Linking later must not lose events in the meantime.
                if (!(await this.isDeliverable(user.user_id))) continue;
                // 1. Alerts (Deduplicated)
                const alerts = await this.db.getUnnotifiedAlertsForUser(user.user_id, !!user.is_admin, 10);
                if (alerts.length > 0) {
                    for (const alert of alerts) {
                        await this.db.enqueueTelegramNotification({
                            userId: user.user_id,
                            kind: alert.severity || 'WARNING',
                            sourceKey: `alert:${alert.id}`,
                            title: `ALERT: ${alert.severity}`,
                            message: `Alert: ${alert.message}\n(Kunci: ${alert.dedupe_key}, Occurrences: ${alert.occurrences})`
                        });
                    }
                    await this.db.markAlertsNotified(alerts.map(a => a.id));
                }

                // 2. Closed DCA Cycles (TP/SL)
                const cycles = await this.db.getUnnotifiedClosedCyclesForUser(user.user_id, 10);
                if (cycles.length > 0) {
                    for (const cycle of cycles) {
                        const statusEmoji = cycle.realized_profit >= 0 ? '📈' : '📉';
                        const sign = cycle.realized_profit >= 0 ? '+' : '';
                        const rate = cycle.realized_profit_percent ? cycle.realized_profit_percent.toFixed(2) : '0.00';
                        await this.db.enqueueTelegramNotification({
                            userId: user.user_id,
                            kind: 'DCA_CLOSE',
                            sourceKey: `cycle:${cycle.id}`,
                            title: `SIKLUS SELESAI: ${cycle.pair.toUpperCase()}`,
                            message: `${statusEmoji} Siklus DCA untuk *${cycle.pair.toUpperCase()}* ditutup.\n\n` +
                                `- Alasan: ${cycle.close_reason || 'SELESAI'}\n` +
                                `- Profit Realisasi: ${sign}${cycle.realized_profit.toLocaleString()} IDR (${sign}${rate}%)\n` +
                                `- Safety Order Terisi: ${cycle.safety_orders_filled}`
                        });
                    }
                    await this.db.markDcaCyclesNotified(cycles.map(c => c.id));
                }

                // 3. Base Order Fills (Initial trade entries)
                const fills = await this.db.getUnnotifiedBaseFillsForUser(user.user_id, 10);
                if (fills.length > 0) {
                    for (const fill of fills) {
                        await this.db.enqueueTelegramNotification({
                            userId: user.user_id,
                            kind: 'BASE_FILL',
                            sourceKey: `order:${fill.id}`,
                            title: `TRADE ENTRY: ${fill.pair.toUpperCase()}`,
                            message: `🛒 Base Order terisi untuk *${fill.pair.toUpperCase()}*.\n\n` +
                                `- Harga: ${fill.price.toLocaleString()} IDR\n` +
                                `- Jumlah: ${fill.amount}\n` +
                                `- Total: ${fill.amount_quote.toLocaleString()} IDR`
                        });
                    }
                    await this.db.markBaseFillOrdersNotified(fills.map(o => o.id));
                }

                // 4. Durable operational/trading events. These cover bot
                // lifecycle, SO/TP placement, SO fills, failures and the
                // anti-spam price/RSI signals emitted by each worker.
                const events = await this.db.getUnnotifiedBotEventsForUser(user.user_id, 50);
                if (events.length > 0) {
                    for (const event of events) {
                        const formatted = this.formatBotEvent(event);
                        await this.db.enqueueTelegramNotification({
                            userId: user.user_id,
                            kind: event.event,
                            sourceKey: `log:${event.id}`,
                            title: formatted.title,
                            message: formatted.message
                        });
                    }
                    await this.db.markBotLogsNotified(events.map(event => event.id));
                }

                await this.enqueueScheduledDigests(user.user_id, this.clock());
            }
        } catch (error) {
            console.error('[TELEGRAM] Feed scanner failed:', error.message);
        } finally {
            this.busy.feeds = false;
        }
    }

    formatBotEvent(event) {
        const pair = String(event.pair || '').toUpperCase();
        const bot = event.bot_name ? `${event.bot_name}${pair ? ` (${pair})` : ''}` : (pair || 'XBot');
        const mode = event.bot_id ? (event.dry_run ? 'DRY RUN' : 'REAL') : 'SYSTEM';
        const definitions = {
            BOT_START: ['🟢 BOT AKTIF', 'Bot mulai berjalan'],
            BOT_STOP: ['🔴 BOT BERHENTI', 'Bot berhenti'],
            BOT_PAUSE: ['⏸️ BOT DIJEDA', 'Bot dijeda'],
            BOT_ERROR: ['🚨 BOT ERROR', 'Worker bot mengalami error'],
            ORDER_PLACED: ['📝 ORDER DIPASANG', 'Order baru berhasil dipasang'],
            ORDER_CANCELLED: ['🚫 ORDER DIBATALKAN', 'Order dibatalkan'],
            ORDER_FAILED: ['❌ ORDER GAGAL', 'Order gagal'],
            DCA_ENTRY: ['🛒 SAFETY ORDER TERISI', 'Safety Order terisi'],
            STOP_LOSS: ['🛑 STOP LOSS', 'Stop loss dieksekusi'],
            API_ERROR: ['⚠️ GANGGUAN API', 'Terjadi gangguan API exchange'],
            CONFIG_CHANGE: ['⚙️ KONFIGURASI BERUBAH', 'Konfigurasi bot berubah'],
            PRICE_SIGNAL: ['💹 PERUBAHAN HARGA', 'Harga bergerak signifikan'],
            RSI_SIGNAL: ['📊 SINYAL RSI', 'RSI memasuki zona penting']
        };
        const [title, label] = definitions[event.event] || ['ℹ️ AKTIVITAS BOT', event.event];
        return {
            title: `${title}: ${pair || bot}`,
            message: `${label}\n\n- Bot: *${bot}*\n- Mode: ${mode}\n- Detail: ${event.message || '-'}`
        };
    }

    localTimeParts(now = new Date()) {
        const values = {};
        for (const part of new Intl.DateTimeFormat('en-CA', {
            timeZone: TELEGRAM_TIMEZONE,
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', hourCycle: 'h23', weekday: 'short'
        }).formatToParts(now)) {
            if (part.type !== 'literal') values[part.type] = part.value;
        }
        return {
            date: `${values.year}-${values.month}-${values.day}`,
            hour: Number(values.hour),
            weekday: values.weekday
        };
    }

    async buildFinancialSummary(userId, sinceIso = null) {
        const stats = await this.db.getTelegramUserSummary(userId, sinceIso);
        const accounts = await this.db.getUserAccounts(String(userId), true);
        let available = 0;
        let held = 0;
        const balanceErrors = [];
        for (const account of accounts) {
            try {
                const apiKey = this.db.decrypt(account.api_key_encrypted, account.id);
                const apiSecret = this.db.decrypt(account.api_secret_encrypted, account.id);
                const result = await new IndodaxClient(
                    apiKey, apiSecret, account.api_version || 'v1').get_balance();
                if (!result || result.error) throw new Error(result?.error || 'saldo tidak tersedia');
                available += Math.max(Number(result.balance?.idr) || 0, 0);
                held += Math.max(Number(result.balance_hold?.idr) || 0, 0);
            } catch (error) {
                balanceErrors.push(account.name);
            }
        }
        const closed = Number(stats.closed_cycles) || 0;
        const wins = Number(stats.wins) || 0;
        const profit = Number(stats.realized_profit) || 0;
        const lines = [
            `- Saldo IDR tersedia: Rp ${available.toLocaleString('id-ID')}`,
            `- Saldo IDR tertahan: Rp ${held.toLocaleString('id-ID')}`,
            `- Modal aktif: Rp ${(Number(stats.active_capital) || 0).toLocaleString('id-ID')}`,
            `- Profit realisasi: ${profit >= 0 ? '+' : ''}Rp ${profit.toLocaleString('id-ID')}`,
            `- Siklus selesai: ${closed} (${closed ? (wins / closed * 100).toFixed(1) : '0.0'}% menang)`,
            `- Bot aktif: ${Number(stats.running_bots) || 0}/${Number(stats.total_bots) || 0}`
        ];
        if (balanceErrors.length) {
            lines.push(`- Saldo gagal dibaca: ${balanceErrors.join(', ')}`);
        }
        return lines.join('\n');
    }

    async enqueueScheduledDigests(userId, now = new Date()) {
        const local = this.localTimeParts(now);
        if (local.hour !== DIGEST_HOUR) return;

        const periods = [{ kind: 'DAILY', key: local.date, days: 1, title: 'RINGKASAN HARIAN' }];
        if (local.weekday === 'Mon') {
            periods.push({ kind: 'WEEKLY', key: local.date, days: 7, title: 'RINGKASAN MINGGUAN' });
        }
        for (const period of periods) {
            if (await this.db.hasTelegramDigest(userId, period.kind, period.key)) continue;
            const since = new Date(now.getTime() - period.days * 86400000).toISOString();
            const summary = await this.buildFinancialSummary(userId, since);
            await this.db.enqueueTelegramNotification({
                userId,
                kind: `${period.kind}_DIGEST`,
                sourceKey: `digest:${period.kind}:${period.key}`,
                title: `📋 ${period.title}`,
                message: `${summary}\n\nZona waktu: ${TELEGRAM_TIMEZONE}`
            });
            await this.db.markTelegramDigest(userId, period.kind, period.key);
        }
    }


    // === Command receiver =================================================
    /**
     * Poll incoming Telegram events using getUpdates.
     * We poll on a per-user basis because tokens are user-owned.
     */
    async pollCommands() {
        if (this.stopped || this.busy.commands) return;
        this.busy.commands = true;
        try {
            const users = await this.db.listTelegramRecipientUsers();
            for (const user of users) {
                if (this.stopped) break;
                const token = await this.resolveToken(user.user_id);
                if (!token) continue;
                const offset = this.updatesOffsets.get(user.user_id) || 0;
                try {
                    const response = await axios.get(`${this.apiBase}/bot${token}/getUpdates`, {
                        params: { offset, timeout: 0 },
                        timeout: 5000
                    });
                    const updates = response.data?.result || [];
                    for (const update of updates) {
                        const nextOffset = update.update_id + 1;
                        this.updatesOffsets.set(user.user_id, nextOffset);
                        await this.handleUpdate(user.user_id, update);
                    }
                } catch (err) {
                    this.throttledLog(`Poll for user ${user.user_id} failed: ${err.message}`);
                }
            }
        } catch (error) {
            console.error('[TELEGRAM] Command poller failed:', error.message);
        } finally {
            this.busy.commands = false;
        }
    }
    async handleUpdate(userId, update) {
        const message = update.message;
        if (!message || !message.text) return;
        const text = message.text.trim();
        const command = text.split(/\s+/)[0].split('@')[0].toLowerCase();
        const chatId = message.chat.id;
        const senderInfo = { userId, chat_id: chatId };

        // 1. Linking chat command: /start KODE
        if (command === '/start') {
            const parts = text.split(/\s+/);
            if (parts.length > 1) {
                const code = parts[1].toUpperCase().trim();
                const binding = await this.db.consumeTelegramLinkCode(code, chatId);
                if (binding) {
                    await this.reply(senderInfo, '✅ Chat Telegram ini berhasil terhubung dengan akun XBot Anda. Anda akan menerima notifikasi trading di sini.');
                } else {
                    await this.reply(senderInfo, '❌ Kode link tidak valid, sudah digunakan, atau kedaluwarsa. Silakan generate kode baru dari dashboard web.');
                }
            } else {
                await this.reply(senderInfo, '🤖 Selamat datang di XBot! Hubungkan akun Anda melalui dashboard settings menggunakan tombol "Kode Link".');
            }
            return;
        }

        // 2. Read-only status command: /status
        if (command === '/status') {
            try {
                const config = await this.db.getTelegramConfig(userId);
                const isLinked = config.linked_chats.some(b => String(b.chat_id) === String(chatId));
                if (!isLinked) {
                    await this.reply(senderInfo, '⚠️ Chat ini belum terhubung ke akun Anda. Gunakan kode link dari dashboard settings.');
                    return;
                }
                const userAccounts = await new Promise((resolve, reject) => {
                    this.db.db.all('SELECT * FROM accounts WHERE user_id=?', [String(userId)], (e, r) => e ? reject(e) : resolve(r || []));
                });
                if (userAccounts.length === 0) {
                    await this.reply(senderInfo, '⚠️ Anda belum mendaftarkan API key bursa Indodax.');
                    return;
                }
                let summary = '📊 *STATUS BOT ANDA*\n\n';
                for (const account of userAccounts) {
                    const bots = await new Promise((resolve, reject) => {
                        this.db.db.all('SELECT * FROM bots WHERE account_id=?', [account.id], (e, r) => e ? reject(e) : resolve(r || []));
                    });
                    summary += `*Akun:* ${account.name} (${account.exchange})\n`;
                    if (bots.length === 0) {
                        summary += '  - Belum ada bot dibuat\n';
                    }
                    for (const bot of bots) {
                        const state = bot.status === 'RUNNING' ? '🟢 RUNNING' : '🔴 STOPPED';
                        const mode = bot.dry_run ? 'Dry Run' : 'REAL';
                        summary += `  - *[${bot.pair.toUpperCase()}]* ${bot.name}: ${state} (${mode})\n`;
                    }
                    summary += '\n';
                }
                const token = await this.resolveToken(userId);
                await this.sender(token, {
                    chat_id: String(chatId),
                    text: toTelegramHtml(summary),
                    parse_mode: 'HTML'
                });
            } catch (err) {
                await this.reply(senderInfo, `❌ Gagal mengambil status: ${err.message}`);
            }
            return;
        }

        if (['/balance', '/report', '/price'].includes(command)) {
            try {
                const config = await this.db.getTelegramConfig(userId);
                const isLinked = config.linked_chats.some(
                    binding => String(binding.chat_id) === String(chatId));
                if (!isLinked) {
                    await this.reply(senderInfo, '⚠️ Chat ini belum terhubung ke akun Anda. Gunakan kode link dari dashboard settings.');
                    return;
                }
                let responseText = '';
                if (command === '/balance') {
                    responseText = `💰 *SALDO AKUN*\n\n${await this.buildFinancialSummary(userId)}`;
                } else if (command === '/report') {
                    const since = new Date(Date.now() - 30 * 86400000).toISOString();
                    responseText = `📋 *LAPORAN 30 HARI*\n\n${await this.buildFinancialSummary(userId, since)}`;
                } else {
                    const bots = await new Promise((resolve, reject) => {
                        this.db.db.all(
                            `SELECT DISTINCT b.pair FROM bots b
                             JOIN accounts ac ON ac.id=b.account_id
                             WHERE ac.user_id=? ORDER BY b.pair`,
                            [String(userId)],
                            (error, rows) => error ? reject(error) : resolve(rows || [])
                        );
                    });
                    if (!bots.length) {
                        responseText = '⚠️ Belum ada pair bot yang dapat diperiksa.';
                    } else {
                        const client = new IndodaxClient('', '');
                        const lines = ['💹 *HARGA TERKINI*', ''];
                        for (const bot of bots) {
                            const ticker = await client.get_ticker(bot.pair);
                            lines.push(ticker && !ticker.error
                                ? `- ${bot.pair.toUpperCase()}: Rp ${(Number(ticker.last) || 0).toLocaleString('id-ID')}`
                                : `- ${bot.pair.toUpperCase()}: tidak tersedia`);
                        }
                        responseText = lines.join('\n');
                    }
                }
                const token = await this.resolveToken(userId);
                await this.sender(token, {
                    chat_id: String(chatId),
                    text: toTelegramHtml(responseText),
                    parse_mode: 'HTML'
                });
            } catch (error) {
                await this.reply(senderInfo, `❌ Gagal mengambil data: ${error.message}`);
            }
            return;
        }

        // 3. Fallback help command
        await this.reply(senderInfo,
            '🤖 Perintah tersedia:\n' +
            '- `/status` : Status semua bot\n' +
            '- `/balance` : Saldo dan profit\n' +
            '- `/price` : Harga pair bot terkini\n' +
            '- `/report` : Laporan performa 30 hari\n' +
            '- `/start` : Panduan aktivasi notifikasi');
    }

    // === Runtime interface ================================================
    start() {
        if (!this.stopped) return;
        this.stopped = false;
        console.log('[TELEGRAM] Starting notification service...');

        // Schedule loops
        const runLoop = (fn, interval) => {
            const timer = setInterval(() => fn.call(this), interval);
            this.timers.push(timer);
            // Execute once immediately
            setTimeout(() => { if (!this.stopped) fn.call(this); }, 500);
        };

        runLoop(this.processOutbox, DEFAULT_POLL_INTERVAL_MS);
        runLoop(this.scanFeeds, FEED_SCAN_INTERVAL_MS);
        runLoop(this.pollCommands, DEFAULT_POLL_INTERVAL_MS);
    }

    stop() {
        this.stopped = true;
        for (const timer of this.timers) {
            clearInterval(timer);
        }
        this.timers = [];
        console.log('[TELEGRAM] Notification service stopped.');
    }
}

module.exports = {
    TelegramService,
};
