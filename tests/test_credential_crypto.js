const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const test = require('node:test');
const { decryptCredential, encryptCredential } = require('../credential-crypto');

const key = 'test-master-key-with-at-least-32-bytes';

test('v2 credential encryption round-trips and detects tampering', () => {
    const payload = encryptCredential('secret-value', key);
    assert.match(payload, /^v2:/);
    assert.equal(decryptCredential(payload, key), 'secret-value');
    const tampered = `${payload.slice(0, -1)}${payload.endsWith('0') ? '1' : '0'}`;
    assert.throws(() => decryptCredential(tampered, key));
});

test('legacy Node CBC credentials remain readable', () => {
    const iv = crypto.randomBytes(16);
    const legacyKey = crypto.scryptSync(key, 'salt', 32, { N: 16384, r: 8, p: 1, maxmem: 64 * 1024 * 1024 });
    const cipher = crypto.createCipheriv('aes-256-cbc', legacyKey, iv);
    const ciphertext = Buffer.concat([cipher.update('legacy-secret', 'utf8'), cipher.final()]);
    const payload = `${iv.toString('hex')}:${ciphertext.toString('hex')}`;
    assert.equal(decryptCredential(payload, key), 'legacy-secret');
});
