const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const studioPath = path.join(__dirname, '..', 'da_selection_ui', 'static', 'hypothesis_studio.js');
const studioSource = fs.readFileSync(studioPath, 'utf8');

function sourceBetween(startMarker, endMarker) {
    const start = studioSource.indexOf(startMarker);
    const end = studioSource.indexOf(endMarker, start);
    assert.notEqual(start, -1, 'missing ' + startMarker);
    assert.notEqual(end, -1, 'missing ' + endMarker);
    return studioSource.slice(start, end);
}

const requestKindSource = sourceBetween(
    '    function threadLaneKey(',
    '    function normalizeQueuedThreadMessages('
);
const queueSource = sourceBetween(
    '    function normalizeQueuedThreadMessages(',
    '    function hasActiveHypothesisRequest('
);
const beginSource = sourceBetween(
    '    function beginThreadRequest(',
    '    function finishThreadRequest('
);
const finishSource = sourceBetween(
    '    function finishThreadRequest(',
    '    function stopActiveThreadRequest('
);
const stopSource = sourceBetween(
    '    function stopActiveThreadRequest(',
    '    function promoteNextQueuedThreadMessage('
);
const promoteSource = sourceBetween(
    '    function promoteNextQueuedThreadMessage(',
    '    async function sendDiscuss('
);
const discussSource = sourceBetween(
    '    async function sendDiscuss(',
    '    async function sendRevise('
);
const submitSource = sourceBetween(
    '    async function submitThreadMessage(',
    '    function beginThreadRequest('
);
const onSubmitSource = sourceBetween(
    '    async function onSubmit(',
    '    function isFocusedThreadPending('
);
const resultViewSource = sourceBetween(
    '    function regenerationViewForResult(',
    '    function navigateRegenerationVersion('
);
const refreshSource = sourceBetween(
    '    function refreshSendEnabled(',
    '    // ---- wiring'
);

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}

function makeSchedulerHarness({
    abortRejects = true,
    resultIds = ['result-1'],
    activeResultEventId = 'result-1',
    viewedResultEventId = activeResultEventId,
} = {}) {
    const state = {
        events: [],
        focusIndex: 1,
        pendingThreadRequests: {},
        queuedThreadMessages: {},
        regenerationRequests: {},
        busyIndex: null,
        busyKind: null,
        busyResultEventId: null,
        generating: false,
        readOnly: false,
        composerMode: 'chat',
    };
    const chatRequests = [];
    let requestSequence = 0;
    let viewedResultId = viewedResultEventId;

    function regenerationViewFor() {
        const results = resultIds.map((eventId) => ({
            eventId,
            threadRecoverable: true,
            hypothesis: { index: 1, full_text: eventId },
        }));
        const activePosition = Math.max(0, resultIds.indexOf(activeResultEventId));
        const viewedPosition = Math.max(0, resultIds.indexOf(viewedResultId));
        const viewed = results[viewedPosition];
        return {
            results,
            activePosition,
            viewedPosition,
            viewedResult: viewed,
            viewedResultEventId: viewed.eventId,
            threadRecoverable: true,
            viewedSnapshot: viewed.hypothesis,
            isViewingCurrent: viewedPosition === activePosition,
        };
    }

    function apiJson(url, method, body, signal) {
        if (url === '/api/cancel_hypothesis_thread') {
            return Promise.resolve({ resp: { ok: true }, data: { success: true } });
        }
        assert.equal(url, '/api/chat');
        const pending = deferred();
        const record = { body, pending };
        chatRequests.push(record);
        if (signal && abortRejects) {
            signal.addEventListener('abort', () => {
                const error = new Error('aborted');
                error.name = 'AbortError';
                pending.reject(error);
            }, { once: true });
        }
        return pending.promise;
    }

    const build = new Function(
        'state', 'window', 'makeClientRequestId', 'rememberPendingThread',
        'refreshSendEnabled', 'clearPendingThread', 'renderQueuedMessages',
        'persistStudioSnapshot', 'isRegeneratingHypothesis',
        'activeMutationHypothesis', 'regenerationViewFor', 'setComposerError',
        'renderCards', 'renderThread', 'applyComposer', 'apiJson',
        'readPendingThread',
        `let activeThreadRequestSeq = 0;
         const activeThreadRequests = {};
         let pageUnloading = false;
         ${requestKindSource}
         ${queueSource}
         ${resultViewSource}
         ${beginSource}
         ${finishSource}
         ${stopSource}
         ${promoteSource}
         ${discussSource}
         return {
             activeThreadRequests,
             beginThreadRequest,
             finishThreadRequest,
             threadLaneKey,
             hasPendingThread,
             threadRequestKind,
             hasAnyLlmEditActive,
             normalizeQueuedThreadMessages,
             getQueuedThreadMessages,
             queueThreadMessage,
             removeQueuedThreadMessage,
             sendDiscuss,
             stopActiveThreadRequest,
         };`
    )(
        state,
        { AbortController },
        () => 'request-' + (++requestSequence),
        () => {},
        () => {},
        () => {},
        () => {},
        () => {},
        () => false,
        ({ index }) => ({ index }),
        regenerationViewFor,
        () => {},
        () => {},
        () => {},
        () => {},
        apiJson,
        (index, resultEventId) => {
            const pending = Object.values(state.pendingThreadRequests).find((entry) =>
                entry.index === index && entry.resultEventId === resultEventId
            );
            return pending ? { resultEventId: pending.resultEventId } : null;
        }
    );

    return {
        state,
        chatRequests,
        setViewedResultEventId(value) { viewedResultId = value; },
        ...build,
    };
}

