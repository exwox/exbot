const assert = require('node:assert/strict');
const { test } = require('node:test');

const router = require('../api-endpoints');

function registeredRoutes() {
    return (router.stack || [])
        .filter(layer => layer.route)
        .flatMap(layer => Object.keys(layer.route.methods).map(method => ({
            method: method.toUpperCase(),
            path: layer.route.path
        })));
}

test('Telegram API routes are registered at module initialization', () => {
    const routes = registeredRoutes();
    for (const expected of [
        { method: 'GET', path: '/telegram/config' },
        { method: 'POST', path: '/telegram/config' },
        { method: 'DELETE', path: '/telegram/config' },
        { method: 'POST', path: '/telegram/link-code' },
        { method: 'POST', path: '/telegram/consume' },
        { method: 'POST', path: '/telegram/test' }
    ]) {
        assert.equal(
            routes.some(route => route.method === expected.method && route.path === expected.path),
            true,
            `${expected.method} ${expected.path} must be registered`
        );
    }
});
