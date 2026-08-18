const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const studioSource = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'hypothesis_studio.js'),
    'utf8'
);

function sourceBetween(startMarker, endMarker) {
    const start = studioSource.indexOf(startMarker);
    const end = studioSource.indexOf(endMarker, start);
    assert.notEqual(start, -1, 'missing ' + startMarker);
    assert.notEqual(end, -1, 'missing ' + endMarker);
    return studioSource.slice(start, end);
}

const refreshSource = sourceBetween(
    '    async function refreshAfterRegenerationCompletion(',
    '    async function restoreRequestedRoundFromLocation('
);
const pollSource = sourceBetween(
    '    function pollRegenerationCompletion(',
    '    function pollGenerationCompletion('
);
const settlementSource = sourceBetween(
    '    function settleRegenerationRequest(',
    '    function visibleExpandedHypotheses('
);
const completedGenerationSource = sourceBetween(
    '    function applyCompletedLiveGeneration(',
    '    async function generate('
);
const cancelSource = sourceBetween(
    '    function cancelRegenerate(',
    '    function openManualEdit('
);
const renderCardSource = sourceBetween(
    '    function renderCard(',
    '    function regenerationVersionControls('
);

class FakeCardElement {
    constructor(tag, className, text) {
        this.tag = tag;
        this.className = className || '';
        this.text = text || '';
        this.children = [];
        this.dataset = {};
        this.attributes = {};
        this.listeners = {};
        this.classList = {
            add: (name) => {
                if (!this.className.split(/\s+/).includes(name)) {
                    this.className = (this.className + ' ' + name).trim();
                }
            },
        };
    }

    appendChild(child) { this.children.push(child); return child; }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
}

function descendants(node) {
    return [node].concat((node.children || []).flatMap(descendants));
}

function makeRenderHarness() {
    const state = {
        readOnly: false,
        focusIndex: null,
        expanded: {},
        manualEditIndex: null,
        busyIndex: null,
        busyKind: null,
        regenerationRequests: { 1: { requestId: 7 } },
    };
    const renderCard = new Function(
        'state', 'regenerationViewFor', 'isRegeneratingHypothesis', 'el',
        'activitySpinner', 'cancelRegenerate', 'cardTitle', 'esc',
        'focusCard', 'buildOverflow',
        `${renderCardSource}\nreturn renderCard;`
    )(
        state,
        (hypothesis) => ({
            viewedSnapshot: hypothesis,
            viewedPosition: 0,
            results: [{}],
        }),
        (index) => !!state.regenerationRequests[Number(index)],
        (tag, className, text) => new FakeCardElement(tag, className, text),
        (className) => new FakeCardElement('span', className),
        () => {},
        (hypothesis) => hypothesis.title,
        (value) => value,
        () => {},
        () => new FakeCardElement('div', 'hs-card-overflow')
    );
    return { state, renderCard };
}

test('active regeneration replaces the complete card interior with loading and Cancel', () => {
    const { renderCard } = makeRenderHarness();
    const card = renderCard({ index: 1, title: 'Old hypothesis title' }, null);
    const nodes = descendants(card);

    assert.equal(card.className.includes('is-regenerating'), true);
    assert.equal(card.children.length, 2);
    assert.equal(nodes.some((node) => node.className.includes('hs-card-regenerating')), true);
    assert.equal(nodes.some((node) => node.className.includes('hs-card-spinner')), true);
    assert.equal(nodes.some((node) => node.text === 'Regenerating hypothesis...'), true);
    assert.equal(nodes.some((node) => node.text === 'Cancel'), true);
    assert.equal(nodes.some((node) => node.text === 'Old hypothesis title'), false);
    assert.equal(nodes.some((node) => node.className.includes('hs-card-head')), false);
    assert.equal(nodes.some((node) => node.className.includes('hs-card-overflow')), false);
    assert.equal(nodes.some((node) => node.className.includes('hs-regeneration-nav')), false);
    assert.equal(nodes.some((node) => node.className.includes('hs-card-expand')), false);
    assert.equal(nodes.some((node) => node.className.includes('hs-card-select')), false);
});

