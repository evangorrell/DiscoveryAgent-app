// Browser-free contract tests for the shared round-start client.
// Run with: node --test tests/round_start.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const CLIENT = path.resolve(__dirname, '../da_selection_ui/static/round_start.js');

const DEFAULT_OPTIONS = {
    num_queries: 5,
    minimum_queries: 1,
    maximum_queries: 50,
    deep_research: true,
    web_search: true,
    reasoning_model: 'claude-opus-4-6',
    execution_model: 'claude-sonnet-4-6',
};

// Shaped like the Models API response the server relays.
const MODELS = [
    { id: 'claude-opus-9', display_name: 'Claude Opus 9' },
    { id: 'claude-opus-4-6', display_name: 'Claude Opus 4.6' },
    { id: 'claude-sonnet-4-6', display_name: 'Claude Sonnet 4.6' },
];

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

// Enough of a DOM for the settings dialog the client builds before launching.
class FakeNode {
    constructor(tag) {
        this.tag = tag;
        this.children = [];
        this.attributes = new Map();
        this.listeners = new Map();
        this.className = '';
        this.textContent = '';
        this.hidden = false;
        this.disabled = false;
        this.checked = false;
        this.value = '';
        this.classList = { add() {}, remove() {} };
    }
    appendChild(child) { this.children.push(child); return child; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
    removeAttribute(name) { this.attributes.delete(name); }
    addEventListener(type, handler) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(handler);
    }
    dispatch(type, event) {
        (this.listeners.get(type) || []).forEach((handler) => handler(event || {}));
    }
    remove() {}
    focus() {}
    descendants() {
        return this.children.flatMap((child) => [child, ...child.descendants()]);
    }
    find(predicate) {
        return this.descendants().find(predicate) || null;
    }
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
    const body = new FakeNode('body');
    const document = {
        body,
        activeElement: button,
        addEventListener(type, handler) { if (type === 'click') clickHandler = handler; },
        removeEventListener() {},
        createElement: (tag) => new FakeNode(tag),
    };
    global.document = document;
    global.window = { location: { assign: (url) => assigned.push(url) } };
    global.fetch = fetchImpl;
    delete require.cache[CLIENT];
    require(CLIENT);

    function dialog() {
        return body.children[body.children.length - 1] || null;
    }
    function dialogButton(label) {
        const overlay = dialog();
        return overlay && overlay.find((node) => node.tag === 'button' && node.textContent === label);
    }

