/**
 * Account Management - Multi-account support
 */
const Database = require('./database');
const crypto = require('crypto');
const { redactSensitive } = require('./log-redaction');

// Initialize database on load
let db = null;
let initialized = false;
function ensureInit() {
    if (!initialized) {
        const encryptionKey = process.env.ENCRYPTION_KEY ||
            (require('fs').existsSync('.env') ?
                require('fs').readFileSync('.env', 'utf8').match(/ENCRYPTION_KEY=(.+)/)?.[1]?.trim() : null);

        if (!encryptionKey) {
            console.warn('[ACCOUNTS] WARNING: No ENCRYPTION_KEY found in .env');
            console.warn('[ACCOUNTS] Generate with: node -e "console.log(require(\'crypto\').randomBytes(32).toString(\'hex\'))"');
        }

        db = new Database();
        db.setEncryptionKey(encryptionKey);
        db.init();
        initialized = true;

        // Wait for DB to be ready
        return new Promise(resolve => setTimeout(resolve, 500));
    }
}

// Get all accounts for a specific user
async function getUserAccounts(userId) {
    await ensureInit();
    return db.getUserAccounts(userId);
}

// Get active accounts for a specific user
async function getUserActiveAccounts(userId) {
    await ensureInit();
    return db.getUserAccounts(userId, true);
}

// Get all accounts (legacy - for backward compatibility)
async function getAllAccounts() {
    await ensureInit();
    return await db.getAllAccounts();
}

// Get active accounts (legacy - for backward compatibility)
async function getActiveAccounts() {
    await ensureInit();
    return await db.getActiveAccounts();
}

// Get account by ID
async function getAccount(id) {
    await ensureInit();
    return await db.getAccount(id);
}

// Create new account
async function createAccount(userId, name, apiKey, apiSecret, exchange = 'Indodax') {
    await ensureInit();

    const accountId = `acc_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
    const encryptedApiKey = db.encrypt(apiKey);
    const encryptedApiSecret = db.encrypt(apiSecret);

    await db.addAccount({
        id: accountId,
        user_id: userId,
        name,
        exchange,
        api_key_encrypted: encryptedApiKey,
        api_secret_encrypted: encryptedApiSecret,
        is_active: true,
        last_connected_at: null,
        last_error: null
    });

    return await getAccount(accountId);
}

// Update account
async function updateAccount(id, updates) {
    await ensureInit();

    const account = await getAccount(id);
    if (!account) {
        throw new Error('Account not found');
    }

    if (updates.api_key) {
        account.api_key_encrypted = db.encrypt(updates.api_key);
    }
    if (updates.api_secret) {
        account.api_secret_encrypted = db.encrypt(updates.api_secret);
    }
    if (updates.name) {
        account.name = updates.name;
    }
    if (updates.exchange) {
        account.exchange = updates.exchange;
    }
    if (updates.is_active !== undefined) {
        account.is_active = updates.is_active;
    }

    await db.updateAccount(account);
    return await getAccount(id);
}

async function getUserAccount(userId, accountId) {
    const account = await getAccount(accountId);
    if (!account) return null;

    // SQLite may serialize a legacy INTEGER user id into a TEXT column as
    // "123.0". Compare numeric ids numerically so valid owners are not
    // incorrectly rejected with a 404, while preserving exact matching for
    // modern text ids.
    const storedId = String(account.user_id);
    const requestedId = String(userId);
    const bothNumeric = /^\d+(?:\.0+)?$/.test(storedId) && /^\d+(?:\.0+)?$/.test(requestedId);
    const belongsToUser = storedId === requestedId || (bothNumeric && Number(storedId) === Number(requestedId));
    return belongsToUser ? account : null;
}

// Delete account
async function deleteAccount(id) {
    await ensureInit();
    return await db.deleteAccount(id);
}

// Get decrypted credentials
async function getDecryptedCredentials(accountId) {
    await ensureInit();

    const account = await getAccount(accountId);
    if (!account) {
        return null;
    }

    let credentials;
    try {
        credentials = {
            api_key: db.decrypt(account.api_key_encrypted),
            api_secret: db.decrypt(account.api_secret_encrypted)
        };
    } catch (e) {
        console.error('[ACCOUNTS] Failed to decrypt credentials:', redactSensitive(e.message));
        try {
            await db.raiseAlert({
                account_id: accountId,
                severity: 'CRITICAL',
                kind: 'CREDENTIAL_DECRYPTION_FAILED',
                dedupe_key: `credential-decryption:${accountId}`,
                message: 'Credential akun tidak dapat didekripsi',
                metadata: { account_id: accountId }
            });
        } catch (alertError) {
            console.error('[ACCOUNTS] Failed to persist decryption alert:',
                redactSensitive(alertError.message));
        }
        return null;
    }
    try {
        await db.resolveAlert(`credential-decryption:${accountId}`);
    } catch (alertError) {
        console.error('[ACCOUNTS] Failed to resolve decryption alert:',
            redactSensitive(alertError.message));
    }
    return credentials;
}

// Test connection
async function testConnection(accountId) {
    await ensureInit();

    const account = await getAccount(accountId);
    if (!account) {
        return { success: false, error: 'Account not found' };
    }

    try {
        const creds = await getDecryptedCredentials(accountId);
        if (!creds) {
            return { success: false, error: 'Failed to decrypt credentials' };
        }

        // Import IndodaxClient
        const { IndodaxClient } = require('./indodax-client');
        const client = new IndodaxClient(creds.api_key, creds.api_secret);

        // Test with getInfo
        const result = await client.get_balance();

        if (result.error) {
            // Update last_error
            await db.updateAccount({
                ...account,
                last_error: result.error
            });
            return { success: false, error: result.error };
        }

        // Update last_connected_at
        await db.updateAccount({
            ...account,
            last_connected_at: new Date().toISOString(),
            last_error: null
        });

        return {
            success: true,
            balance: result,
            idr_balance: parseFloat(result.balance?.idr || 0)
        };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

// Mask credential for display
function maskCredential(credential, visibleStart = 6, visibleEnd = 4) {
    if (!credential) return '';
    if (credential.length <= visibleStart + visibleEnd) {
        return credential.substring(0, visibleStart) + '****';
    }
    return credential.substring(0, visibleStart) +
        '*'.repeat(credential.length - visibleStart - visibleEnd) +
        credential.substring(credential.length - visibleEnd);
}

module.exports = {
    getAllAccounts,
    getActiveAccounts,
    getAccount,
    getUserAccounts,
    getUserActiveAccounts,
    getUserAccount,
    createAccount,
    updateAccount,
    deleteAccount,
    getDecryptedCredentials,
    testConnection,
    maskCredential
};