test('terminal cleanup restores the normal card while other cards remain unaffected', () => {
    const { state, renderCard } = makeRenderHarness();
    const otherNodes = descendants(renderCard({ index: 2, title: 'Other hypothesis' }, null));
    assert.equal(otherNodes.some((node) => node.text === 'Other hypothesis'), true);
    assert.equal(otherNodes.some((node) => node.className.includes('hs-card-overflow')), true);
    assert.equal(otherNodes.some((node) => node.className.includes('hs-card-expand')), true);
    assert.equal(otherNodes.some((node) => node.className.includes('hs-card-select')), true);

    delete state.regenerationRequests[1];
    const restoredNodes = descendants(renderCard({ index: 1, title: 'New hypothesis version' }, null));
    assert.equal(restoredNodes.some((node) => node.className.includes('hs-card-regenerating')), false);
    assert.equal(restoredNodes.some((node) => node.text === 'New hypothesis version'), true);
    assert.equal(restoredNodes.some((node) => node.className.includes('hs-card-overflow')), true);
    assert.equal(restoredNodes.some((node) => node.className.includes('hs-card-expand')), true);
    assert.equal(restoredNodes.some((node) => node.className.includes('hs-card-select')), true);
});

function makeSettlementHarness() {
    const first = { requestId: 1 };
    const second = { requestId: 2 };
    const state = {
        regenerationRequests: { 1: first, 2: second },
        busyIndex: null,
        busyKind: null,
        busyResultEventId: null,
    };
    const calls = { cleared: [], refreshed: 0, persisted: 0 };
    const factories = new Function(
        'state', 'regenerationRequest', 'clearPendingRegeneration', 'refreshSendEnabled',
        'persistStudioSnapshot',
        `${settlementSource}\nreturn { settleRegenerationRequest, settleFullGenerationMutationState };`
    )(
        state,
        (index) => state.regenerationRequests[Number(index)] || null,
        (index) => { calls.cleared.push(index == null ? null : Number(index)); },
        () => { calls.refreshed += 1; },
        () => { calls.persisted += 1; }
    );
    return { state, calls, first, second, ...factories };
}

test('terminal regeneration releases the exact card owner and immediately restores menu eligibility', () => {
    const harness = makeSettlementHarness();
    assert.equal(harness.settleRegenerationRequest(1, harness.first), true);
    assert.equal(harness.state.regenerationRequests[1], undefined);
    assert.equal(harness.state.regenerationRequests[2], harness.second);
    assert.deepEqual(harness.calls.cleared, [1]);
    assert.equal(harness.calls.refreshed, 1);
    assert.equal(harness.calls.persisted, 1);
});

test('a late cancelled result cannot clear or re-lock a newer regeneration owner', () => {
    const harness = makeSettlementHarness();
    const cancelled = harness.first;
    const successor = { requestId: 3 };
    harness.state.regenerationRequests[1] = successor;
    assert.equal(harness.settleRegenerationRequest(1, cancelled), false);
    assert.equal(harness.state.regenerationRequests[1], successor);
    assert.deepEqual(harness.calls.cleared, []);
});

