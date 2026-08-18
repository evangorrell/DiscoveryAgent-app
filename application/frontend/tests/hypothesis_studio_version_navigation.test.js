const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const HSEvents = require('../da_selection_ui/static/hypothesis_events.js');

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

const versionNavigationSource = sourceBetween(
    '    function viewedVersionResultIds(',
    '    // Per-hypothesis chat threads'
);
const restoreCardsSource = sourceBetween(
    '    async function restoreCurrentCardsFromHistory(',
    '    async function refreshAfterRegenerationCompletion('
);
const finishAndReviseSource = sourceBetween(
    '    function finishLlmEditRequest(',
    '    // ---- regenerate / manual edit / remove'
);
const pendingRestoreSource = sourceBetween(
    '    function applyPendingThreadFromStorage(',
    '    async function pollPendingThreadCompletion('
);

function hypothesis(index, version) {
    return {
        index,
        state_id: `h${index}-state-v${version}`,
        full_text: `Hypothesis ${index} Version ${version}`,
        thread_recoverable: true,
    };
}

function versionEvents({ includeH1V5 = false } = {}) {
    const events = [{
        kind: 'hypotheses',
        id: 'set-v1',
        hypotheses: [hypothesis(1, 1), hypothesis(2, 1)],
    }];
    [2, 3, 4].forEach((version) => events.push({
        kind: 'revision', id: `h1-v${version}`, index: 1,
        source: 'regenerated', hypothesis: hypothesis(1, version),
    }));
    [2, 3].forEach((version) => events.push({
        kind: 'revision', id: `h2-v${version}`, index: 2,
        source: 'regenerated', hypothesis: hypothesis(2, version),
    }));
    if (includeH1V5) events.push({
        kind: 'revision', id: 'h1-v5', resultEventId: 'h1-v5', index: 1,
        source: 'regenerated', sourceResultEventId: 'h1-v2',
        hypothesis: hypothesis(1, 5),
    });
    return events;
}

function deriveCards(state) {
    const active = HSEvents.computeActiveVersions(state.events);
    return Object.keys(active).map((key) => active[key].hypothesis);
}

function regenerationViewFor(state, activeHypothesis) {
    const idx = Number(activeHypothesis.index);
    const history = HSEvents.regenerationHistory(state.events, idx);
    let viewedPosition = Number(state.viewedRegenerationPositions[idx]);
    if (!Number.isInteger(viewedPosition) || viewedPosition < 0 ||
            viewedPosition >= history.results.length) {
        viewedPosition = history.activePosition;
    }
    const viewed = history.results[viewedPosition];
    return {
        results: history.results,
        activePosition: history.activePosition,
        viewedPosition,
        viewedResult: viewed,
        viewedResultEventId: viewed && viewed.eventId,
        viewedSnapshot: viewed && viewed.hypothesis,
        isViewingCurrent: viewedPosition === history.activePosition,
    };
}

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

function makeVersionHarness() {
    const state = {
        events: versionEvents(),
        currentRoundIndex: 0,
        viewedRegenerationPositions: { 1: 1, 2: 0 },
        focusIndex: 1,
        manualEditIndex: null,
        readOnly: false,
        regenerationRequests: {},
    };
    const calls = { remembered: 0, cards: 0, thread: 0, composer: 0 };
    const HSE = Object.assign({}, HSEvents, {
        normalizeHistoryEvents(data) { return data.events; },
    });
    const build = new Function(
        'state', 'HSE', 'deriveCards', 'regenerationViewFor',
        'isManualEditSaving', 'setComposerError', 'rememberStudioView',
        'renderCards', 'renderThread', 'applyComposer',
        `${versionNavigationSource}
         return {
             viewedVersionResultIds,
             restoreViewedVersionResultIds,
             setViewedVersionResultId,
             navigateRegenerationVersion,
         };`
    )(
        state,
        HSE,
        () => deriveCards(state),
        (h) => regenerationViewFor(state, h),
        () => false,
        () => {},
        () => { calls.remembered += 1; },
        () => { calls.cards += 1; },
        () => { calls.thread += 1; },
        () => { calls.composer += 1; }
    );
    return { state, calls, HSE, ...build };
}

