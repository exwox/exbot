const crypto = require('crypto');

const V2_PREFIX = 'v2';
const V2_AAD = Buffer.from('xbot-credential-v2', 'utf8');
const SCRYPT_OPTIONS = { N: 16384, r: 8, p: 1, maxmem: 64 * 1024 * 1024 };

function validateMasterKey(value) {
    const key = String(value || '');
    if (Buffer.byteLength(key, 'utf8') < 16) {
        throw new Error('ENCRYPTION_KEY must contain at least 16 bytes; 32 or more random bytes are recommended');
    }
    return key;
}

function deriveKey(masterKey, salt) {
    return crypto.scryptSync(validateMasterKey(masterKey), salt, 32, SCRYPT_OPTIONS);
}

function credentialAad(version, context = '') {
    const suffix = context ? ':' + context : '';
    return Buffer.from('xbot-credential-' + version + suffix, 'utf8');
}

function encryptCredential(plaintext, masterKey, context = '') {
    const version = context ? 'v3' : V2_PREFIX;
    const salt = crypto.randomBytes(16);
    const nonce = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv('aes-256-gcm', deriveKey(masterKey, salt), nonce);
    cipher.setAAD(credentialAad(version, context));
    const ciphertext = Buffer.concat([cipher.update(String(plaintext), 'utf8'), cipher.final()]);
    const tag = cipher.getAuthTag();
    return [version, salt.toString('hex'), nonce.toString('hex'), tag.toString('hex'), ciphertext.toString('hex')].join(':');
}

function decryptAuthenticated(payload, masterKey, context = '') {
    const parts = String(payload).split(':');
    if (parts.length !== 5 || !['v2', 'v3'].includes(parts[0])) {
        throw new Error('Invalid authenticated credential payload');
    }
    const [version, saltHex, nonceHex, tagHex, ciphertextHex] = parts;
    if (version === 'v3' && !context) {
        throw new Error('Account context is required for v3 credential');
    }
    const salt = Buffer.from(saltHex, 'hex');
    const nonce = Buffer.from(nonceHex, 'hex');
    const tag = Buffer.from(tagHex, 'hex');
    const ciphertext = Buffer.from(ciphertextHex, 'hex');
    if (salt.length !== 16 || nonce.length !== 12 || tag.length !== 16) {
        throw new Error('Invalid v2 credential parameters');
    }
    const decipher = crypto.createDecipheriv('aes-256-gcm', deriveKey(masterKey, salt), nonce);
    decipher.setAAD(version === 'v2' ? V2_AAD : credentialAad(version, context));
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString('utf8');
}

function decryptLegacyCbc(payload, masterKey) {
    const parts = String(payload).split(':');
    if (parts.length !== 2) throw new Error('Invalid legacy CBC credential payload');
    const iv = Buffer.from(parts[0], 'hex');
    if (iv.length !== 16) throw new Error('Invalid legacy CBC IV');
    const encrypted = Buffer.from(parts[1], 'hex');
    const decipher = crypto.createDecipheriv(
        'aes-256-cbc',
        deriveKey(masterKey, Buffer.from('salt', 'utf8')),
        iv
    );
    return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8');
}

function decryptLegacyFernet(payload, masterKey) {
    const rawKey = Buffer.from(String(masterKey), 'base64url');
    const token = Buffer.from(String(payload), 'base64url');
    if (rawKey.length !== 32 || token.length < 73 || token[0] !== 0x80) {
        throw new Error('Invalid legacy Fernet credential payload');
    }
    const signed = token.subarray(0, -32);
    const suppliedMac = token.subarray(-32);
    const expectedMac = crypto.createHmac('sha256', rawKey.subarray(0, 16)).update(signed).digest();
    if (!crypto.timingSafeEqual(suppliedMac, expectedMac)) {
        throw new Error('Invalid legacy Fernet credential signature');
    }
    const iv = token.subarray(9, 25);
    const encrypted = token.subarray(25, -32);
    const decipher = crypto.createDecipheriv('aes-128-cbc', rawKey.subarray(16), iv);
    return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8');
}

function decryptCredential(payload, masterKey, context = '') {
    if (!payload) return '';
    validateMasterKey(masterKey);
    if (/^v[23]:/.test(String(payload))) {
        return decryptAuthenticated(payload, masterKey, context);
    }
    if (String(payload).split(':').length === 2) return decryptLegacyCbc(payload, masterKey);
    return decryptLegacyFernet(payload, masterKey);
}

module.exports = {
    decryptCredential,
    encryptCredential,
    validateMasterKey,
};
