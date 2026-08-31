const assert = require('node:assert/strict');
const { after, test } = require('node:test');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const sqlite3 = require('sqlite3');

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'xbot-auth-'));
const databasePath = path.join(tempDir, 'auth.db');
const heartbeatPath = path.join(tempDir, 'manager-heartbeat');
const port = 22000 + Math.floor(Math.random() * 10000);
const baseUrl = `http://127.0.0.1:${port}`;
let server;

function startServer() {
    fs.writeFileSync(heartbeatPath, new Date().toISOString());
    server = spawn(process.execPath, ['dashboard.js'], {
        cwd: path.resolve(__dirname, '..'),
        env: {
            ...process.env,
            PORT: String(port),
            DASHBOARD_HOST: '0.0.0.0',
            DB_PATH: databasePath,
            DATABASE_PATH: databasePath,
            ENCRYPTION_KEY: 'integration-test-master-key-32-bytes',
            MANAGER_HEARTBEAT_PATH: heartbeatPath,
            NODE_ENV: 'test',
        },
        stdio: ['ignore', 'ignore', 'pipe'],
    });
    return server;
}

async function waitForServer() {
    let lastError;
    for (let attempt = 0; attempt < 50; attempt += 1) {
        try {
            const response = await fetch(`${baseUrl}/login`, { redirect: 'manual' });
            if (response.status < 500) return;
        } catch (error) {
            lastError = error;
        }
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw lastError || new Error('Dashboard did not start');
}

async function stopServer() {
    if (!server || server.exitCode !== null) return;
    server.kill('SIGTERM');
    await new Promise(resolve => server.once('exit', resolve));
}

function activateUser(username) {
    return new Promise((resolve, reject) => {
        const connection = new sqlite3.Database(databasePath);
        connection.run('UPDATE users SET is_active=1 WHERE username=?', [username], error => {
            connection.close();
            error ? reject(error) : resolve();
        });
    });
}

function insertAccountAlert(accountId) {
    return new Promise((resolve, reject) => {
        const connection = new sqlite3.Database(databasePath);
        const now = new Date().toISOString();
        connection.run(
            `INSERT INTO alerts
             (account_id, severity, kind, dedupe_key, status, message,
              occurrences, first_seen_at, last_seen_at)
             VALUES (?, 'ERROR', 'INTEGRATION_TEST', ?, 'OPEN', ?, 1, ?, ?)`,
            [accountId, `integration:${accountId}`, 'Tenant-scoped alert', now, now],
            function (error) {
                const alertId = this.lastID;
                connection.close();
                error ? reject(error) : resolve(alertId);
            }
        );
    });
}

function insertEmergencyState(accountId, botId) {
    return new Promise((resolve, reject) => {
        const connection = new sqlite3.Database(databasePath);
        const now = new Date().toISOString();
        connection.serialize(() => {
            connection.run(
                `INSERT INTO positions
                 (id, bot_id, status, base_price, average_entry_price,
                  base_amount, total_amount, total_invested, current_price,
                  open_orders, tp_order_id, created_at, updated_at)
                 VALUES (?, ?, 'OPEN', 10000, 10000, 1, 1, 10000, 10000,
                         ?, ?, ?, ?)`,
                [`emergency-pos-${botId}`, botId,
                    JSON.stringify([{ order_id: `dry-so-${botId}`, so_number: 1 }]),
                    `dry-tp-${botId}`, now, now]
            );
            connection.run(
                `INSERT INTO dca_cycles
                 (id, account_id, bot_id, pair, status, dry_run,
                  started_at, updated_at)
                 VALUES (?, ?, ?, 'btcidr', 'OPEN', 1, ?, ?)`,
                [`emergency-pos-${botId}`, accountId, botId, now, now]
            );
            connection.run(
                `INSERT INTO orders
                 (id, bot_id, account_id, position_id, exchange_order_id,
                  order_type, side, pair, status, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, 'so_1', 'buy', 'btcidr', 'OPEN', ?, ?)`,
                [`emergency-order-${botId}`, botId, accountId,
                    `emergency-pos-${botId}`, `dry-so-${botId}`, now, now],
                error => {
                    connection.close();
                    error ? reject(error) : resolve();
                }
            );
        });
    });
}

function readEmergencyState(botId) {
    return new Promise((resolve, reject) => {
        const connection = new sqlite3.Database(databasePath);
        connection.get(
            `SELECT
               (SELECT status FROM positions WHERE bot_id=? ORDER BY created_at DESC LIMIT 1) AS position_status,
               (SELECT status FROM dca_cycles WHERE bot_id=? ORDER BY started_at DESC LIMIT 1) AS cycle_status,
               (SELECT status FROM orders WHERE bot_id=? ORDER BY created_at DESC LIMIT 1) AS order_status`,
            [botId, botId, botId],
            (error, row) => {
                connection.close();
                error ? reject(error) : resolve(row);
            }
        );
    });
}

async function registerAndLogin(username, email) {
    const password = 'long-password-value';
    const registration = await fetch(`${baseUrl}/api/auth/register`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ username, email, password }),
    });
    assert.equal((await registration.json()).success, true);
    await activateUser(username);
    const login = await fetch(`${baseUrl}/api/auth/login`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ username, password }),
    });
    const body = await login.json();
    assert.equal(body.success, true);
    return { body, cookie: login.headers.get('set-cookie') };
}