test('cancelling regeneration immediately releases menu ownership without waiting for transport', () => {
    const harness = makeSettlementHarness();
    const calls = { cancelled: [], api: [], renders: 0, persisted: 0 };
    const cancel = new Function(
        'regenerationRequest', 'rememberCanceledRegeneration', 'apiJson',
        'settleRegenerationRequest', 'renderCards', 'persistStudioSnapshot',
        `${cancelSource}\nreturn cancelRegenerate;`
    )(
        (index) => harness.state.regenerationRequests[Number(index)] || null,
        (index) => { calls.cancelled.push(Number(index)); },
        (...args) => { calls.api.push(args); return new Promise(() => {}); },
        harness.settleRegenerationRequest,
        () => { calls.renders += 1; },
        () => { calls.persisted += 1; }
    );

    cancel(1);

    assert.equal(harness.first.canceled, true);
    assert.equal(harness.state.regenerationRequests[1], undefined);
    assert.equal(harness.state.regenerationRequests[2], harness.second);
    assert.deepEqual(calls.cancelled, [1]);
    assert.equal(calls.api.length, 1);
    assert.equal(calls.api[0][0], '/api/cancel_regeneration');
    assert.equal(calls.renders, 1);
});

test('fresh generation installs the new cards after clearing orphaned per-card regeneration locks', () => {
    const harness = makeSettlementHarness();
    const state = Object.assign(harness.state, {
        events: [{ kind: 'hypotheses', hypotheses: [{ index: 9 }] }],
        viewedRegenerationPositions: { 1: 2 },
        generationId: 'old-generation',
        focusIndex: 1,
        expanded: { 1: true },
        manualEditIndex: 1,
        manualEditDraft: 'old draft',
    });
    const applyCompleted = new Function(
        'state', 'settleFullGenerationMutationState',
        `${completedGenerationSource}\nreturn applyCompletedLiveGeneration;`
    )(state, harness.settleFullGenerationMutationState);
    const generatedEvents = [{
        kind: 'hypotheses', id: 'new-set',
        hypotheses: [{ index: 1 }, { index: 2 }, { index: 3 }],
    }];

    applyCompleted(generatedEvents, 'new-generation');

    assert.equal(state.events[0].hypotheses.length, 3);
    assert.deepEqual(state.regenerationRequests, {});
    assert.equal(state.generationId, 'new-generation');
    assert.equal(state.focusIndex, null);
    assert.deepEqual(state.expanded, {});
    assert.equal(state.manualEditIndex, null);
});

function makeRefreshHarness({ focusIndex = 2, restoreSucceeds = true } = {}) {
    const state = {
        focusIndex,
        expanded: { 2: true },
        viewedRegenerationPositions: { 1: 0, 2: 0 },
        manualEditIndex: 2,
        manualEditDraft: 'unsaved draft',
        readOnly: false,
    };
    const calls = {
        errors: [], cards: [], restoreOptions: [], thread: 0, composer: 0, sidebar: 0, persisted: 0,
    };
    const refresh = new Function(
        'state', 'normalizedExpanded', 'normalizedRegenerationPositions', 'els',
        'restoreCurrentCardsFromHistory', 'setComposerError', 'renderCards',
        'cardIndexExists', 'renderThread', 'applyComposer', 'renderSidebar',
        'persistStudioSnapshot',
        `${refreshSource}\nreturn refreshAfterRegenerationCompletion;`
    )(
        state,
        (value) => Object.assign({}, value),
        (value) => Object.assign({}, value),
        { cards: { querySelector: () => ({ scrollTop: 47 }) } },
        async (options) => {
            calls.restoreOptions.push(options);
            return restoreSucceeds;
        },
        (message) => calls.errors.push(message),
        (options) => calls.cards.push(options),
        () => true,
        () => { calls.thread += 1; },
        () => { calls.composer += 1; },
        () => { calls.sidebar += 1; },
        () => { calls.persisted += 1; }
    );
    return { state, calls, refresh };
}

