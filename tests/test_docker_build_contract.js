const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');

test('docker build excludes host state and compiles native sqlite inside image', () => {
    const ignored = new Set(fs.readFileSync(
        path.join(root, '.dockerignore'), 'utf8'
    ).split(/\r?\n/).map(line => line.trim()).filter(Boolean));
    for (const required of ['.env', 'node_modules', 'data', 'logs', 'backups']) {
        assert.equal(ignored.has(required), true, `${required} must be excluded`);
    }

    const dockerfile = fs.readFileSync(path.join(root, 'Dockerfile'), 'utf8');
    assert.match(dockerfile, /npm_config_build_from_source=true\s+npm ci/);
    assert.match(dockerfile, /require\(['"]sqlite3['"]\)/);
});

test('compose applies least-privilege runtime defaults', () => {
    const compose = fs.readFileSync(path.join(root, 'compose.yaml'), 'utf8');
    assert.match(compose, /read_only:\s*true/);
    assert.match(compose, /cap_drop:\s*\n\s*- ALL/);
    assert.match(compose, /no-new-privileges:true/);
    assert.match(compose, /XBOT_BIND_ADDRESS:-127\.0\.0\.1/);
    assert.match(compose, /tmpfs:\s*\n\s*- \/tmp:/);
});

test('container startup warns about long-lived bootstrap and shared backup secrets', () => {
    const entrypoint = fs.readFileSync(path.join(root, 'docker-entrypoint.sh'), 'utf8');
    assert.match(entrypoint, /ADMIN_PASSWORD masih tersedia/);
    assert.match(entrypoint, /BACKUP_ENCRYPTION_KEY kosong/);
});