    return {
        button, dismiss, note, surface, assigned, body, dialog, dialogButton,
        checkboxes: () => dialog().descendants().filter((node) => node.tag === 'input' && node.type === 'checkbox'),
        selects: () => dialog().descendants().filter((node) => node.tag === 'select'),
        confirm() { dialogButton('Start Round ' + round).dispatch('click'); },
        cancel() { dialogButton('Cancel').dispatch('click'); },
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
    await new Promise((resolve) => setImmediate(resolve));
}

function optionsResponse(round = 4, extra = {}) {
    return response(true, {
        ok: true,
        round,
        defaults: DEFAULT_OPTIONS,
        models: MODELS,
        models_source: 'anthropic',
        ...extra,
    });
}

test('one pending shared launch blocks duplicate clicks and uses the server workspace URL', async () => {
    const pendingFetch = deferred();
    const calls = [];
    const ui = loadClient((url, init) => {
        calls.push({ url, init });
        if (url === '/api/rounds/options') return Promise.resolve(optionsResponse());
        return pendingFetch.promise;
    });

    ui.click();
    ui.click();
    await flush();
    assert.equal(calls.length, 1, 'settings are requested once, and no launch has started');

    ui.confirm();
    await flush();
    assert.equal(calls.length, 2);
    assert.equal(calls[1].url, '/api/rounds/start');
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

test('the settings a scientist picks are what the launch request carries', async () => {
    const calls = [];
    const ui = loadClient((url, init) => {
        calls.push({ url, init });
        if (url === '/api/rounds/options') return Promise.resolve(optionsResponse());
        return Promise.resolve(response(true, { ok: true, redirect_url: '/rounds/?round=4' }));
    });

    ui.click();
    await flush();

    const number = ui.dialog().descendants().find((node) => node.tag === 'input' && node.type === 'number');
    number.value = '12';
    const boxes = ui.checkboxes();
    boxes[0].checked = false;          // deep research off
    ui.confirm();
    await flush();

    const sent = JSON.parse(calls[1].init.body);
    assert.equal(sent.num_queries, 12);
    assert.equal(sent.deep_research, false);
    assert.equal(sent.web_search, true);
});

test('the dialog offers only agents that need no outside authorization', async () => {
    const ui = loadClient((url) => {
        if (url === '/api/rounds/options') return Promise.resolve(optionsResponse());
        return Promise.resolve(response(true, { ok: true, redirect_url: '/rounds/?round=4' }));
    });

    ui.click();
    await flush();

    // Open Targets and the Census-family agents need an MCP server or a
    // CENSUS_API_KEY, so they are not offered at all.
    assert.equal(ui.checkboxes().length, 2);
    const labels = ui.dialog().descendants()
        .filter((node) => node.className === 'v2-checkbox-label')
        .map((node) => node.textContent);
    assert.deepEqual(labels, ['Deep research', 'Web search']);
});

test('the sub-agent architecture is not a choice the dialog offers', async () => {
    const calls = [];
    const ui = loadClient((url, init) => {
        calls.push({ url, init });
        if (url === '/api/rounds/options') return Promise.resolve(optionsResponse());
        return Promise.resolve(response(true, { ok: true, redirect_url: '/rounds/?round=4' }));
    });

    ui.click();
    await flush();
    const text = ui.dialog().descendants().map((node) => node.textContent).join(' ');
    assert.equal(text.includes('Sub-agent'), false);

    ui.confirm();
    await flush();
    // Nothing is sent for it — the server turns it on unconditionally.
    assert.equal('sub_agents' in JSON.parse(calls[1].init.body), false);
});

test('the query count explains what spending more of it buys', async () => {
    const ui = loadClient((url) => {
        if (url === '/api/rounds/options') return Promise.resolve(optionsResponse());
        return Promise.resolve(response(true, { ok: true, redirect_url: '/rounds/?round=4' }));
    });

    ui.click();
    await flush();

    const help = ui.dialog().descendants()
        .filter((node) => node.className === 'v2-field-help')
        .map((node) => node.textContent)
        .join(' ');
    assert.match(help, /more resources spent exploring this hypothesis/);
});

test('the model list offered comes from the server, newest first', async () => {
    const ui = loadClient((url) => {
        if (url === '/api/rounds/options') return Promise.resolve(optionsResponse());
        return Promise.resolve(response(true, { ok: true, redirect_url: '/rounds/?round=4' }));
    });

    ui.click();
    await flush();

    const [reasoning, execution] = ui.selects();
    // A model released after this client shipped is offered without a code change.
    assert.deepEqual(
        reasoning.children.map((option) => option.value),
        ['claude-opus-9', 'claude-opus-4-6', 'claude-sonnet-4-6'],
    );
    assert.equal(reasoning.children.find((o) => o.selected).value, 'claude-opus-4-6');
    assert.equal(execution.children.find((o) => o.selected).value, 'claude-sonnet-4-6');
});

test('a saved model the current list no longer offers stays selectable', async () => {
    const ui = loadClient((url) => {
        if (url === '/api/rounds/options') {
            return Promise.resolve(optionsResponse(4, {
                defaults: { ...DEFAULT_OPTIONS, reasoning_model: 'claude-retired-1' },
            }));
        }
        return Promise.resolve(response(true, { ok: true, redirect_url: '/rounds/?round=4' }));
    });

    ui.click();
    await flush();

    const [reasoning] = ui.selects();
    // Dropping it would silently move the project onto a different model.
    assert.equal(reasoning.children[0].value, 'claude-retired-1');
    assert.equal(reasoning.children.find((o) => o.selected).value, 'claude-retired-1');
});

test('the chosen models ride along with the launch request', async () => {
    const calls = [];
    const ui = loadClient((url, init) => {
        calls.push({ url, init });
        if (url === '/api/rounds/options') return Promise.resolve(optionsResponse());
        return Promise.resolve(response(true, { ok: true, redirect_url: '/rounds/?round=4' }));
    });

    ui.click();
    await flush();
    const [reasoning, execution] = ui.selects();
    reasoning.value = 'claude-opus-9';
    execution.value = 'claude-sonnet-4-6';
    ui.confirm();
    await flush();

    const sent = JSON.parse(calls[1].init.body);
    assert.equal(sent.reasoning_model, 'claude-opus-9');
    assert.equal(sent.execution_model, 'claude-sonnet-4-6');
});

test('cancelling the settings dialog starts nothing and restores the button', async () => {
    const calls = [];
    const ui = loadClient((url) => {
        calls.push(url);
        if (url === '/api/rounds/options') return Promise.resolve(optionsResponse());
        throw new Error('a cancelled dialog must not launch a round');
    });

    ui.click();
    await flush();
    ui.cancel();
    await flush();

    assert.deepEqual(calls, ['/api/rounds/options']);
    assert.deepEqual(ui.assigned, []);
    assert.equal(ui.button.disabled, false);
    assert.equal(ui.note.hidden, true);
});

test('launch failure stays on the current surface and restores both modal actions', async () => {
    const ui = loadClient(async (url) => {
        if (url === '/api/rounds/options') return optionsResponse();
        return response(false, {
            ok: false,
            code: 'anthropic_api_key_missing',
            message: 'Anthropic API key not detected. Set ANTHROPIC_API_KEY and try again.',
        });
    });

    ui.click();
    await flush();
    ui.confirm();
    await flush();

    assert.deepEqual(ui.assigned, []);
    assert.equal(ui.surface.hidden, false);
    assert.equal(ui.button.disabled, false);
    assert.equal(ui.dismiss.disabled, false);
    assert.equal(ui.button.getAttribute('aria-busy'), null);
    assert.equal(ui.note.hidden, false);
    assert.match(ui.note.textContent, /ANTHROPIC_API_KEY/);
});