after(async () => {
    await stopServer();
    fs.rmSync(tempDir, { recursive: true, force: true });
});

test('authentication is tenant-safe and survives a dashboard restart', async () => {
    startServer();
    await waitForServer();

    const anonymous = await fetch(`${baseUrl}/`, { redirect: 'manual' });
    assert.equal(anonymous.status, 302);
    assert.equal(anonymous.headers.get('location'), '/login');

    const liveness = await fetch(`${baseUrl}/healthz`);
    assert.equal(liveness.status, 200);
    assert.equal((await liveness.json()).status, 'ok');
    const readiness = await fetch(`${baseUrl}/readyz`);
    assert.equal(readiness.status, 200);
    assert.equal((await readiness.json()).bot_manager, 'ok');
    const stale = new Date(Date.now() - 30000);
    fs.utimesSync(heartbeatPath, stale, stale);
    assert.equal((await fetch(`${baseUrl}/readyz`)).status, 503);
    fs.writeFileSync(heartbeatPath, new Date().toISOString());

    const weak = await fetch(`${baseUrl}/api/auth/register`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ username: 'secureuser', email: 'secure@example.com', password: 'short' }),
    });
    assert.equal(weak.status, 400);

    const { body: loginBody, cookie } = await registerAndLogin('secureuser', 'secure@example.com');
    assert.equal(loginBody.success, true);
    assert.equal(Object.hasOwn(loginBody, 'session_token'), false);
    assert.match(cookie, /^xbot_session=/);
    assert.match(cookie, /HttpOnly/i);
    assert.match(cookie, /SameSite=Strict/i);

    const authenticated = await fetch(`${baseUrl}/`, { headers: { cookie }, redirect: 'manual' });
    assert.equal(authenticated.status, 200);

    const queryToken = await fetch(`${baseUrl}/?session_token=fake`, { redirect: 'manual' });
    assert.equal(queryToken.status, 302);

    const createAccount = await fetch(`${baseUrl}/api/accounts`, {
        method: 'POST', headers: { cookie, 'content-type': 'application/json' },
        body: JSON.stringify({ name: 'Primary', api_key: 'API-KEY-SECRET', api_secret: 'API-SECRET-VALUE' }),
    });
    const accountBody = await createAccount.json();
    assert.equal(accountBody.success, true);
    const accountId = accountBody.data.id;
    const ownerAccounts = await (await fetch(`${baseUrl}/api/accounts`, { headers: { cookie } })).json();
    assert.equal(ownerAccounts.data.length, 1);
    assert.equal(Object.hasOwn(ownerAccounts.data[0], 'api_key_encrypted'), false);
    assert.notEqual(ownerAccounts.data[0].api_key_masked, 'API-KEY-SECRET');
    const ownerExposure = await fetch(
        `${baseUrl}/api/accounts/${encodeURIComponent(accountId)}/exposure`,
        { headers: { cookie } }
    );
    assert.equal(ownerExposure.status, 200);
    const ownerExposureBody = await ownerExposure.json();
    assert.equal(ownerExposureBody.data.reserved_exposure_idr, 0);
    assert.equal(ownerExposureBody.data.enforced, false);

    const liveReadiness = await (await fetch(
        `${baseUrl}/api/live-readiness`, { headers: { cookie } })).json();
    assert.equal(liveReadiness.data.allowed, false);
    assert.equal(liveReadiness.data.exposure_limit_idr, 0);
    const blockedLiveBot = await fetch(`${baseUrl}/api/bots`, {
        method: 'POST', headers: { cookie, 'content-type': 'application/json' },
        body: JSON.stringify({
            account_id: accountId, name: 'Blocked Live Bot', pair: 'btcidr',
            dry_run: false
        }),
    });
    assert.equal(blockedLiveBot.status, 409);
    assert.equal((await blockedLiveBot.json()).success, false);
    const dryBotResponse = await fetch(`${baseUrl}/api/bots`, {
        method: 'POST', headers: { cookie, 'content-type': 'application/json' },
        body: JSON.stringify({
            account_id: accountId, name: 'Safe Dry Bot', pair: 'btcidr',
            dry_run: true
        }),
    });
    const dryBot = await dryBotResponse.json();
    assert.equal(dryBot.success, true, JSON.stringify(dryBot));
    await insertEmergencyState(accountId, dryBot.data.id);
    const startDryBot = await fetch(`${baseUrl}/api/bots/${dryBot.data.id}/start`, {
        method: 'POST', headers: { cookie }
    });
    assert.equal(startDryBot.status, 200);
    const unsafeReset = await fetch(`${baseUrl}/api/bots/${dryBot.data.id}/reset-position`, {
        method: 'POST', headers: { cookie }
    });
    assert.equal(unsafeReset.status, 409);
    assert.equal((await readEmergencyState(dryBot.data.id)).position_status, 'OPEN');

    const stopDryBot = await fetch(`${baseUrl}/api/bots/${dryBot.data.id}/stop`, {
        method: 'POST', headers: { cookie }
    });
    assert.equal(stopDryBot.status, 200);
    const stoppedState = await readEmergencyState(dryBot.data.id);
    assert.equal(stoppedState.position_status, 'OPEN');
    assert.equal(stoppedState.cycle_status, 'OPEN');
    assert.equal(stoppedState.order_status, 'CANCELLED');

    const safeReset = await fetch(`${baseUrl}/api/bots/${dryBot.data.id}/reset-position`, {
        method: 'POST', headers: { cookie }
    });
    assert.equal(safeReset.status, 200);
    const resetState = await readEmergencyState(dryBot.data.id);
    assert.equal(resetState.position_status, 'MANUALLY_RESET');
    assert.equal(resetState.cycle_status, 'CLOSED');
    assert.equal(resetState.order_status, 'CANCELLED');
    const botReadiness = await (await fetch(
        `${baseUrl}/api/live-readiness?bot_id=${encodeURIComponent(dryBot.data.id)}`,
        { headers: { cookie } })).json();
    assert.equal(botReadiness.data.allowed, false);
    // Allowlist bot sudah dihapus; gate tetap tertutup karena flag master off.
    assert.equal(botReadiness.data.bot_allowed, true);
    assert.equal(botReadiness.data.completed_dry_run_cycles, 0);
    assert.equal(botReadiness.data.strategy_risk_ready, false);
    const blockedModeChange = await fetch(`${baseUrl}/api/bots/${dryBot.data.id}`, {
        method: 'PUT', headers: { cookie, 'content-type': 'application/json' },
        body: JSON.stringify({ dry_run: false }),
    });
    assert.equal(blockedModeChange.status, 403);

    const second = await registerAndLogin('seconduser', 'second@example.com');
    const secondAccounts = await (await fetch(`${baseUrl}/api/accounts`, { headers: { cookie: second.cookie } })).json();
    assert.equal(secondAccounts.data.length, 0);
    const crossTenant = await fetch(`${baseUrl}/api/accounts/${encodeURIComponent(accountId)}`, {
        method: 'PUT', headers: { cookie: second.cookie, 'content-type': 'application/json' },
        body: JSON.stringify({ name: 'Stolen' }),
    });
    assert.equal(crossTenant.status, 404);
    const crossTenantExposure = await fetch(
        `${baseUrl}/api/accounts/${encodeURIComponent(accountId)}/exposure`,
        { headers: { cookie: second.cookie } }
    );
    assert.equal(crossTenantExposure.status, 404);
    const crossTenantReadiness = await fetch(
        `${baseUrl}/api/live-readiness?bot_id=${encodeURIComponent(dryBot.data.id)}`,
        { headers: { cookie: second.cookie } }
    );
    assert.equal(crossTenantReadiness.status, 404);

    const alertId = await insertAccountAlert(accountId);
    const ownerAlerts = await (await fetch(
        `${baseUrl}/api/alerts`, { headers: { cookie } })).json();
    assert.equal(ownerAlerts.success, true);
    assert.equal(ownerAlerts.data.some(alert => alert.id === alertId), true);
    const secondAlerts = await (await fetch(
        `${baseUrl}/api/alerts`, { headers: { cookie: second.cookie } })).json();
    assert.equal(secondAlerts.data.some(alert => alert.id === alertId), false);
    const forbiddenAck = await fetch(
        `${baseUrl}/api/alerts/${alertId}/acknowledge`,
        { method: 'POST', headers: { cookie: second.cookie } }
    );
    assert.equal(forbiddenAck.status, 404);
    const ownerAck = await fetch(
        `${baseUrl}/api/alerts/${alertId}/acknowledge`,
        { method: 'POST', headers: { cookie } }
    );
    assert.equal(ownerAck.status, 200);
    const openAfterAck = await (await fetch(
        `${baseUrl}/api/alerts`, { headers: { cookie } })).json();
    assert.equal(openAfterAck.data.some(alert => alert.id === alertId), false);

    const crossSite = await fetch(`${baseUrl}/api/accounts`, {
        method: 'POST',
        headers: { cookie, origin: 'https://attacker.invalid', 'content-type': 'application/json' },
        body: JSON.stringify({ name: 'Blocked', api_key: 'x', api_secret: 'y' }),
    });
    assert.equal(crossSite.status, 403);

    for (let attempt = 0; attempt < 5; attempt += 1) {
        await fetch(`${baseUrl}/api/auth/login`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ username: 'locked-user', password: 'wrong-password' })
        });
    }
    const lockedBeforeRestart = await (await fetch(`${baseUrl}/api/auth/login`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ username: 'locked-user', password: 'wrong-password' })
    })).json();
    assert.equal(lockedBeforeRestart.locked, true);

    await stopServer();
    startServer();
    await waitForServer();
    const afterRestart = await fetch(`${baseUrl}/api/auth/me`, { headers: { cookie } });
    assert.equal((await afterRestart.json()).success, true);
    const lockedAfterRestart = await (await fetch(`${baseUrl}/api/auth/login`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ username: 'locked-user', password: 'wrong-password' })
    })).json();
    assert.equal(lockedAfterRestart.locked, true);
});
