#!/usr/bin/env node
'use strict';

const path = require('node:path');
const sqlite3 = require('sqlite3');
const { evaluateLiveRolloutPreflight } = require('../live-rollout-preflight');

function parseArguments(argv) {
    const index = argv.indexOf('--bot-id');
    if (index < 0 || !argv[index + 1]) {
        throw new Error('usage: live_rollout_preflight.js --bot-id BOT_ID [--database PATH]');
    }
    const databaseIndex = argv.indexOf('--database');
    return {
        botId: argv[index + 1],
        database: databaseIndex >= 0 && argv[databaseIndex + 1]
            ? argv[databaseIndex + 1]
            : (process.env.DB_PATH || 'data/dca_bot.db')
    };
}

function openReadOnly(databasePath) {
    return new Promise((resolve, reject) => {
        const db = new sqlite3.Database(
            path.resolve(databasePath), sqlite3.OPEN_READONLY,
            error => error ? reject(error) : resolve(db));
    });
}

function get(db, sql, parameters) {
    return new Promise((resolve, reject) => db.get(
        sql, parameters,
        (error, row) => error ? reject(error) : resolve(row)));
}

async function loadSnapshot(db, botId) {
    const bot = await get(db, `
        SELECT b.id, b.status, b.dry_run, b.strategy_id,
               a.is_active AS account_active
        FROM bots b JOIN accounts a ON a.id=b.account_id
        WHERE b.id=?`, [botId]);
    const strategy = bot?.strategy_id
        ? await get(db, 'SELECT * FROM strategies WHERE id=?', [bot.strategy_id])
        : null;
    const cycles = await get(db, `
        SELECT COUNT(*) AS count FROM dca_cycles
        WHERE bot_id=? AND dry_run=1 AND status='CLOSED'
          AND close_reason IN ('TAKE_PROFIT', 'STOP_LOSS')
          AND closed_at IS NOT NULL AND exit_price>0 AND total_amount>0`, [botId]);
    const positions = await get(db, `
        SELECT COUNT(*) AS count FROM positions
        WHERE bot_id=? AND status IN ('OPEN', 'PENDING_BASE')`, [botId]);
    const orders = await get(db, `
        SELECT COUNT(*) AS count FROM orders
        WHERE bot_id=? AND status IN
          ('REQUESTED', 'SUBMISSION_UNKNOWN', 'OPEN', 'PENDING', 'PARTIALLY_FILLED')`,
    [botId]);
    return {
        bot,
        strategy,
        completedDryRunCycles: Number(cycles?.count) || 0,
        activePositions: Number(positions?.count) || 0,
        recoverableOrders: Number(orders?.count) || 0
    };
}

async function main() {
    const args = parseArguments(process.argv.slice(2));
    const db = await openReadOnly(args.database);
    try {
        const snapshot = await loadSnapshot(db, args.botId);
        const result = evaluateLiveRolloutPreflight(snapshot, process.env);
        process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
        process.exitCode = result.allowed ? 0 : 2;
    } finally {
        db.close();
    }
}

main().catch(error => {
    process.stderr.write(`Live rollout preflight gagal: ${error.message}\n`);
    process.exitCode = 1;
});