function resolveChat(record, reply, sourceResultEventId = 'result-1') {
    record.pending.resolve({
        resp: { ok: true },
        data: { success: true, reply, source_result_event_id: sourceResultEventId },
    });
}

async function flushAsyncWork() {
    await new Promise((resolve) => setImmediate(resolve));
}

test('normal Chat follow-ups execute sequentially in deterministic FIFO transcript order', async () => {
    const harness = makeSchedulerHarness();
    const first = harness.sendDiscuss('A', 1);
    harness.queueThreadMessage(1, 'B');
    harness.queueThreadMessage(1, 'C');

    assert.equal(harness.chatRequests.length, 1);
    assert.deepEqual(harness.getQueuedThreadMessages(1).map((item) => item.text), ['B', 'C']);

    resolveChat(harness.chatRequests[0], 'Answer A');
    await first;
    assert.equal(harness.chatRequests.length, 2);
    assert.equal(harness.chatRequests[1].body.message, 'B');

    resolveChat(harness.chatRequests[1], 'Answer B');
    await flushAsyncWork();
    assert.equal(harness.chatRequests.length, 3);
    assert.equal(harness.chatRequests[2].body.message, 'C');

    resolveChat(harness.chatRequests[2], 'Answer C');
    await flushAsyncWork();

    assert.deepEqual(
        harness.state.events.map((event) => [event.role, event.content]),
        [
            ['user', 'A'],
            ['assistant', 'Answer A'],
            ['user', 'B'],
            ['assistant', 'Answer B'],
            ['user', 'C'],
            ['assistant', 'Answer C'],
        ]
    );
    assert.equal(harness.chatRequests.length, 3);
    assert.deepEqual(harness.getQueuedThreadMessages(1), []);
});

function makeSubmissionHarness({
    focusIndex = 1,
    mode = 'chat',
    pendingByIndex = {},
    pendingByLane = {},
    viewedResultEventId = 'result-1',
    message = 'B',
} = {}) {
    const state = {
        readOnly: false,
        focusIndex,
        composerMode: mode,
        generating: false,
        busyIndex: null,
        busyKind: null,
        busyResultEventId: null,
    };
    const activeThreadRequests = {};
    Object.keys(pendingByIndex).forEach((key) => {
        activeThreadRequests[key] = { kind: pendingByIndex[key] };
    });
    const queued = [];
    const discussed = [];
    const revised = [];
    const errors = [];
    const pendingKind = (index, resultEventId) =>
        pendingByLane[index + ':' + String(resultEventId || '')] || pendingByIndex[index] || null;
    const els = { input: { value: message, focus() {} } };
    const submit = new Function(
        'state', 'els', 'activeThreadRequests', 'isPendingSelectedCard',
        'selectedCardLockMessage', 'setComposerError', 'hasPendingThread',
        'threadRequestKind', 'currentThreadResultEventId', 'queueThreadMessage',
        'activeThreadRequest', 'isLlmEditActive', 'hasAnyLlmEditActive',
        'autoGrow', 'renderQueuedMessages',
        'refreshSendEnabled', 'persistStudioSnapshot', 'isRegeneratingHypothesis',
        'sendRevise', 'sendDiscuss',
        `${submitSource}\nreturn submitThreadMessage;`
    )(
        state,
        els,
        activeThreadRequests,
        () => false,
        () => '',
        (error) => { if (error) errors.push(error); },
        (index, resultEventId) => !!pendingKind(index, resultEventId),
        (index, resultEventId) => pendingKind(index, resultEventId) ||
            (activeThreadRequests[index] || {}).kind || null,
        () => viewedResultEventId,
        (index, text) => { queued.push([index, text]); return true; },
        (index, resultEventId) => {
            const active = activeThreadRequests[index];
            return active && (!resultEventId || resultEventId === viewedResultEventId) ? active : null;
        },
        (index, resultEventId) => pendingKind(index, resultEventId) === 'revise',
        (index) => Object.keys(pendingByLane).some((key) =>
            Number(key.split(':', 1)[0]) === Number(index) && pendingByLane[key] === 'revise'
        ) || pendingByIndex[index] === 'revise',
        () => {},
        () => {},
        () => {},
        () => {},
        () => false,
        async (text, index) => {
            revised.push([index, text, viewedResultEventId]);
            els.input.value = '';
        },
        async (text, index) => { discussed.push([index, text]); }
    );
    return { state, els, queued, discussed, revised, errors, submit };
}

