const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const CLIENT = path.resolve(__dirname, '../da_selection_ui/static/round_start.js');
const SOURCE = fs.readFileSync(CLIENT, 'utf8');
const LAUNCH_TEMPLATE = fs.readFileSync(path.resolve(__dirname, '../da_selection_ui/templates/_round_launch_dialog.html'), 'utf8');

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

function response(ok, data) {
    return { ok, headers: { get: () => 'application/json' }, json: async () => data };
}

function optionsPayload(round = 4) {
    return {
        ok: true, project_id: 'project-1', round,
        defaults: {
            num_queries: 12, query_count: 12, minimum_queries: 1, maximum_queries: 50,
            deep_research: true, web_search: false,
            reasoning_model: 'claude-opus-4-6', execution_model: 'claude-opus-4-6',
        },
        models: [
            { id: 'claude-opus-4-6', display_name: 'Claude Opus 4.6' },
            { id: 'claude-sonnet-4-6', display_name: 'Claude Sonnet 4.6' },
            { id: 'claude-haiku-4-5-20251001', display_name: 'Claude Haiku 4.5' },
        ],
    };
}

function classList() {
    const values = new Set();
    return {
        add: (value) => values.add(value),
        remove: (value) => values.delete(value),
        contains: (value) => values.has(value),
    };
}

function element(id) {
    const attributes = new Map();
    const listeners = new Map();
    return {
        id, hidden: false, disabled: false, checked: false, value: '', min: '', max: '',
        textContent: '', children: [], classList: classList(),
        setAttribute(name, value) { attributes.set(name, String(value)); },
        getAttribute(name) { return attributes.has(name) ? attributes.get(name) : null; },
        removeAttribute(name) { attributes.delete(name); },
        addEventListener(type, handler) { listeners.set(type, handler); },
        dispatch(type, event = {}) { const handler = listeners.get(type); if (handler) handler({ target: this, ...event }); },
        appendChild(child) { this.children.push(child); },
        append(...children) { this.children.push(...children); },
        replaceChildren(...children) { this.children = children; },
        focus() { this.focused = true; },
        querySelector() { return null; },
        closest() { return null; },
    };
}

function loadClient(fetchImpl, round = 4) {
    const ids = [
        'round-launch-overlay', 'round-launch-title', 'round-launch-queries',
        'round-launch-query-help', 'round-launch-deep-research', 'round-launch-web-search',
        'round-launch-reasoning-model', 'round-launch-execution-model', 'round-launch-error',
        'round-launch-cancel', 'round-launch-confirm',
    ];
    const elements = Object.fromEntries(ids.map((id) => [id, element(id)]));
    elements['round-launch-overlay'].hidden = true;
    const note = element('trigger-note'); note.hidden = true;
    const surface = element('trigger-surface');
    surface.querySelector = (selector) => selector === '[data-start-round-note]' ? note : null;
    const button = element('start-round');
    button.textContent = 'Start Round ' + round;
    button.setAttribute('data-start-round-number', String(round));
    button.parentNode = element('parent');
    button.closest = (selector) => selector === '[data-start-round]' ? button
        : selector.includes('[data-start-round-surface]') ? surface : null;
    const documentListeners = new Map();
    const assigned = [];
    const events = [];
    global.document = {
        body: { classList: classList() },
        getElementById: (id) => elements[id] || null,
        createElement: (tag) => element(tag),
        addEventListener(type, handler) { documentListeners.set(type, handler); },
    };
    global.window = {
        discoveryProjectContext: { projectId: 'project-1' },
        location: { assign: (url) => assigned.push(url) },
        dispatchEvent: (event) => events.push(event.type),
    };
    global.fetch = fetchImpl;
    delete require.cache[CLIENT];
    require(CLIENT);
    return {
        assigned, events, button, note, surface, elements,
        clickStart() {
            documentListeners.get('click')({ target: button, preventDefault() {} });
        },
        confirm() { elements['round-launch-confirm'].dispatch('click'); },
    };
}

