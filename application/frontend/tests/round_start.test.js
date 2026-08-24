// Browser-free contract tests for the shared project-configured round start.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const CLIENT = path.resolve(__dirname, '../da_selection_ui/static/round_start.js');
const SOURCE = fs.readFileSync(CLIENT, 'utf8');

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

function response(ok, data) {
    return { ok, headers: { get: () => 'application/json' }, json: async () => data };
}

function loadClient(fetchImpl, round = 4) {
    let clickHandler = null;
    const assigned = [];
    const note = { textContent: '', hidden: true };
    const dismiss = { disabled: false };
    const surface = {
        hidden: false,
        querySelector: (selector) => selector === '[data-start-round-note]' ? note : null,
        querySelectorAll: (selector) => selector === '[data-start-round-dismiss]' ? [dismiss] : [],
    };
    const attributes = new Map([['data-start-round-number', String(round)]]);
    const button = {
        textContent: 'Start Round ' + round,
        disabled: false,
        parentNode: { querySelector: () => null, appendChild() {} },
        getAttribute: (name) => attributes.has(name) ? attributes.get(name) : null,
        setAttribute: (name, value) => attributes.set(name, String(value)),
        removeAttribute: (name) => attributes.delete(name),
        closest(selector) {
            if (selector === '[data-start-round]') return this;
            if (selector.includes('[data-start-round-surface]')) return surface;
            return null;
        },
    };
    global.document = {
        addEventListener(type, handler) { if (type === 'click') clickHandler = handler; },
    };
    global.window = {
        discoveryProjectContext: { projectId: 'project-1' },
        location: { assign: (url) => assigned.push(url) },
    };
    global.fetch = fetchImpl;
    delete require.cache[CLIENT];
    require(CLIENT);
    return {
        assigned, button, dismiss, note, surface,
        click() { clickHandler({ target: button, preventDefault() {} }); },
    };
}

async function flush() {
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
}

test('one pending shared launch blocks duplicate clicks and uses the server workspace URL', async () => {
    const pending = deferred();
    const calls = [];
    const ui = loadClient((url, init) => { calls.push({ url, init }); return pending.promise; });

    ui.click();
    ui.click();
    await flush();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/rounds/start');
    assert.equal(ui.button.disabled, true);
    assert.equal(ui.dismiss.disabled, true);
    assert.equal(ui.note.textContent, 'Starting Round 4…');

    pending.resolve(response(true, { ok: true, redirect_url: '/rounds/?project=example&round=4&started=1' }));
    await flush();
    assert.deepEqual(ui.assigned, ['/rounds/?project=example&round=4&started=1']);
});

test('Round Start sends no parallel query, model, or research-tool settings', async () => {
    const calls = [];
    const ui = loadClient((url, init) => {
        calls.push({ url, init });
        return Promise.resolve(response(true, { ok: true, redirect_url: '/rounds/?round=4' }));
    });
    ui.click();
    await flush();

    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/rounds/start');
    assert.deepEqual(JSON.parse(calls[0].init.body), { project_id: 'project-1' });
    assert.equal(calls[0].init.headers['Content-Type'], 'application/json');
    assert.doesNotMatch(SOURCE, /rounds\/options|num_queries|reasoning_model|execution_model|deep_research|web_search/);
});

test('launch failure stays on the current surface and restores its actions', async () => {
    const ui = loadClient(async () => response(false, {
        ok: false,
        code: 'anthropic_api_key_missing',
        message: 'Anthropic API key not detected. Set ANTHROPIC_API_KEY and try again.',
    }));
    ui.click();
    await flush();

    assert.deepEqual(ui.assigned, []);
    assert.equal(ui.surface.hidden, false);
    assert.equal(ui.button.disabled, false);
    assert.equal(ui.dismiss.disabled, false);
    assert.equal(ui.button.getAttribute('aria-busy'), null);
    assert.equal(ui.note.hidden, false);
    assert.match(ui.note.textContent, /ANTHROPIC_API_KEY/);
});