test('a delayed history reconciliation preserves the newest cross-card version choices by durable ID', async () => {
    const harness = makeVersionHarness();
    const detail = deferred();
    let requestCount = 0;
    const restore = new Function(
        'state', 'apiJsonWithTimeout', 'applyHistoryList',
        'viewedVersionResultIds', 'deriveCards', 'regenerationViewFor',
        'HSE', 'nextEventId', 'restoreViewedVersionResultIds', 'hasHypotheses',
        `${restoreCardsSource}
         return restoreCurrentCardsFromHistory;`
    )(
        harness.state,
        async () => {
            requestCount += 1;
            if (requestCount === 1) return { resp: { ok: true }, data: { success: true } };
            return detail.promise;
        },
        () => {},
        harness.viewedVersionResultIds,
        () => deriveCards(harness.state),
        (h) => regenerationViewFor(harness.state, h),
        harness.HSE,
        () => 'unused',
        harness.restoreViewedVersionResultIds,
        () => true
    );

    const pendingRestore = restore();
    await new Promise((resolve) => setImmediate(resolve));

    // The scientist can browse every existing H2 version while a background
    // reconciliation request is waiting on the server.
    harness.navigateRegenerationVersion(2, 1);
    harness.navigateRegenerationVersion(2, 2);
    assert.equal(regenerationViewFor(harness.state, hypothesis(2, 3)).viewedResultEventId, 'h2-v3');

    detail.resolve({
        resp: { ok: true },
        data: {
            success: true,
            events: versionEvents({ includeH1V5: true }),
            storage_round_index: 0,
            round_index: 0,
            read_only: false,
        },
    });
    assert.equal(await pendingRestore, true);

    assert.equal(regenerationViewFor(harness.state, hypothesis(1, 5)).viewedResultEventId, 'h1-v2');
    assert.equal(regenerationViewFor(harness.state, hypothesis(2, 3)).viewedResultEventId, 'h2-v3');
    assert.equal(harness.state.focusIndex, 2);

    // Neither a later repaint nor the newly-created H1 version pins either card.
    [2, 1, 0].forEach((position) => harness.navigateRegenerationVersion(1, position));
    [1, 0].forEach((position) => harness.navigateRegenerationVersion(2, position));
    assert.equal(regenerationViewFor(harness.state, hypothesis(1, 5)).viewedResultEventId, 'set-v1');
    assert.equal(regenerationViewFor(harness.state, hypothesis(2, 3)).viewedResultEventId, 'set-v1');
});

function makeEditHarness({ succeeds = true } = {}) {
    const versions = makeVersionHarness();
    const state = Object.assign(versions.state, {
        composerMode: 'edit',
        busyIndex: null,
        busyKind: null,
        busyResultEventId: null,
    });
    const activeThreadRequests = {
        '1:h1-v4': {
            requestId: 40, kind: 'discuss', index: 1,
            resultEventId: 'h1-v4', stopped: false,
        },
    };
    const editResponse = deferred();
    let requestSequence = 50;
    const errors = [];
    const renderCalls = [];
    const els = { input: { value: 'Create Version 5' } };

    function currentThreadResultEventId(index) {
        const active = HSEvents.activeHypothesis(state.events, index);
        return active ? regenerationViewFor(state, active).viewedResultEventId : null;
    }
    function beginThreadRequest(index, kind, message, resultEventId) {
        const laneKey = `${index}:${resultEventId || ''}`;
        const active = {
            requestId: ++requestSequence,
            clientRequestId: `request-${requestSequence}`,
            kind, index, resultEventId, laneKey, stopped: false,
        };
        activeThreadRequests[laneKey] = active;
        return { active, laneKey, requestId: active.requestId, clientRequestId: active.clientRequestId,
            resultEventId, signal: undefined };
    }
    function finishThreadRequest(index, resultEventId, requestId) {
        const laneKey = `${index}:${resultEventId || ''}`;
        if (!activeThreadRequests[laneKey] || activeThreadRequests[laneKey].requestId !== requestId) return false;
        delete activeThreadRequests[laneKey];
        return true;
    }
    const sendRevise = new Function(
        'state', 'isPendingSelectedCard', 'selectedCardLockMessage', 'setComposerError',
        'hasAnyLlmEditActive', 'refreshSendEnabled', 'els', 'autoGrow',
        'activeMutationHypothesis', 'regenerationViewFor', 'hypothesisMarkdown',
        'hypothesisBodyMarkdown', 'beginThreadRequest', 'renderCards', 'renderThread',
        'applyComposer', 'apiJson', 'activeThreadRequests', 'removedHypothesisStateIds',
        'finishThreadRequest', 'sameThreadResultEventId', 'currentThreadResultEventId',
        'nextEventId', 'setViewedVersionResultId', 'isViewingLiveRound',
        'restoreCurrentCardsFromHistory', 'render', 'pageUnloading',
        `${finishAndReviseSource}
         return sendRevise;`
    )(
        state,
        () => false,
        () => '',
        (message) => { if (message) errors.push(message); },
        (index) => Object.values(activeThreadRequests).some((request) =>
            request && Number(request.index) === Number(index) && request.kind === 'revise'
        ),
        () => {},
        els,
        () => {},
        ({ index }) => HSEvents.activeHypothesis(state.events, index),
        (h) => regenerationViewFor(state, h),
        (h) => h.full_text,
        (h) => h.full_text,
        beginThreadRequest,
        () => renderCalls.push('cards'),
        () => renderCalls.push('thread'),
        () => renderCalls.push('composer'),
        async () => editResponse.promise,
        activeThreadRequests,
        new Set(),
        finishThreadRequest,
        (left, right) => String(left || '') === String(right || ''),
        currentThreadResultEventId,
        () => `local-${requestSequence}`,
        versions.setViewedVersionResultId,
        () => true,
        async () => true,
        () => renderCalls.push('render'),
        false
    );
    return {
        ...versions, state, activeThreadRequests, errors, renderCalls,
        editResponse, sendRevise,
        resolveEdit() {
            editResponse.resolve(succeeds ? {
                resp: { ok: true },
                data: {
                    success: true,
                    hypothesis: hypothesis(1, 5),
                    result_event_id: 'h1-v5',
                    source_result_event_id: 'h1-v2',
                    created_version_number: 5,
                },
            } : { resp: { ok: false }, data: { error: 'Edit failed.' } });
        },
    };
}

