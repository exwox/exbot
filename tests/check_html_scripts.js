const fs = require('fs');
const path = require('path');

for (const name of ['index.html', 'trades.html', 'settings.html', 'backtest.html']) {
    const html = fs.readFileSync(path.join(__dirname, '..', 'templates', name), 'utf8');
    const ids = [...html.matchAll(/\sid=["']([^"']+)["']/gi)].map(match => match[1]);
    const duplicateIds = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
    if (duplicateIds.length) {
        throw new Error(`${name} duplicate element ids: ${duplicateIds.join(', ')}`);
    }
    const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
        .map(match => match[1])
        .filter(source => source.trim());
    scripts.forEach((source, index) => {
        try {
            new Function(source);
        } catch (error) {
            throw new Error(`${name} inline script ${index + 1}: ${error.message}`);
        }
    });
    console.log(`${name} inline scripts: OK`);
}

const settings = fs.readFileSync(
    path.join(__dirname, '..', 'templates', 'settings.html'), 'utf8'
);
for (const marker of [
    'window.saveTelegramToken =',
    'window.toggleTelegramConfig =',
    'window.generateTelegramLinkCode =',
    'window.testTelegramNotification =',
    'function loadTelegramConfig()'
]) {
    const occurrences = settings.split(marker).length - 1;
    if (occurrences !== 1) {
        throw new Error(`settings.html must contain exactly one ${marker}; found ${occurrences}`);
    }
}

for (const selector of [
    '.form-control:disabled',
    '.form-control:-webkit-autofill',
    '.btn-outline-primary',
    '.alert-success'
]) {
    if (!settings.includes(selector)) {
        throw new Error(`settings.html must define a visible dark-theme state for ${selector}`);
    }
}