test('submit accepts Chat B while A is active, but preserves drafts blocked by active Edit', async () => {
    const chat = makeSubmissionHarness({ pendingByIndex: { 1: 'discuss' } });
    await chat.submit();
    assert.deepEqual(chat.queued, [[1, 'B']]);
    assert.equal(chat.els.input.value, '');

    const editLockedChat = makeSubmissionHarness({ pendingByIndex: { 1: 'revise' } });
    await editLockedChat.submit();
    assert.deepEqual(editLockedChat.queued, []);
    assert.equal(editLockedChat.els.input.value, 'B');

    const secondEdit = makeSubmissionHarness({ mode: 'edit', pendingByIndex: { 1: 'revise' } });
    await secondEdit.submit();
    assert.deepEqual(secondEdit.queued, []);
    assert.equal(secondEdit.els.input.value, 'B');

    const isolated = makeSubmissionHarness({ focusIndex: 2, pendingByIndex: { 1: 'discuss' } });
    await isolated.submit();
    assert.deepEqual(isolated.discussed, [[2, 'B']]);
});

test('Version 2 Chat does not block Edit with LLM on Version 1', async () => {
    const harness = makeSubmissionHarness({
        mode: 'edit',
        viewedResultEventId: 'result-1',
        pendingByLane: { '1:result-2': 'discuss' },
        message: 'Revise Version 1 only',
    });

    await harness.submit();

    assert.deepEqual(harness.revised, [[1, 'Revise Version 1 only', 'result-1']]);
    assert.deepEqual(harness.errors, []);
    assert.equal(harness.els.input.value, '');
});

test('same-version Chat still blocks Edit because both operations share one ordered thread', async () => {
    const harness = makeSubmissionHarness({
        mode: 'edit',
        viewedResultEventId: 'result-1',
        pendingByLane: { '1:result-1': 'discuss' },
        message: 'Unsafe concurrent edit',
    });

    await harness.submit();

    assert.deepEqual(harness.revised, []);
    assert.equal(harness.els.input.value, 'Unsafe concurrent edit');
    assert.deepEqual(harness.errors, [
        'Wait for the active DiscoveryAgent response to finish before editing.',
    ]);
});

test('Edit active on Version 2 rejects a Version 1 edit locally without consuming its draft', async () => {
    const harness = makeSubmissionHarness({
        mode: 'edit',
        viewedResultEventId: 'result-1',
        pendingByLane: { '1:result-2': 'revise' },
        message: 'Preserve this Version 1 edit instruction',
    });

    await harness.submit();

    assert.deepEqual(harness.revised, []);
    assert.deepEqual(harness.queued, []);
    assert.equal(harness.els.input.value, 'Preserve this Version 1 edit instruction');
    assert.deepEqual(harness.errors, [
        'Another edit is already in progress for this hypothesis. ' +
        'Wait for it to finish or cancel it before starting another.',
    ]);
});

test('Edit active on another hypothesis does not block the focused hypothesis', async () => {
    const harness = makeSubmissionHarness({
        focusIndex: 2,
        mode: 'edit',
        viewedResultEventId: 'result-h2-v1',
        pendingByLane: { '1:result-h1-v2': 'revise' },
        message: 'Edit Hypothesis 2',
    });

    await harness.submit();

    assert.deepEqual(harness.revised, [[2, 'Edit Hypothesis 2', 'result-h2-v1']]);
    assert.deepEqual(harness.errors, []);
    assert.equal(harness.els.input.value, '');
});

