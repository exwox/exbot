/** Ensure an explicit administrator exists without shipping a default password. */
const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..');
process.chdir(projectRoot);

const Database = require('../database');
const auth = require('../auth');

function envValue(name) {
    if (process.env[name]) return process.env[name];
    if (!fs.existsSync('.env')) return '';
    const match = fs.readFileSync('.env', 'utf8').match(new RegExp(`^${name}=(.*)$`, 'm'));
    return match?.[1]?.trim() || '';
}

async function main() {
    const encryptionKey = envValue('ENCRYPTION_KEY');
    const database = new Database();
    database.setEncryptionKey(encryptionKey);
    database.init();
    await new Promise(resolve => setTimeout(resolve, 500));

    const users = await database.getAllUsers();
    if (users.some(user => user.is_admin)) {
        database.db.close();
        console.log('[ADMIN] Administrator already configured');
        return;
    }

    const username = envValue('ADMIN_USERNAME') || 'admin';
    const password = envValue('ADMIN_PASSWORD');
    const email = envValue('ADMIN_EMAIL') || 'admin@localhost.invalid';
    if (password.length < 10) {
        database.db.close();
        throw new Error('No administrator exists. Set ADMIN_PASSWORD (minimum 10 characters) for the first startup.');
    }
    if (!/^[A-Za-z0-9_.-]{3,64}$/.test(username)) {
        database.db.close();
        throw new Error('ADMIN_USERNAME is invalid');
    }

    const existing = await database.getUserByUsername(username);
    if (existing) {
        existing.is_active = true;
        existing.is_admin = true;
        Object.assign(existing, auth.hashPassword(password));
        await database.updateUser(existing);
    } else {
        const passwordData = auth.hashPassword(password);
        await database.addUser({
            id: Date.now(), username, email, ...passwordData,
            is_active: true, is_admin: true, expired_at: null,
        });
    }
    database.db.close();
    console.log(`[ADMIN] Administrator '${username}' configured. Remove ADMIN_PASSWORD from the environment after first startup.`);
}

main().catch(error => {
    console.error(`[ADMIN] ${error.message}`);
    process.exitCode = 1;
});