test('completion refresh preserves the initialized Studio and unrelated expanded card state', async () => {
    const harness = makeRefreshHarness({ focusIndex: 2 });
    assert.equal(await harness.refresh(1), true);

    assert.equal(harness.state.focusIndex, 2);
    assert.deepEqual(harness.state.expanded, { 2: true });
    assert.deepEqual(harness.state.viewedRegenerationPositions, { 1: 0, 2: 0 });
    assert.equal(harness.state.manualEditIndex, 2);
    assert.equal(harness.state.manualEditDraft, 'unsaved draft');
    assert.deepEqual(harness.calls.cards, [{ restoreScrollTop: 47 }]);
    assert.deepEqual(harness.calls.restoreOptions, [{ followNewCurrentIndex: 1 }]);
    assert.equal(harness.calls.thread, 0);
    assert.equal(harness.calls.composer, 0);
    assert.equal(harness.calls.sidebar, 1);
    assert.equal(harness.calls.persisted, 1);
    assert.deepEqual(harness.calls.errors, [null]);
});

test('completion on the focused card refreshes its new current version locally', async () => {
    const harness = makeRefreshHarness({ focusIndex: 1 });
    assert.equal(await harness.refresh(1), true);
    assert.equal(harness.calls.thread, 1);
    assert.equal(harness.calls.composer, 1);
    assert.deepEqual(harness.state.viewedRegenerationPositions, { 1: 0, 2: 0 });
});

test('history refresh failure leaves the shell state intact and reports a local error', async () => {
    const harness = makeRefreshHarness({ focusIndex: 2, restoreSucceeds: false });
    assert.equal(await harness.refresh(1), false);
    assert.deepEqual(harness.calls.cards, [{ restoreScrollTop: 47 }]);
    assert.equal(harness.calls.thread, 0);
    assert.equal(harness.calls.sidebar, 0);
    assert.match(harness.calls.errors[0], /Could not refresh/);
});

function makePollHarness(status) {
    const state = { regenerationRequests: { 1: { requestId: 9 } } };
    const calls = { cleared: 0, refreshed: 0, errors: [], cards: 0 };
    let scheduled = null;
    const poll = new Function(
        'window', 'state', 'regenerationRequest', 'generationStatus',
        'settleRegenerationRequest', 'refreshAfterRegenerationCompletion',
        'setComposerError', 'renderCards',
        `${pollSource}\nreturn pollRegenerationCompletion;`
    )(
        { setTimeout(callback) { scheduled = callback; } },
        state,
        (index) => state.regenerationRequests[index],
        async () => status,
        (index, expected) => {
            if (state.regenerationRequests[index] !== expected) return false;
            delete state.regenerationRequests[index];
            calls.cleared += 1;
            return true;
        },
        async () => { calls.refreshed += 1; },
        (message) => calls.errors.push(message),
        () => { calls.cards += 1; }
    );
    return {
        state,
        calls,
        poll,
        async runScheduled() { assert.ok(scheduled); await scheduled(); },
    };
}

test('poll completion reconciles the card without invoking global initialization', async () => {
    const harness = makePollHarness({ regeneration: { in_progress: false, index: 1 } });
    harness.poll(9, 1);
    await harness.runScheduled();
    assert.deepEqual(harness.state.regenerationRequests, {});
    assert.equal(harness.calls.cleared, 1);
    assert.equal(harness.calls.refreshed, 1);
    assert.doesNotMatch(pollSource, /initialize\s*\(/);
    assert.doesNotMatch(pollSource, /showLoading\s*\(/);
});

test('cancelled poll ownership cannot trigger a completion refresh', async () => {
    const harness = makePollHarness({ regeneration: { in_progress: false, index: 1 } });
    harness.poll(9, 1);
    delete harness.state.regenerationRequests[1];
    await harness.runScheduled();
    assert.equal(harness.calls.cleared, 0);
    assert.equal(harness.calls.refreshed, 0);
});

test('a terminal failed regeneration uses the same local durable reconciliation path', async () => {
    const harness = makePollHarness({
        status: 'failed',
        regeneration: { in_progress: false, index: 1 },
    });
    harness.poll(9, 1);
    await harness.runScheduled();
    assert.equal(harness.calls.refreshed, 1);
    assert.deepEqual(harness.calls.errors, ['Could not regenerate that hypothesis.']);
    assert.equal(harness.calls.cards, 1);
});