test('request ownership is version-scoped while LLM edits remain single-flight per hypothesis', () => {
    const harness = makeSchedulerHarness({
        resultIds: ['result-1', 'result-2'],
        activeResultEventId: 'result-2',
        viewedResultEventId: 'result-2',
    });

    const chatV2 = harness.beginThreadRequest(1, 'discuss', 'Chat on V2', 'result-2');
    harness.queueThreadMessage(1, 'Queued on V2', 'result-2');
    const editV1 = harness.beginThreadRequest(1, 'revise', 'Edit V1', 'result-1');

    assert.ok(chatV2);
    assert.ok(editV1);
    assert.equal(harness.threadRequestKind(1, 'result-2'), 'discuss');
    assert.equal(harness.threadRequestKind(1, 'result-1'), 'revise');
    assert.equal(harness.hasAnyLlmEditActive(1), true);
    assert.equal(harness.beginThreadRequest(1, 'revise', 'Second edit', 'result-2'), null);
    assert.deepEqual(
        harness.getQueuedThreadMessages(1, 'result-2').map((item) => item.text),
        ['Queued on V2']
    );

    assert.equal(harness.finishThreadRequest(1, 'result-1', editV1.requestId), true);
    assert.equal(harness.hasPendingThread(1, 'result-1'), false);
    assert.equal(harness.hasPendingThread(1, 'result-2'), true);
    assert.deepEqual(
        harness.getQueuedThreadMessages(1, 'result-2').map((item) => item.text),
        ['Queued on V2']
    );
});

test('cancelling active Chat advances the next queued message and leaves no stuck request', async () => {
    const harness = makeSchedulerHarness();
    const first = harness.sendDiscuss('A', 1);
    harness.queueThreadMessage(1, 'B');

    harness.stopActiveThreadRequest();
    await first;
    assert.equal(harness.chatRequests.length, 2);
    assert.equal(harness.chatRequests[1].body.message, 'B');

    resolveChat(harness.chatRequests[1], 'Answer B');
    await flushAsyncWork();
    assert.deepEqual(
        harness.state.events.map((event) => [event.role, event.content]),
        [
            ['user', 'A'],
            ['assistant', 'Response stopped.'],
            ['user', 'B'],
            ['assistant', 'Answer B'],
        ]
    );
    assert.deepEqual(harness.state.pendingThreadRequests, {});
    assert.deepEqual(harness.activeThreadRequests, {});
});

test('cancelling Chat starts B before the stopped A transport exits and discards late A', async () => {
    const harness = makeSchedulerHarness({ abortRejects: false });
    const first = harness.sendDiscuss('A', 1);
    harness.queueThreadMessage(1, 'B');

    harness.stopActiveThreadRequest();
    assert.equal(harness.chatRequests.length, 2);
    assert.equal(harness.chatRequests[1].body.message, 'B');
    assert.deepEqual(
        harness.state.events.map((event) => [event.role, event.content]),
        [
            ['user', 'A'],
            ['assistant', 'Response stopped.'],
            ['user', 'B'],
        ]
    );

    resolveChat(harness.chatRequests[1], 'Answer B');
    await flushAsyncWork();
    resolveChat(harness.chatRequests[0], 'Late answer A');
    await first;
    await flushAsyncWork();

    assert.deepEqual(
        harness.state.events.map((event) => [event.role, event.content]),
        [
            ['user', 'A'],
            ['assistant', 'Response stopped.'],
            ['user', 'B'],
            ['assistant', 'Answer B'],
        ]
    );
    assert.equal(harness.state.events.some((event) => event.content === 'Late answer A'), false);
    assert.deepEqual(harness.state.pendingThreadRequests, {});
    assert.deepEqual(harness.activeThreadRequests, {});
});

