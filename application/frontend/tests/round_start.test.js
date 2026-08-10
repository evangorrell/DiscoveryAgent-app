// Browser-free contract tests for the shared round-start client.
// Run with: node --test tests/round_start.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const CLIENT = path.resolve(__dirname, '../da_selection_ui/static/round_start.js');

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

function response(ok, data) {
    return {
        ok,
        headers: { get: () => 'application/json' },
        json: async () => data,
    };
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
    const footer = {
        querySelector: () => null,
        appendChild: () => {},
    };
    const attributes = new Map([['data-start-round-number', String(round)]]);
    const button = {
        textContent: 'Start Round ' + round,
        disabled: false,
        parentNode: footer,
        getAttribute: (name) => attributes.has(name) ? attributes.get(name) : null,
        setAttribute: (name, value) => attributes.set(name, String(value)),
        removeAttribute: (name) => attributes.delete(name),
        closest(selector) {
            if (selector === '[data-start-round]') return this;
            if (selector.includes('[data-start-round-surface]')) return surface;
            return null;
        },
    };
    const document = {
        addEventListener(type, handler) { if (type === 'click') clickHandler = handler; },
        createElement() { throw new Error('The provided modal note should be reused.'); },
    };
    global.document = document;
    global.window = { location: { assign: (url) => assigned.push(url) } };
    global.fetch = fetchImpl;
    delete require.cache[CLIENT];
    require(CLIENT);

    return {
        button, dismiss, note, surface, assigned,
        click() {
            clickHandler({
                target: button,
                preventDefault() {},
            });
        },
    };
}

async function flush() {
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
}

test('one pending shared launch blocks duplicate clicks and uses the server workspace URL', async () => {
    const pendingFetch = deferred();
    let fetchCount = 0;
    const ui = loadClient(() => {
        fetchCount += 1;
        return pendingFetch.promise;
    });

    ui.click();
    ui.click();
    assert.equal(fetchCount, 1);
    assert.equal(ui.button.disabled, true);
    assert.equal(ui.dismiss.disabled, true);
    assert.equal(ui.note.textContent, 'Starting Round 4…');

    pendingFetch.resolve(response(true, {
        ok: true,
        redirect_url: '/rounds/?project=example&round=4&started=1',
    }));
    await flush();

    assert.deepEqual(ui.assigned, ['/rounds/?project=example&round=4&started=1']);
});

test('launch failure stays on the current surface and restores both modal actions', async () => {
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
