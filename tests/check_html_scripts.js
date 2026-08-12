const fs = require('fs');
const path = require('path');

for (const name of ['index.html', 'trades.html', 'settings.html', 'backtest.html']) {
    const html = fs.readFileSync(path.join(__dirname, '..', 'templates', name), 'utf8');
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