test('cancelling Edit releases only its exact version lane and returns the composer to Chat', () => {
    const harness = makeSchedulerHarness({
        resultIds: ['result-1', 'result-2'],
        activeResultEventId: 'result-2',
        viewedResultEventId: 'result-1',
    });
    const heldOtherVersion = harness.beginThreadRequest(
        1, 'discuss', 'Held Version 2 Chat', 'result-2'
    );
    const editing = harness.beginThreadRequest(1, 'revise', 'Edit Version 1', 'result-1');
    harness.state.composerMode = 'edit';
    harness.state.busyIndex = 1;
    harness.state.busyKind = 'revise';
    harness.state.busyResultEventId = 'result-1';

    harness.stopActiveThreadRequest();

    assert.equal(harness.state.composerMode, 'chat');
    assert.equal(harness.state.busyIndex, null);
    assert.equal(harness.hasPendingThread(1, 'result-1'), false);
    assert.equal(harness.hasPendingThread(1, 'result-2'), true);
    assert.equal(harness.activeThreadRequests['1:result-1'], undefined);
    assert.equal(harness.activeThreadRequests['1:result-2'].requestId, heldOtherVersion.requestId);
    assert.ok(editing);
});

function makeSendControlHarness({ mode, pendingKind, busyKind, message }) {
    const state = {
        focusIndex: 1,
        composerMode: mode,
        pendingThreadRequests: pendingKind ? { 1: { kind: pendingKind } } : {},
        readOnly: false,
        generating: false,
        busyIndex: busyKind ? 1 : null,
        busyKind: busyKind || null,
        busyResultEventId: busyKind ? 'result-1' : null,
    };
    const send = {
        disabled: true,
        attributes: {},
        classList: { add() {}, remove() {} },
        setAttribute(name, value) { this.attributes[name] = value; },
        innerHTML: '',
    };
    const els = { input: { value: message }, send };
    const refresh = new Function(
        'state', 'els', 'hasPendingThread', 'currentThreadResultEventId',
        'isLlmEditActive', 'isRegeneratingHypothesis', 'isPendingSelectedCard',
        `${refreshSource}\nreturn refreshSendEnabled;`
    )(
        state,
        els,
        (index) => !!state.pendingThreadRequests[index],
        () => 'result-1',
        (index) => state.busyIndex === index && state.busyKind === 'revise',
        () => false,
        () => false
    );
    refresh();
    return send;
}

test('active response always presents the Stop control even when a draft is present', () => {
    const chatSend = makeSendControlHarness({
        mode: 'chat', pendingKind: 'discuss', busyKind: null, message: 'B',
    });
    assert.equal(chatSend.disabled, false);
    assert.equal(chatSend.attributes['aria-label'], 'Stop response');
    assert.match(chatSend.innerHTML, /<rect /);

    const editSend = makeSendControlHarness({
        mode: 'chat', pendingKind: 'revise', busyKind: 'revise', message: 'B',
    });
    assert.equal(editSend.disabled, false);
    assert.equal(editSend.attributes['aria-label'], 'Stop response');
    assert.match(editSend.innerHTML, /<rect /);
});

test('idle composer presents the Send control', () => {
    const send = makeSendControlHarness({
        mode: 'chat', pendingKind: null, busyKind: null, message: 'B',
    });
    assert.equal(send.disabled, false);
    assert.equal(send.attributes['aria-label'], 'Send message');
    assert.doesNotMatch(send.innerHTML, /<rect /);
});

test('clicking the active Stop control cancels without submitting the draft', async () => {
    let stopped = 0;
    let submitted = 0;
    const onSubmit = new Function(
        'isFocusedThreadPending', 'cancelActiveThreadResponse', 'submitThreadMessage',
        `${onSubmitSource}\nreturn onSubmit;`
    )(
        () => true,
        () => { stopped += 1; },
        async () => { submitted += 1; }
    );

    await onSubmit({ preventDefault() {} });
    assert.equal(stopped, 1);
    assert.equal(submitted, 0);
});

test('clicking the idle Send control submits normally', async () => {
    let stopped = 0;
    let submitted = 0;
    const onSubmit = new Function(
        'isFocusedThreadPending', 'cancelActiveThreadResponse', 'submitThreadMessage',
        `${onSubmitSource}\nreturn onSubmit;`
    )(
        () => false,
        () => { stopped += 1; },
        async () => { submitted += 1; }
    );

    await onSubmit({ preventDefault() {} });
    assert.equal(stopped, 0);
    assert.equal(submitted, 1);
});

test('request state remains isolated by hypothesis index', () => {
    const harness = makeSchedulerHarness();
    harness.state.pendingThreadRequests[1] = { kind: 'discuss' };
    harness.queueThreadMessage(1, 'B');
    harness.queueThreadMessage(2, 'independent');

    assert.deepEqual(harness.getQueuedThreadMessages(1).map((item) => item.text), ['B']);
    assert.deepEqual(harness.getQueuedThreadMessages(2).map((item) => item.text), ['independent']);
    assert.equal(harness.activeThreadRequests[2], undefined);
});