test('Edit provenance stays on H1 V2 while H2 navigation remains mutable and the held H1 V4 Chat survives', async () => {
    const harness = makeEditHarness();
    const editing = harness.sendRevise('Create Version 5', 1);
    assert.equal(harness.state.busyResultEventId, 'h1-v2');

    harness.navigateRegenerationVersion(2, 0);
    harness.navigateRegenerationVersion(2, 1);
    harness.navigateRegenerationVersion(2, 2);
    harness.resolveEdit();
    await editing;

    assert.equal(harness.state.composerMode, 'chat');
    assert.equal(harness.state.busyIndex, null);
    assert.equal(harness.state.busyKind, null);
    assert.equal(regenerationViewFor(harness.state, hypothesis(1, 5)).viewedResultEventId, 'h1-v5');
    assert.equal(regenerationViewFor(harness.state, hypothesis(2, 3)).viewedResultEventId, 'h2-v3');
    assert.equal(harness.state.focusIndex, 2);
    assert.ok(harness.activeThreadRequests['1:h1-v4']);
    assert.equal(harness.activeThreadRequests['1:h1-v2'], undefined);

    [2, 1].forEach((position) => harness.navigateRegenerationVersion(1, position));
    [1, 0].forEach((position) => harness.navigateRegenerationVersion(2, position));
    const beforeLateChat = Object.assign({}, harness.state.viewedRegenerationPositions);
    harness.state.events.push({
        kind: 'message', role: 'assistant', content: 'Held V4 answer',
        hypothesisIndex: 1, resultEventId: 'h1-v4',
    });
    delete harness.activeThreadRequests['1:h1-v4'];
    assert.deepEqual(harness.state.viewedRegenerationPositions, beforeLateChat);
});

test('failed Edit clears operation ownership without moving either card version', async () => {
    const harness = makeEditHarness({ succeeds: false });
    const editing = harness.sendRevise('Fail safely', 1);
    harness.navigateRegenerationVersion(2, 2);
    const before = Object.assign({}, harness.state.viewedRegenerationPositions);
    harness.resolveEdit();
    await editing;

    assert.equal(harness.state.composerMode, 'chat');
    assert.equal(harness.state.busyIndex, null);
    assert.equal(harness.activeThreadRequests['1:h1-v2'], undefined);
    assert.deepEqual(harness.state.viewedRegenerationPositions, before);
    assert.deepEqual(harness.errors, ['Edit failed.']);
});

test('version navigation remains enabled on the regenerating card and every other card', () => {
    const harness = makeVersionHarness();
    harness.state.regenerationRequests[1] = { requestId: 99 };

    harness.navigateRegenerationVersion(1, 2);
    harness.navigateRegenerationVersion(2, 2);

    assert.equal(regenerationViewFor(harness.state, hypothesis(1, 4)).viewedResultEventId, 'h1-v3');
    assert.equal(regenerationViewFor(harness.state, hypothesis(2, 3)).viewedResultEventId, 'h2-v3');
});

test('pending request restoration never owns focus or viewed-version navigation', () => {
    assert.doesNotMatch(pendingRestoreSource, /state\.focusIndex\s*=/);
    assert.doesNotMatch(pendingRestoreSource, /state\.viewedRegenerationPositions\s*\[/);
    assert.match(pendingRestoreSource, /resultEventId: pending\.resultEventId/);
    assert.match(pendingRestoreSource, /busyResultEventId = pending\.resultEventId/);
});