async function flush() {
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
}

test('one shared launch flow blocks duplicate option loads and duplicate starts', async () => {
    const optionRequest = deferred();
    const startRequest = deferred();
    const calls = [];
    const ui = loadClient((url, init) => {
        calls.push({ url, init });
        return url.startsWith('/api/rounds/options') ? optionRequest.promise : startRequest.promise;
    });

    ui.clickStart(); ui.clickStart();
    await flush();
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /^\/api\/rounds\/options\?project_id=project-1$/);
    assert.equal(ui.button.disabled, true);

    optionRequest.resolve(response(true, optionsPayload()));
    await flush();
    assert.equal(ui.elements['round-launch-overlay'].hidden, false);
    assert.equal(ui.elements['round-launch-queries'].value, '12');
    assert.equal(ui.button.disabled, false);

    ui.confirm(); ui.confirm();
    await flush();
    assert.equal(calls.length, 2);
    assert.equal(calls[1].url, '/api/rounds/start');
    assert.equal(ui.elements['round-launch-confirm'].disabled, true);
    assert.equal(ui.elements['round-launch-cancel'].disabled, true);

    startRequest.resolve(response(true, { ok: true, redirect_url: '/rounds/?project=example&round=4&started=1' }));
    await flush();
    assert.deepEqual(ui.assigned, ['/rounds/?project=example&round=4&started=1']);
    assert.deepEqual(ui.events, ['da-round-launched']);
});

test('Round Start submits exactly the editable per-round settings', async () => {
    const calls = [];
    const ui = loadClient((url, init) => {
        calls.push({ url, init });
        return Promise.resolve(url.startsWith('/api/rounds/options')
            ? response(true, optionsPayload())
            : response(true, { ok: true, redirect_url: '/rounds/?round=4' }));
    });
    ui.clickStart(); await flush();
    ui.elements['round-launch-queries'].value = '17';
    ui.elements['round-launch-deep-research'].checked = false;
    ui.elements['round-launch-web-search'].checked = true;
    ui.elements['round-launch-reasoning-model'].value = 'claude-sonnet-4-6';
    ui.elements['round-launch-execution-model'].value = 'claude-haiku-4-5-20251001';
    ui.confirm(); await flush();

    assert.deepEqual(JSON.parse(calls[1].init.body), {
        project_id: 'project-1', num_queries: 17, deep_research: false, web_search: true,
        reasoning_model: 'claude-sonnet-4-6', execution_model: 'claude-haiku-4-5-20251001',
    });
    assert.doesNotMatch(calls[1].init.body, /README|task_description|working_directory|no_sandbox/);
});

test('launch failure stays in the settings dialog and restores its actions', async () => {
    assert.match(LAUNCH_TEMPLATE, /id="round-launch-error"[\s\S]{0,100}style="color: var\(--v2-danger, #b3261e\);"/);
    const ui = loadClient((url) => Promise.resolve(url.startsWith('/api/rounds/options')
        ? response(true, optionsPayload())
        : response(false, {
            ok: false, code: 'anthropic_api_key_missing',
            message: 'Anthropic API key not detected. Set ANTHROPIC_API_KEY and try again.',
        })));
    ui.clickStart(); await flush();
    ui.confirm(); await flush();

    assert.deepEqual(ui.assigned, []);
    assert.equal(ui.elements['round-launch-overlay'].hidden, false);
    assert.equal(ui.elements['round-launch-confirm'].disabled, false);
    assert.equal(ui.elements['round-launch-cancel'].disabled, false);
    assert.match(ui.elements['round-launch-error'].textContent, /ANTHROPIC_API_KEY/);
});

test('the shared client contains no mutable project content or path controls', () => {
    assert.doesNotMatch(SOURCE, /readme|task_description|project_context|working_directory|allowed_read_paths|no_sandbox/i);
});
