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
