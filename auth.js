/** Authentication and persistent server-side sessions. */
const Database = require('./database');
const crypto = require('crypto');

const db = new Database();
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const SCRYPT_OPTIONS = { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 };
let initialized = false;

async function ensureInit() {
    if (!initialized) {
        const encryptionKey = process.env.ENCRYPTION_KEY ||
            (require('fs').existsSync('.env') ?
                require('fs').readFileSync('.env', 'utf8').match(/ENCRYPTION_KEY=(.+)/)?.[1]?.trim() : null);
        db.setEncryptionKey(encryptionKey);
        db.init();
        initialized = true;
        await new Promise(resolve => setTimeout(resolve, 500));
        await db.pruneExpiredSessions();
    }
}

function legacyHashPassword(password, salt) {
    return crypto.pbkdf2Sync(password, salt, 10000, 64, 'sha512').toString('hex');
}

function hashPassword(password, salt = crypto.randomBytes(16).toString('hex')) {
    const derived = crypto.scryptSync(password, salt, 64, SCRYPT_OPTIONS).toString('hex');
    return { password_hash: `scrypt$${derived}`, salt };
}

function verifyPassword(password, user) {
    const stored = String(user.password_hash || '');
    const candidate = stored.startsWith('scrypt$')
        ? `scrypt$${crypto.scryptSync(password, user.salt, 64, SCRYPT_OPTIONS).toString('hex')}`
        : legacyHashPassword(password, user.salt);
    const left = Buffer.from(candidate, 'utf8');
    const right = Buffer.from(stored, 'utf8');
    return left.length === right.length && crypto.timingSafeEqual(left, right);
}

function tokenHash(token) {
    return crypto.createHash('sha256').update(String(token || '')).digest('hex');
}

function userIsExpired(user) {
    if (!user.expired_at) return false;
    const expiry = String(user.expired_at).includes('T')
        ? new Date(user.expired_at).getTime()
        : new Date(`${user.expired_at}T23:59:59`).getTime();
    return !Number.isFinite(expiry) || Date.now() > expiry;
}

async function register(username, password, email) {
    await ensureInit();
    username = String(username || '').trim();
    email = String(email || '').trim().toLowerCase();
    if (!/^[A-Za-z0-9_.-]{3,64}$/.test(username)) {
        return { success: false, error: 'Username harus 3–64 karakter dan hanya berisi huruf, angka, titik, underscore, atau dash' };
    }
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        return { success: false, error: 'Alamat email valid wajib diisi' };
    }
    if (typeof password !== 'string' || password.length < 10) {
        return { success: false, error: 'Password minimal 10 karakter' };
    }
    if (await db.getUserByUsername(username)) {
        return { success: false, error: 'Username sudah digunakan' };
    }
    const passwordData = hashPassword(password);
    const userId = Date.now();
    await db.addUser({
        id: userId, username, email, ...passwordData,
        is_active: false, is_admin: false, expired_at: null, created_at: new Date().toISOString()
    });
    return { success: true, user_id: userId, message: 'Pendaftaran berhasil dan menunggu aktivasi admin.' };
}

async function login(username, password) {
    await ensureInit();
    const user = await db.getUserByUsername(String(username || '').trim());
    if (!user || !verifyPassword(String(password || ''), user)) {
        return { success: false, error: 'Invalid username or password' };
    }
    if (!user.is_active) return { success: false, error: 'Akun nonaktif atau menunggu aktivasi admin.' };
    if (userIsExpired(user)) return { success: false, error: 'Masa aktif akun telah berakhir.' };

    if (!String(user.password_hash).startsWith('scrypt$')) {
        Object.assign(user, hashPassword(password));
        await db.updateUser(user);
    }

    const sessionToken = crypto.randomBytes(32).toString('hex');
    const createdAt = new Date();
    await db.addSession({
        token_hash: tokenHash(sessionToken),
        user_id: user.id,
        created_at: createdAt.toISOString(),
        expires_at: new Date(createdAt.getTime() + SESSION_TTL_MS).toISOString(),
    });
    return {
        success: true,
        session_token: sessionToken,
        user: { id: user.id, username: user.username, email: user.email, is_admin: !!user.is_admin, expired_at: user.expired_at || null }
    };
}

async function logout(sessionToken) {
    if (!sessionToken) return { success: true };
    await ensureInit();
    await db.deleteSession(tokenHash(sessionToken));
    return { success: true };
}

async function getCurrentUser(sessionToken) {
    if (!sessionToken) return null;
    await ensureInit();
    const hash = tokenHash(sessionToken);
    const session = await db.getSession(hash);
    if (!session) return null;
    if (new Date(session.expires_at).getTime() <= Date.now()) {
        await db.deleteSession(hash);
        return null;
    }
    const user = await db.getUser(session.user_id);
    if (!user || !user.is_active || userIsExpired(user)) {
        await db.deleteSession(hash);
        return null;
    }
    return { id: user.id, username: user.username, email: user.email, is_admin: !!user.is_admin, expired_at: user.expired_at || null };
}

async function revokeUserSessions(userId) {
    await ensureInit();
    return db.deleteUserSessions(userId);
}

module.exports = { register, login, logout, getCurrentUser, revokeUserSessions, hashPassword };
