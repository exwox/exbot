const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'exbot-telegram-'));
process.env.DB_PATH = path.join(tempDir, 'telegram.db');
process.env.ENCRYPTION_KEY = 'test-master-encryption-key-must-be-long';

const Database = require('../database');
const { TelegramService } = require('../telegram-service');

const db = new Database();
db.setEncryptionKey(process.env.ENCRYPTION_KEY);
db.init();

const run = (sql, params = []) => new Promise((resolve, reject) => {
    db.db.run(sql, params, function (error) {
        if (error) reject(error);
        else resolve(this);
    });
});

(async () => {
    console.log('🧪 Running Telegram Integration Tests...');
    await new Promise(resolve => setTimeout(resolve, 300));
    const now = new Date().toISOString();

    // Setup Test User
    await run(
        `INSERT INTO users
         (id, username, email, password_hash, salt, is_active, is_admin, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        ['user_test', 'user_test', 'test@example.com', 'hash', 'salt', 1, 1, now, now]
    );

    // 1. Test Config CRUD and encryption
    console.log('  - Testing config save & encryption...');
    const validToken = '12345678:AAGekW9W3N6Q84rJpS9n201-9a2kL90N1d2';
    const config1 = await db.setTelegramConfig('user_test', {
        botToken: validToken,
        enabled: true
    });
    assert.strictEqual(config1.enabled, true);
    assert.strictEqual(config1.has_token, true);

    const decryptedToken = await db.getTelegramBotToken('user_test');
    assert.strictEqual(decryptedToken, validToken);

    // Test invalid token format validation
    await assert.rejects(
        () => db.setTelegramConfig('user_test', { botToken: 'badtoken' }),
        /Format token Telegram tidak valid/
    );

    // 2. Test Link Codes Lifecycle
    console.log('  - Testing link codes (generate, peek, consume)...');
    const { code, expires_at } = await db.createTelegramLinkCode('user_test');
    assert.ok(code);
    assert.strictEqual(code.length, 8);

    const peeked = await db.peekTelegramLinkCode(code, 'user_test');
    assert.ok(peeked);
    assert.strictEqual(peeked.status, 'PENDING');

    // Peek with wrong user should be null (ownership gate check)
    const peekedWrongUser = await db.peekTelegramLinkCode(code, 'user_other');
    assert.strictEqual(peekedWrongUser, null);

    // Consume the link code
    const consumed = await db.consumeTelegramLinkCode(code, '123456789');
    assert.ok(consumed);
    assert.strictEqual(consumed.user_id, 'user_test');
    assert.strictEqual(consumed.chat_id, '123456789');

    // Trying to consume again should fail
    const consumedAgain = await db.consumeTelegramLinkCode(code, '123456789');
    assert.strictEqual(consumedAgain, null);

    // A bearer link code can only be consumed once, even when two polling
    // loops race on the same update.
    const racedCode = await db.createTelegramLinkCode('user_test');
    const raced = await Promise.all([
        db.consumeTelegramLinkCode(racedCode.code, 'chat-a'),
        db.consumeTelegramLinkCode(racedCode.code, 'chat-b')
    ]);
    assert.strictEqual(raced.filter(Boolean).length, 1);

    const updatedConfig = await db.getTelegramConfig('user_test');
    assert.strictEqual(updatedConfig.chat_id, '123456789');
    // 3. Test Queue and Outbox processing
    console.log('  - Testing notification queue & TelegramService delivery...');
    const notificationId = await db.enqueueTelegramNotification({
        userId: 'user_test',
        kind: 'INFO',
        title: 'UNIT TEST',
        message: 'Hello World'
    });
    assert.ok(notificationId);

    const pending = await db.getPendingTelegramNotifications(5);
    assert.strictEqual(pending.length, 1);
    assert.strictEqual(pending[0].title, 'UNIT TEST');

    // Setup TelegramService with a mock sender to capture payloads
    const sentPayloads = [];
    const mockSender = async (token, payload) => {
        sentPayloads.push({ token, payload });
        return { data: { ok: true } };
    };

    const telegramService = new TelegramService(db, {
        sender: mockSender,
        clock: () => new Date('2026-08-27T05:00:00.000Z')
    });
    telegramService.stopped = false;

    // Run a single process cycle
    await telegramService.processOutbox();

    assert.strictEqual(sentPayloads.length, updatedConfig.linked_chats.length);
    assert.ok(sentPayloads.every(item => item.token === validToken));
    assert.ok(sentPayloads.some(item => item.payload.chat_id === '123456789'));
    assert.ok(sentPayloads.every(item => item.payload.text.includes('UNIT TEST')));

    // The notification should now be marked as SENT
    const pendingAfter = await db.getPendingTelegramNotifications(5);
    assert.strictEqual(pendingAfter.length, 0);

    // 4. Test Delivery Failures & Retries
    console.log('  - Testing delivery failure and retry limit...');
    const failNotificationId = await db.enqueueTelegramNotification({
        userId: 'user_test',
        kind: 'INFO',
        title: 'FAIL TEST',
        message: 'Should Fail'
    });

    const failingService = new TelegramService(db, {
        sender: async () => {
            throw new Error('API Timeout');
        }
    });
    failingService.stopped = false;

    // Run 5 failed attempts
    for (let i = 0; i < 5; i++) {
        await failingService.processOutbox();
    }

    // Now the status should become FAILED
    const rows = await new Promise((resolve, reject) => {
        db.db.all('SELECT * FROM telegram_notifications WHERE id=?', [failNotificationId], (e, r) => e ? reject(e) : resolve(r));
    });
    assert.strictEqual(rows[0].status, 'FAILED');
    assert.strictEqual(rows[0].attempts, 5);
    assert.strictEqual(rows[0].last_error, 'API Timeout');

    // 5. Test Feed Scanner integration
    console.log('  - Testing feed scanner (alerts & DCA closures)...');
    // Enqueue an alert to trigger scanning
    await run(
        `INSERT INTO alerts
         (account_id, bot_id, severity, kind, dedupe_key, status, message, occurrences, first_seen_at, last_seen_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [null, null, 'ERROR', 'CIRCUIT', 'test-dedupe-key', 'OPEN', 'Exchange rate-limit hit', 1, now, now]
    );
    await run(
        `INSERT INTO accounts
         (id, user_id, name, exchange, api_key_encrypted, api_secret_encrypted,
          is_active, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        ['acc_telegram', 'user_test', 'Akun Telegram', 'Indodax', '', '', 1, now, now]
    );
    await run(
        `INSERT INTO bots
         (id, account_id, name, exchange, pair, status, dry_run, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        ['bot_telegram', 'acc_telegram', 'BTC DCA', 'Indodax', 'btcidr', 'RUNNING', 1, now, now]
    );
    await run(
        `INSERT INTO bot_logs
         (account_id, bot_id, level, event, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)`,
        ['acc_telegram', 'bot_telegram', 'INFO', 'ORDER_PLACED',
            '[DRY RUN] TP order: 0.001 @ Rp 1,000,000', now]
    );
    await run(
        `INSERT INTO bot_logs
         (account_id, bot_id, level, event, message, created_at)
         VALUES (?, ?, ?, ?, ?, ?)`,
        ['acc_telegram', 'bot_telegram', 'INFO', 'RSI_SIGNAL',
            'RSI BTCIDR memasuki OVERSOLD: 29.50', now]
    );

    // Run scanFeeds
    await telegramService.scanFeeds();

    // The scan should have picked up the alert, enqueued a notification, and marked it notified
    const scanPending = await db.getPendingTelegramNotifications(5);
    assert.strictEqual(scanPending.length, 3);
    assert.ok(scanPending.some(item => item.message.includes('Exchange rate-limit hit')));
    assert.ok(scanPending.some(item => item.title.includes('ORDER DIPASANG')));
    assert.ok(scanPending.some(item => item.title.includes('SINYAL RSI')));

    // Source records are marked after being handed to the durable queue, so
    // the next scan cannot enqueue duplicates.
    await telegramService.scanFeeds();
    const scanPendingAgain = await db.getPendingTelegramNotifications(10);
    assert.strictEqual(scanPendingAgain.length, 3);

    // Read-only financial commands are available to linked chats.
    await telegramService.handleUpdate('user_test', {
        message: { chat: { id: '123456789' }, text: '/balance' }
    });
    assert.ok(sentPayloads.some(item => item.payload.text.includes('SALDO AKUN')));

    // At the configured local hour a daily digest is queued, plus a weekly
    // digest on Monday. The durable marker makes repeated scans idempotent.
    const mondayMorningJakarta = new Date('2026-08-31T01:05:00.000Z');
    await telegramService.enqueueScheduledDigests('user_test', mondayMorningJakarta);
    await telegramService.enqueueScheduledDigests('user_test', mondayMorningJakarta);
    const withDigests = await db.getPendingTelegramNotifications(10);
    assert.strictEqual(withDigests.length, 5);
    assert.ok(withDigests.some(item => item.kind === 'DAILY_DIGEST'));
    assert.ok(withDigests.some(item => item.kind === 'WEEKLY_DIGEST'));

    const firstDeduplicated = await db.enqueueTelegramNotification({
        userId: 'user_test', kind: 'INFO', title: 'UNIK', message: 'sekali',
        sourceKey: 'unit:source-key'
    });
    const secondDeduplicated = await db.enqueueTelegramNotification({
        userId: 'user_test', kind: 'INFO', title: 'UNIK', message: 'sekali',
        sourceKey: 'unit:source-key'
    });
    assert.ok(firstDeduplicated);
    assert.strictEqual(secondDeduplicated, null);

    console.log('✅ All Telegram Integration Tests Passed!');
    db.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
})().catch(err => {
    console.error('❌ Test failed:', err);
    process.exit(1);
});
