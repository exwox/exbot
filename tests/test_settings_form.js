const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

test('browser form keeps Save and every DCA field attached after relocation', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'xbot-form-'));
    try {
        const source = fs.readFileSync(path.join(__dirname, '../templates/settings.html'), 'utf8');
        const saveFunction = source.slice(source.indexOf('async function saveDcaSettings(event)'),
            source.indexOf('function showAddAccountForm()'));
        const defaults = JSON.parse(fs.readFileSync(path.join(__dirname, '../config/strategy_defaults.json'), 'utf8'));
        const html = source.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
            .replace(/<link\b[^>]*>/gi, '')
            .replace('</body>', `<script>
                ${saveFunction}
                let selectedDcaBot = { id: 'bot', strategy_id: 'strategy', dry_run: true };
                let selectedDcaStrategy = { id: 'strategy' };
                const currentDcaCapitalPlan = null;
                const dcaData = { bots: [] };
                const requests = [];
                const alerts = [];
                function showSettingsAlert(message) { alerts.push(message); }
                async function loadDcaSettings() {}
                async function loadBots() {}
                async function fetch(url, options) {
                    requests.push({ url, body: JSON.parse(options.body) });
                    return { ok: true, json: async () => ({ success: true,
                        data: { ...selectedDcaBot, dry_run: false } }) };
                }
                (async () => {
                const form = document.getElementById('dcaForm');
                document.getElementById('botsList').insertAdjacentElement('afterend', form);
                const save = [...document.querySelectorAll('button')]
                    .find(b => b.textContent.trim() === 'Simpan Pengaturan DCA');
                const result = document.createElement('pre');
                result.id = 'form-result';
                const report = {
                    saveAttached: save?.form === form,
                    fields: [...form.elements].map(e => e.name).filter(Boolean)
                };
                const defaults = ${JSON.stringify(defaults)};
                for (const field of form.elements) {
                    if (field.name in defaults) {
                        if (field.type === 'checkbox') field.checked = !!defaults[field.name];
                        else field.value = defaults[field.name];
                    }
                }
                form.elements.bot_name.value = 'Browser Test';
                form.elements.pair.value = 'btcidr';
                form.elements.dry_run.value = 'false';
                await saveDcaSettings({ preventDefault() {}, target: form });
                report.requests = requests;
                report.alerts = alerts;
                result.textContent = JSON.stringify(report);
                document.body.append(result);
                })();
            </script></body>`);
        const fixture = path.join(directory, 'form.html');
        fs.writeFileSync(fixture, html);
        const browser = spawnSync(process.env.CHROME_BIN || '/usr/bin/google-chrome', [
            '--headless=new', '--no-sandbox', '--disable-gpu',
            '--disable-background-networking', '--no-first-run',
            '--user-data-dir=' + path.join(directory, 'profile'),
            '--dump-dom', 'file://' + fixture
        ], { encoding: 'utf8', timeout: 20000, maxBuffer: 2 * 1024 * 1024 });
        assert.equal(browser.status, 0, browser.stderr || String(browser.error));
        const match = browser.stdout.match(/<pre id="form-result">([^<]+)<\/pre>/);
        assert.ok(match, 'Browser did not report form state');
        const report = JSON.parse(match[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&'));
        assert.equal(report.saveAttached, true, 'Save button is outside the form');
        for (const field of ['dry_run', 'base_order_amount', 'rsi_period', 'rsi_oversold', 'initial_entry_mode']) {
            assert.ok(report.fields.includes(field), field + ' is outside the form');
        }
        assert.equal(report.requests.length, 1, JSON.stringify(report.alerts));
        assert.equal(report.requests[0].url, '/api/settings');
        assert.equal(report.requests[0].body.settings.dry_run, false);
        assert.equal(report.requests[0].body.settings.rsi_period, defaults.rsi_period);
        assert.equal(report.requests[0].body.settings.bot_name, 'Browser Test');
    } finally {
        fs.rmSync(directory, { recursive: true, force: true });
    }
});
