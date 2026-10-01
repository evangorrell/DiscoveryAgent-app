const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../da_selection_ui/static/project_round_action.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../da_selection_ui/static/project_round_action.css'), 'utf8');

function pending(round = 1, selectionId = 'selection-1') {
    return { success: true, is_current: true, status: 'selected', has_selected: true,
        ready_to_start: true, read_only: false, storage_round_index: round - 1,
        target_round_number: round, events: [{ type: 'hypothesis_selected', id: selectionId }] };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function harness({ detail = pending(), storage = new Map(), reducedMotion = false, project = 'project-1', fetchImpl } = {}) {
    const classes = new Set();
    const attributes = new Map();
    const buttonEvents = {};
    const button = { hidden: true, textContent: '',
        classList: { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x) },
        setAttribute: (k,v) => attributes.set(k,v), removeAttribute: k => attributes.delete(k),
        addEventListener: (k,fn) => { buttonEvents[k] = fn; },
    };
    const surface = { hidden: true };
    const windowEvents = {};
    const documentEvents = {};
    const calls = [];
    const timers = [];
    const window = {
        discoveryProjectContext: project ? { projectId: project, projectSlug: project } : {},
        sessionStorage: { getItem: key => storage.get(key), setItem: (key,value) => storage.set(key,value) },
        matchMedia: () => ({ matches: reducedMotion }),
        addEventListener: (key, fn) => { windowEvents[key] = fn; },
        setInterval: fn => timers.push(fn),
    };
    let current = detail;
    vm.runInNewContext(source, {
        window,
        document: {
            getElementById: id => id === 'project-start-round' ? button : surface,
            hidden: false, addEventListener: (key, fn) => { documentEvents[key] = fn; },
        },
        requestAnimationFrame: fn => fn(),
        fetch: async url => {
            calls.push(url);
            if (fetchImpl) return fetchImpl(url);
            return { ok: true, json: async () => url === '/api/hypothesis_history'
                ? { success: true, rounds: current ? [current] : [] } : current };
        },
    });
    return { button, surface, classes, attributes, storage, calls, timers, buttonEvents,
        setDetail: value => { current = value; },
        event: name => windowEvents[name](),
        refresh: () => windowEvents.focus(),
    };
}

test('pending selection shows correct Round 1 and Round 5 in semantic shared control', async () => {
    const h = harness(); await flush();
    assert.equal(h.button.hidden, false);
    assert.equal(h.surface.hidden, false);
    assert.equal(h.button.textContent, 'Start Round 1');
    h.setDetail(pending(5, 'selection-5')); h.refresh(); await flush();
    assert.equal(h.button.textContent, 'Start Round 5');
    assert.equal(h.attributes.get('data-start-round-number'), '5');
});

test('global context does not fetch or show any project CTA', async () => {
    const h = harness({ project: null }); await flush();
    assert.equal(h.button.hidden, true);
    assert.equal(h.calls.length, 0);
});

test('cleared, blocked, running, completed and historical selections never show a launch CTA', async () => {
    for (const detail of [null, { ...pending(), ready_to_start: false }, { ...pending(), has_selected: false },
        { ...pending(), status: 'running' }, { ...pending(), status: 'completed' },
        { ...pending(), is_current: false }, { ...pending(), read_only: true }]) {
        const h = harness({ detail }); await flush();
        assert.equal(h.button.hidden, true);
    }
});

test('selection refresh hides old action immediately and shows only the verified replacement', async () => {
    const h = harness(); await flush();
    h.setDetail(null); h.event('da-pending-selection-changed');
    assert.equal(h.button.hidden, true); await flush();
    assert.equal(h.button.hidden, true);
    h.setDetail(pending(2, 'replacement')); h.event('da-pending-selection-changed'); await flush();
    assert.equal(h.button.textContent, 'Start Round 2');
});

test('successful launch hides immediately and rejects a stale in-flight refresh', async () => {
    let resolve;
    let hold = false;
    const h = harness({ fetchImpl: async url => {
        if (hold) return new Promise(done => { resolve = done; });
        return { ok: true, json: async () => url.endsWith('/0') ? pending() : { rounds: [pending()] } };
    } });
    await flush(); hold = true; h.refresh(); await flush();
    h.event('da-round-launched');
    assert.equal(h.button.hidden, true);
    resolve({ ok: true, json: async () => ({ rounds: [pending()] }) }); await flush();
    h.refresh(); await flush();
    assert.equal(h.button.hidden, true);
});

test('network/API errors fail closed rather than leave a stale launch action', async () => {
    const h = harness({ fetchImpl: async () => { throw new Error('offline'); } });
    await flush(); assert.equal(h.button.hidden, true);
});

test('pulse is finite and does not replay on polling, chat renders or project navigation', async () => {
    const storage = new Map();
    const h = harness({ storage }); await flush();
    assert.ok(h.classes.has('is-attention-cue'));
    h.buttonEvents.animationend();
    h.refresh(); await flush();
    assert.equal(h.classes.has('is-attention-cue'), false);
    const nextPage = harness({ storage }); await flush();
    assert.equal(nextPage.button.hidden, false);
    assert.equal(nextPage.classes.has('is-attention-cue'), false);
    nextPage.setDetail(pending(1, 'new-selection'));
    nextPage.event('da-pending-selection-changed'); await flush();
    assert.ok(nextPage.classes.has('is-attention-cue'));
    assert.match(css, /animation: project-round-attention 3s ease-out 1;/);
});

test('reduced-motion preference keeps static green CTA and consumes cue across navigation', async () => {
    const storage = new Map();
    const h = harness({ storage, reducedMotion: true }); await flush();
    assert.equal(h.button.hidden, false);
    assert.equal(h.classes.has('is-attention-cue'), false);
    const next = harness({ storage }); await flush();
    assert.equal(next.classes.has('is-attention-cue'), false);
    assert.match(css, /prefers-reduced-motion: reduce[\s\S]*animation: none;/);
});

test('cue identity is project-scoped, not shared between different projects', async () => {
    const storage = new Map();
    harness({ storage }); await flush();
    const other = harness({ storage, project: 'another-project' }); await flush();
    assert.ok(other.classes.has('is-attention-cue'));
});