test('queued chats are visible only on their exact hypothesis version', () => {
    const harness = makeSchedulerHarness({
        resultIds: ['result-1', 'result-2'],
        activeResultEventId: 'result-2',
        viewedResultEventId: 'result-2',
    });
    harness.queueThreadMessage(1, 'Version 2 follow-up');

    assert.deepEqual(
        harness.getQueuedThreadMessages(1).map((item) => item.text),
        ['Version 2 follow-up']
    );
    assert.equal(harness.state.queuedThreadMessages[1][0].resultEventId, 'result-2');

    harness.setViewedResultEventId('result-1');
    assert.deepEqual(harness.getQueuedThreadMessages(1), []);
    harness.queueThreadMessage(1, 'Version 1 follow-up');
    assert.deepEqual(
        harness.getQueuedThreadMessages(1).map((item) => item.text),
        ['Version 1 follow-up']
    );
    assert.deepEqual(
        harness.getQueuedThreadMessages(1, 'result-2').map((item) => item.text),
        ['Version 2 follow-up']
    );

    harness.removeQueuedThreadMessage(1, 'result-1', 0);
    assert.deepEqual(harness.getQueuedThreadMessages(1, 'result-1'), []);
    assert.deepEqual(
        harness.getQueuedThreadMessages(1, 'result-2').map((item) => item.text),
        ['Version 2 follow-up']
    );
});

test('queue persistence preserves version IDs and safely migrates legacy entries to the viewed version', () => {
    const harness = makeSchedulerHarness({
        resultIds: ['result-1', 'result-2'],
        activeResultEventId: 'result-2',
        viewedResultEventId: 'result-2',
    });
    const normalized = harness.normalizeQueuedThreadMessages({
        1: [
            { text: 'Version 1', createdAt: 1, resultEventId: 'result-1' },
            { text: 'Version 2', createdAt: 2, resultEventId: 'result-2' },
            'Legacy queued message',
        ],
    }, () => 'result-2');

    assert.deepEqual(normalized[1].map((item) => item.resultEventId), [
        'result-1', 'result-2', 'result-2',
    ]);
    assert.deepEqual(normalized[1].map((item) => item.text), [
        'Version 1', 'Version 2', 'Legacy queued message',
    ]);
});

test('FIFO promotion dispatches each queued chat to its captured version after navigation', async () => {
    const harness = makeSchedulerHarness({
        resultIds: ['result-1', 'result-2'],
        activeResultEventId: 'result-2',
        viewedResultEventId: 'result-2',
    });
    const first = harness.sendDiscuss('A on Version 2', 1);
    harness.queueThreadMessage(1, 'B on Version 2');
    harness.setViewedResultEventId('result-1');
    harness.queueThreadMessage(1, 'C on Version 1');

    resolveChat(harness.chatRequests[0], 'Answer A', 'result-2');
    await first;
    assert.equal(harness.chatRequests[1].body.message, 'B on Version 2');
    assert.equal('regeneration_result_id' in harness.chatRequests[1].body, false);

    resolveChat(harness.chatRequests[1], 'Answer B', 'result-2');
    await flushAsyncWork();
    assert.equal(harness.chatRequests[2].body.message, 'C on Version 1');
    assert.equal(harness.chatRequests[2].body.regeneration_result_id, 'result-1');

    resolveChat(harness.chatRequests[2], 'Answer C', 'result-1');
    await flushAsyncWork();
    assert.deepEqual(harness.state.pendingThreadRequests, {});
    assert.deepEqual(harness.state.queuedThreadMessages, {});
});

test('a late Version 2 Chat response stays on Version 2 after navigation to Version 1', async () => {
    const harness = makeSchedulerHarness({
        resultIds: ['result-1', 'result-2'],
        activeResultEventId: 'result-2',
        viewedResultEventId: 'result-2',
    });
    const request = harness.sendDiscuss('Explain Version 2', 1);

    harness.setViewedResultEventId('result-1');
    resolveChat(harness.chatRequests[0], 'Version 2 answer', 'result-2');
    await request;

    const answer = harness.state.events.find((event) => event.content === 'Version 2 answer');
    assert.ok(answer);
    assert.equal(answer.resultEventId, 'result-2');
    assert.equal(harness.hasPendingThread(1, 'result-1'), false);
    assert.equal(harness.hasPendingThread(1, 'result-2'), false);
});
