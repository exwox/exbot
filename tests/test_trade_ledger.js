const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'exbot-ledger-'));
process.env.DB_PATH = path.join(tempDir, 'ledger.db');
const Database = require('../database');

const db = new Database();
db.init();

const run = (sql, params = []) => new Promise((resolve, reject) => {
    db.db.run(sql, params, function (error) {
        if (error) reject(error);
        else resolve(this);
    });
});

(async () => {
    await new Promise(resolve => setTimeout(resolve, 300));
    const now = new Date().toISOString();
    await run(
        `INSERT INTO users
         (id, username, email, password_hash, salt, is_active, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        ['user_test', 'user_test', 'test@example.com', 'hash', 'salt', 1, now, now]
    );
    await run(
        `INSERT INTO accounts
         (id, user_id, name, exchange, api_key_encrypted, api_secret_encrypted,
          is_active, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        ['account_test', 'user_test', 'Test', 'Indodax', '', '', 1, now, now]
    );
    await run(
        `INSERT INTO bots
         (id, account_id, name, exchange, pair, status, dry_run, strategy_id,
          created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        ['bot_test', 'account_test', 'Test Bot', 'Indodax', 'btcidr',
            'RUNNING', 1, null, now, now]
    );
    await db.addOrder({
        id: 'intent_test',
        bot_id: 'bot_test',
        account_id: 'account_test',
        position_id: 'position_test',
        exchange_order_id: '',
        client_order_id: 'xb_test_client_order',
        order_type: 'base_market',
        side: 'buy',
        pair: 'btcidr',
        price: 100,
        amount: 1,
        amount_quote: 100,
        status: 'REQUESTED',
        is_dca: true,
        dca_level: 0,
        so_number: 0
    });
    const recoverableOrders = await db.getOpenOrders('bot_test');
    assert.strictEqual(recoverableOrders.length, 1);
    assert.strictEqual(recoverableOrders[0].position_id, 'position_test');
    assert.strictEqual(
        recoverableOrders[0].client_order_id, 'xb_test_client_order'
    );
    await assert.rejects(
        () => db.addOrder({
            id: 'intent_duplicate',
            bot_id: 'bot_test',
            account_id: 'account_test',
            position_id: 'position_test',
            exchange_order_id: '',
            client_order_id: 'xb_test_client_order',
            order_type: 'base_market',
            side: 'buy',
            pair: 'btcidr',
            price: 100,
            amount: 1,
            amount_quote: 100,
            status: 'REQUESTED',
            is_dca: true,
            dca_level: 0,
            so_number: 0
        }),
        error => error.code === 'SQLITE_CONSTRAINT'
    );
    await db.updateOrderStatus('intent_test', 'CANCELLED');
    await run("UPDATE bots SET dry_run=0 WHERE id='bot_test'");
    await db.savePosition({
        id: 'position_exposure',
        bot_id: 'bot_test',
        status: 'OPEN',
        base_price: 100,
        average_entry_price: 100,
        base_amount: 1,
        total_amount: 1,
        sold_amount: 0,
        total_invested: 100,
        reserved_capital: 25000,
        take_profit_price: 110,
        stop_loss_price: 0,
        current_price: 100,
        so_entries: [],
        tp_order_id: null,
        exit_order_id: null,
        exit_reason: '',
        open_orders: []
    });
    assert.strictEqual(await db.getAccountExposure('account_test'), 25000);
    await db.addLog(
        'account_test', 'ERROR', 'API_ERROR',
        'api_key=VISIBLE Authorization: Bearer bearer-value',
        'bot_test', { session_token: 'SESSION-VALUE' }
    );
    const safeLog = (await db.getLogs('account_test'))[0];
    const serializedLog = `${safeLog.message} ${safeLog.metadata}`;
    for (const secret of ['VISIBLE', 'bearer-value', 'SESSION-VALUE']) {
        assert.equal(serializedLog.includes(secret), false);
    }
    assert.equal(JSON.parse(safeLog.metadata).session_token, '[REDACTED]');
    const alertId = await db.raiseAlert({
        account_id: 'account_test',
        bot_id: 'bot_test',
        severity: 'ERROR',
        kind: 'EXCHANGE_CIRCUIT_OPEN',
        dedupe_key: 'circuit:bot_test:ticker',
        message: 'api_key=VISIBLE alert',
        metadata: { session_token: 'SESSION-VALUE' }
    });
    await db.raiseAlert({
        account_id: 'account_test',
        bot_id: 'bot_test',
        severity: 'ERROR',
        kind: 'EXCHANGE_CIRCUIT_OPEN',
        dedupe_key: 'circuit:bot_test:ticker',
        message: 'api_secret=VISIBLE_AGAIN alert'
    });
    const alerts = await db.getUserAlerts('user_test');
    assert.strictEqual(alerts.length, 1);
    assert.strictEqual(alerts[0].id, alertId);
    assert.strictEqual(alerts[0].occurrences, 2);
    assert.equal(alerts[0].message.includes('VISIBLE_AGAIN'), false);
    assert.strictEqual((await db.getUserAlerts('other_user')).length, 0);
    assert.strictEqual(await db.acknowledgeAlert(alertId, 'other_user'), false);
    assert.strictEqual(await db.acknowledgeAlert(alertId, 'user_test'), true);
    assert.strictEqual((await db.getUserAlerts('user_test')).length, 0);
    await db.raiseAlert({
        account_id: 'account_test',
        bot_id: 'bot_test',
        kind: 'EXCHANGE_CIRCUIT_OPEN',
        dedupe_key: 'circuit:bot_test:ticker',
        message: 'Circuit reopened'
    });
    assert.strictEqual((await db.getUserAlerts('user_test'))[0].occurrences, 3);
    assert.strictEqual(await db.resolveAlert('circuit:bot_test:ticker'), true);

    await db.recordRuntimeStart('node-test', 300, 3);
    await db.recordRuntimeStart('node-test', 300, 3);
    await db.recordRuntimeStart('node-test', 300, 3);
    const systemAlerts = await db.getUserAlerts('user_test', 'OPEN', 100, true);
    const restartAlert = systemAlerts.find(alert => alert.kind === 'RESTART_LOOP');
    assert.ok(restartAlert);
    assert.strictEqual(await db.acknowledgeAlert(restartAlert.id, 'user_test'), false);
    assert.strictEqual(
        await db.acknowledgeAlert(restartAlert.id, 'user_test', true), true);
    await run(
        `INSERT INTO trades
         (id, account_id, bot_id, position_id, order_id, pair, side,
          trade_type, price, amount, amount_quote, fee, cost_basis,
          realized_profit, realized_profit_percent, close_reason, dry_run,
          executed_at, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        ['trade_test', 'account_test', 'bot_test', 'position_test', 'order_test',
            'btcidr', 'sell', 'take_profit', 110, 1, 109, 1, 100, 9, 9,
            'TAKE_PROFIT', 1, now, now]
    );
    await run(
        `INSERT INTO dca_cycles
         (id, account_id, bot_id, pair, status, dry_run, base_price,
          average_entry_price, exit_price, total_invested, total_amount,
          gross_exit_value, net_exit_value, total_fees, safety_orders_filled,
          realized_profit, realized_profit_percent, close_reason, started_at,
          closed_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        ['position_test', 'account_test', 'bot_test', 'btcidr', 'CLOSED', 1,
            100, 100, 110, 100, 1, 110, 109, 1, 0, 9, 9,
            'TAKE_PROFIT', now, now, now]
    );

    const trades = await db.getBotTrades('bot_test');
    const stats = await db.getBotTradeStats('bot_test');
    const cycleStats = await db.getBotCycleStats('bot_test');
    assert.strictEqual(trades.length, 1);
    assert.strictEqual(trades[0].trade_type, 'take_profit');
    assert.strictEqual(stats.realized_profit, 9);
    assert.strictEqual(stats.completed_cycles, 1);
    assert.strictEqual(cycleStats.closed_cycles, 1);
    assert.strictEqual(cycleStats.realized_profit, 9);
    assert.strictEqual(await db.getCompletedDryRunCycleCount('bot_test'), 1);
    await run(
        `INSERT INTO dca_cycles
         (id, account_id, bot_id, pair, status, dry_run, base_price,
          exit_price, total_invested, total_amount, close_reason,
          started_at, closed_at, updated_at)
         VALUES (?, ?, ?, ?, 'CLOSED', 1, ?, 0, ?, ?,
                 'MANUALLY_RESET', ?, ?, ?)`,
        ['manual_reset_cycle', 'account_test', 'bot_test', 'btcidr',
            100, 100, 1, now, now, now]
    );
    assert.strictEqual(await db.getCompletedDryRunCycleCount('bot_test'), 1);
    console.log('Node trade ledger: OK');

    await assert.rejects(
        () => db.resetUserData('user_test'),
        error => error.code === 'BOTS_NOT_SAFE_TO_RESET'
    );
    await run("UPDATE bots SET status='STOPPED' WHERE id='bot_test'");
    const deleted = await db.resetUserData('user_test');
    assert.strictEqual(deleted.trades, 1);
    assert.strictEqual(deleted.cycles, 2);
    assert.strictEqual(deleted.bots, 1);
    assert.strictEqual(deleted.accounts, 1);
    const userStillExists = await new Promise((resolve, reject) => {
        db.db.get(
            'SELECT COUNT(*) AS count FROM users WHERE id=?',
            ['user_test'],
            (error, row) => error ? reject(error) : resolve(row.count)
        );
    });
    assert.strictEqual(userStillExists, 1);
    console.log('User-scoped reset: OK');
    db.db.close();
})().catch(error => {
    console.error(error);
    if (db.db) db.db.close();
    process.exitCode = 1;
});
