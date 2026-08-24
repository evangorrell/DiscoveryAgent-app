const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const auditPath = path.join(__dirname, '..', 'da_selection_ui', 'static', 'project_audit_agent.js');
const auditSource = fs.readFileSync(auditPath, 'utf8');
const templateSource = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'templates', 'round_workspace.html'),
    'utf8'
);
const hypothesisSource = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'hypothesis_studio.js'),
    'utf8'
);
const hypothesisTemplate = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'templates', 'hypothesis_studio.html'),
    'utf8'
);

function sourceBetween(startMarker, endMarker) {
    const start = auditSource.indexOf(startMarker);
    const end = auditSource.indexOf(endMarker, start);
    assert.notEqual(start, -1, 'missing ' + startMarker);
    assert.notEqual(end, -1, 'missing ' + endMarker);
    return auditSource.slice(start, end);
}

const queueSource = sourceBetween(
    '    function getQueuedAuditMessages(',
    '    function rebuildPendingChatIds('
);
const resumeSource = sourceBetween(
    '    function resumePendingAuditJobs()',
    '    function getActiveAuditJob()'
);
const promoteSource = sourceBetween(
    '    function promoteNextQueuedAuditMessage(',
    '    async function submitAuditJob('
);
const cancelSource = sourceBetween(
    '    async function cancelActiveAuditResponse()',
    '    function autoSizeInput()'
);
const sendSource = sourceBetween(
    '    function sendMessage()',
    '    newChatBtn && newChatBtn.addEventListener'
);
const composerWiringSource = sourceBetween(
    '    newChatBtn && newChatBtn.addEventListener',
    "    document.addEventListener('click'"
);
const refreshControlsSource = sourceBetween(
    '    function refreshSendEnabled()',
    '    function updateComposerForActiveChat()'
);
const composerHandlersSource = sourceBetween(
    '    function handleComposerKeydown(e)',
    "    chatInputEl && chatInputEl.addEventListener('keydown'"
);

function deferred() {
    let resolve;
    const promise = new Promise((res) => { resolve = res; });
    return { promise, resolve };
}

function composerControl({ pending, stoppingJobId = null, text = 'Next question' }) {
    const attributes = {};
    const classes = new Set();
    const chatSendBtn = {
        disabled: true,
        innerHTML: '',
        classList: {
            add(name) { classes.add(name); },
            remove(name) { classes.delete(name); },
        },
        setAttribute(name, value) { attributes[name] = value; },
    };
    const chatInputEl = { value: text, disabled: false };
    const invoke = new Function(
        'chatSendBtn', 'chatInputEl', 'isActiveChatPending', 'stoppingAuditJobId',
        `${refreshControlsSource}; refreshSendEnabled();`
    );
    invoke(chatSendBtn, chatInputEl, () => pending, stoppingJobId);
    return { chatSendBtn, chatInputEl, attributes, classes };
}

function composerHandlers({ pending, sendMessage, cancelActiveAuditResponse }) {
    return new Function(
        'isActiveChatPending', 'sendMessage', 'cancelActiveAuditResponse',
        `${composerHandlersSource}; return { handleComposerKeydown, handleComposerSubmit };`
    )(() => pending, sendMessage, cancelActiveAuditResponse);
}

function makeHarness({
    queued = [], pending = true, projectId = `prj_${'a'.repeat(32)}`,
} = {}) {
    const chatId = 'chat-1';
    const pendingAuditJobs = pending
        ? [{ jobId: 'job-a', chatId, projectId, message: 'A', createdAt: 1 }]
        : [];
    const pendingChatIds = new Set(pending ? [chatId] : []);
    const cancelledAuditJobIds = new Set();
    const queuedAuditMessages = queued.length
        ? { [chatId]: queued.map((entry, index) => ({
            text: typeof entry === 'string' ? entry : entry.text,
            createdAt: index + 2,
            viewerContext: typeof entry === 'string' ? null : entry.viewerContext,
        })) }
        : {};
    const chatSessions = [{
        id: chatId,
        titleEdited: false,
        messages: pending ? [{ role: 'user', text: 'A' }] : [],
    }];
    let activeChatId = chatId;
    let chatHistory = chatSessions[0].messages;
    const submitted = [];
    const polled = [];
    const fetchCalls = [];
    const cancelTransport = deferred();
    const chatInputEl = {
        value: '',
        disabled: false,
        focusCalls: 0,
        focus() { this.focusCalls += 1; },
    };
    let refreshCalls = 0;

    function removePendingAuditJob(jobId) {
        const index = pendingAuditJobs.findIndex((job) => job.jobId === jobId);
        if (index >= 0) {
            const [job] = pendingAuditJobs.splice(index, 1);
            pendingChatIds.delete(job.chatId);
        }
    }
    function appendAssistantMessageToChat(targetChatId, text) {
        const chat = chatSessions.find((entry) => entry.id === targetChatId);
        chat.messages.push({ role: 'assistant', text });
        if (targetChatId === activeChatId) chatHistory = chat.messages;
    }
    function appendUserMessageToChat(targetChatId, text, viewerContext) {
        const chat = chatSessions.find((entry) => entry.id === targetChatId);
        chat.messages.push({ role: 'user', text, viewerContext });
        if (targetChatId === activeChatId) chatHistory = chat.messages;
    }
    function submitAuditJob(targetChatId, message, requestContext, _replyContent, viewerContext) {
        submitted.push({ chatId: targetChatId, message, requestContext, viewerContext });
        pendingAuditJobs.push({
            jobId: `job-${message.toLowerCase()}`,
            chatId: targetChatId,
            projectId,
            message,
            createdAt: Date.now(),
        });
        pendingChatIds.add(targetChatId);
    }
    function pollAuditChatJob(job) { polled.push(job.jobId); }
    function fetch(url, options) {
        assert.match(url, /\/api\/audit_chat_jobs\/job-a\/cancel$/);
        assert.equal(options.method, 'POST');
        assert.equal(options.credentials, 'include');
        assert.deepEqual(JSON.parse(options.body), { project_id: projectId });
        fetchCalls.push({ url, options });
        return cancelTransport.promise;
    }
    function normalizeStoredAuditChatMessages(messages) { return messages.slice(); }
    function getActiveAuditJob() {
        return pendingAuditJobs.find((job) => job.chatId === activeChatId) || null;
    }

    const build = new Function(
        'queuedAuditMessages', 'pendingAuditJobs', 'pendingChatIds',
        'cancelledAuditJobIds', 'chatSessions', 'activeChatId', 'chatHistory',
        'removePendingAuditJob', 'appendAssistantMessageToChat',
        'appendUserMessageToChat', 'normalizeStoredAuditChatMessages',
        'renderStoredChatHistory', 'persistAuditChatState', 'renderChatList',
        'updateComposerForActiveChat', 'syncEmptyState', 'renderQueuedMessages',
        'submitAuditJob', 'pollAuditChatJob', 'getActiveAuditJob', 'fetch',
        'projectId', 'stoppingAuditJobId', 'stoppingAuditJob', 'auditScreenState',
        'chatInputEl', 'refreshSendEnabled', 'captureViewerContextSnapshot',
        'autoSizeInput', 'normalizeViewerContextSnapshot', 'reconcileAuditScreenAfterWork',
        `${queueSource}
         ${resumeSource}
         ${promoteSource}
         ${cancelSource}
         ${sendSource}
         return {
             cancelActiveAuditResponse,
             getQueuedAuditMessages,
             promoteNextQueuedAuditMessage,
             resumePendingAuditJobs,
             sendMessage,
             stoppingJobId: function() { return stoppingAuditJobId; },
         };`
    )(
        queuedAuditMessages,
        pendingAuditJobs,
        pendingChatIds,
        cancelledAuditJobIds,
        chatSessions,
        activeChatId,
        chatHistory,
        removePendingAuditJob,
        appendAssistantMessageToChat,
        appendUserMessageToChat,
        normalizeStoredAuditChatMessages,
        () => {},
        () => {},
        () => {},
        () => {},
        () => {},
        () => {},
        submitAuditJob,
        pollAuditChatJob,
        getActiveAuditJob,
        fetch,
        projectId,
        null,
        null,
        'active',
        chatInputEl,
        () => { refreshCalls += 1; },
        () => ({ version: 1, capturedAt: 10, artifacts: [] }),
        () => {},
        (value) => value,
        () => {}
    );

    return {
        chatId,
        pendingAuditJobs,
        pendingChatIds,
        cancelledAuditJobIds,
        queuedAuditMessages,
        chatSessions,
        submitted,
        polled,
        fetchCalls,
        cancelTransport,
        chatInputEl,
        refreshCalls: () => refreshCalls,
        ...build,
    };
}

test('Audit composer has one contextual Send or Stop control matching Hypothesis chat', () => {
    assert.equal((templateSource.match(/class="aa-send(?: |")/g) || []).length, 1);
    assert.match(templateSource, /<button type="submit" id="chat-send-btn" class="aa-send" disabled aria-label="Send message">/);
    assert.doesNotMatch(templateSource, /audit-stop-btn|Stop audit response/);
    assert.doesNotMatch(auditSource, /chatStopBtn|audit-stop-btn|Queue message/);
    assert.match(composerWiringSource, /addEventListener\('submit', handleComposerSubmit\)/);
    assert.match(composerWiringSource, /addEventListener\('keydown', handleComposerKeydown\)/);

    const idle = composerControl({ pending: false, text: 'Ready' });
    assert.equal(idle.chatSendBtn.disabled, false);
    assert.equal(idle.attributes['aria-label'], 'Send message');
    assert.equal(idle.classes.has('is-stop'), false);
    assert.match(idle.chatSendBtn.innerHTML, /M12 19V5/);

    const active = composerControl({ pending: true, text: 'Queued draft' });
    assert.equal(active.chatSendBtn.disabled, false);
    assert.equal(active.chatInputEl.disabled, false);
    assert.equal(active.attributes['aria-label'], 'Stop response');
    assert.equal(active.classes.has('is-stop'), true);
    assert.match(active.chatSendBtn.innerHTML, /<rect /);

    const emptyIdle = composerControl({ pending: false, text: '   ' });
    assert.equal(emptyIdle.chatSendBtn.disabled, true);

    const hypothesisRefresh = hypothesisSource.slice(
        hypothesisSource.indexOf('    function refreshSendEnabled()'),
        hypothesisSource.indexOf('    // ---- wiring'),
    );
    for (const contract of [
        "classList.add('is-stop')",
        "setAttribute('aria-label', 'Stop response')",
        "classList.remove('is-stop')",
        "setAttribute('aria-label', 'Send message')",
    ]) {
        assert.ok(refreshControlsSource.includes(contract));
        assert.ok(hypothesisRefresh.includes(contract));
    }
    assert.match(hypothesisTemplate, /<button type="submit" class="hs-send" id="hs-send" aria-label="Send message">/);
});

test('Audit terminal states all restore the single idle Send control', () => {
    for (const terminalState of ['completed', 'failed', 'cancelled', 'stopped']) {
        const control = composerControl({ pending: false, text: terminalState });
        assert.equal(control.chatSendBtn.disabled, false, terminalState);
        assert.equal(control.attributes['aria-label'], 'Send message', terminalState);
        assert.equal(control.attributes['aria-busy'], 'false', terminalState);
        assert.equal(control.classes.has('is-stop'), false, terminalState);
        assert.match(control.chatSendBtn.innerHTML, /M12 19V5/, terminalState);
        assert.doesNotMatch(control.chatSendBtn.innerHTML, /<rect /, terminalState);
    }
});

test('Audit Enter queues while active, Shift+Enter inserts a newline, and submit Stop preserves drafts', async () => {
    const harness = makeHarness();
    let prevented = 0;
    const handlers = composerHandlers({
        pending: true,
        sendMessage: harness.sendMessage,
        cancelActiveAuditResponse: harness.cancelActiveAuditResponse,
    });

    harness.chatInputEl.value = 'B';
    handlers.handleComposerKeydown({
        key: 'Enter', shiftKey: false, isComposing: false,
        preventDefault() { prevented += 1; },
    });
    assert.equal(prevented, 1);
    assert.deepEqual(harness.getQueuedAuditMessages(harness.chatId).map(entry => entry.text), ['B']);
    assert.equal(harness.cancelledAuditJobIds.size, 0);
    assert.deepEqual(harness.fetchCalls, []);

    harness.chatInputEl.value = 'line one';
    handlers.handleComposerKeydown({
        key: 'Enter', shiftKey: true, isComposing: false,
        preventDefault() { prevented += 1; },
    });
    assert.equal(prevented, 1);
    assert.equal(harness.chatInputEl.value, 'line one');
    assert.deepEqual(harness.getQueuedAuditMessages(harness.chatId).map(entry => entry.text), ['B']);

    harness.chatInputEl.value = 'unsent draft';
    handlers.handleComposerSubmit({ preventDefault() { prevented += 1; } });
    assert.equal(harness.chatInputEl.value, 'unsent draft');
    assert.equal(harness.cancelledAuditJobIds.has('job-a'), true);
    assert.deepEqual(harness.submitted.map(entry => entry.message), ['B']);
    harness.cancelTransport.resolve({ ok: true });
    await Promise.resolve();
});

test('Audit Enter sends while idle without invoking Stop', () => {
    const harness = makeHarness({ pending: false });
    let prevented = 0;
    let stopCalls = 0;
    const handlers = composerHandlers({
        pending: false,
        sendMessage: harness.sendMessage,
        cancelActiveAuditResponse() { stopCalls += 1; },
    });

    harness.chatInputEl.value = 'First question';
    handlers.handleComposerKeydown({
        key: 'Enter', shiftKey: false, isComposing: false,
        preventDefault() { prevented += 1; },
    });

    assert.equal(prevented, 1);
    assert.equal(stopCalls, 0);
    assert.deepEqual(harness.submitted.map(entry => entry.message), ['First question']);
});

test('second and third composer submissions queue without cancelling the active request', () => {
    const harness = makeHarness();

    harness.chatInputEl.value = 'B';
    harness.sendMessage();
    harness.chatInputEl.value = 'C';
    harness.sendMessage();

    assert.deepEqual(harness.pendingAuditJobs.map((job) => job.jobId), ['job-a']);
    assert.deepEqual(harness.getQueuedAuditMessages(harness.chatId).map((entry) => entry.text), ['B', 'C']);
    assert.deepEqual(harness.submitted, []);
    assert.deepEqual(harness.fetchCalls, []);
});

test('first composer submission creates exactly one active project-owned job', () => {
    const harness = makeHarness({ pending: false });

    harness.chatInputEl.value = 'A';
    harness.sendMessage();

    assert.deepEqual(harness.pendingAuditJobs.map((job) => job.jobId), ['job-a']);
    assert.deepEqual(harness.submitted.map((entry) => entry.message), ['A']);
    assert.deepEqual(harness.getQueuedAuditMessages(harness.chatId), []);
    assert.deepEqual(harness.chatSessions[0].messages.map((entry) => entry.text), ['A']);
});

test('polling stays on A and normal completion drains B then C exactly once', () => {
    const harness = makeHarness({ queued: ['B', 'C'] });

    harness.resumePendingAuditJobs();
    assert.deepEqual(harness.polled, ['job-a']);
    assert.deepEqual(harness.submitted, []);

    harness.pendingAuditJobs.splice(0);
    harness.pendingChatIds.delete(harness.chatId);
    assert.equal(harness.promoteNextQueuedAuditMessage(harness.chatId), true);
    assert.equal(harness.promoteNextQueuedAuditMessage(harness.chatId), false);
    assert.deepEqual(harness.submitted.map((entry) => entry.message), ['B']);
    assert.deepEqual(harness.getQueuedAuditMessages(harness.chatId).map((entry) => entry.text), ['C']);

    harness.pendingAuditJobs.splice(0);
    harness.pendingChatIds.delete(harness.chatId);
    assert.equal(harness.promoteNextQueuedAuditMessage(harness.chatId), true);
    assert.equal(harness.promoteNextQueuedAuditMessage(harness.chatId), false);
    assert.deepEqual(harness.submitted.map((entry) => entry.message), ['B', 'C']);
    assert.deepEqual(harness.getQueuedAuditMessages(harness.chatId), []);
});

test('project A Stop and queue draining never mutate project B state', async () => {
    const projectA = makeHarness({ projectId: `prj_${'a'.repeat(32)}` });
    const projectB = makeHarness({ projectId: `prj_${'b'.repeat(32)}` });

    projectA.chatInputEl.value = 'A2';
    projectA.sendMessage();
    projectB.chatInputEl.value = 'B2';
    projectB.sendMessage();

    const stopping = projectA.cancelActiveAuditResponse();
    assert.deepEqual(projectA.submitted.map((entry) => entry.message), ['A2']);
    assert.deepEqual(projectA.getQueuedAuditMessages(projectA.chatId), []);
    assert.deepEqual(projectB.pendingAuditJobs.map((job) => job.jobId), ['job-a']);
    assert.deepEqual(projectB.getQueuedAuditMessages(projectB.chatId).map((entry) => entry.text), ['B2']);
    assert.equal(projectB.cancelledAuditJobIds.size, 0);

    projectA.cancelTransport.resolve({ ok: true });
    await stopping;
});

test('Audit Stop promotes B before A cancellation transport settles', async () => {
    const harness = makeHarness({ queued: ['B'] });
    const stopping = harness.cancelActiveAuditResponse();

    assert.equal(harness.cancelledAuditJobIds.has('job-a'), true);
    assert.deepEqual(harness.submitted.map((entry) => entry.message), ['B']);
    assert.deepEqual(harness.getQueuedAuditMessages(harness.chatId), []);
    assert.deepEqual(
        harness.chatSessions[0].messages.map((entry) => [entry.role, entry.text]),
        [['user', 'A'], ['assistant', 'Response stopped.'], ['user', 'B']]
    );

    harness.cancelTransport.resolve({ ok: true });
    await stopping;
});

test('repeated Stop activation is idempotent and cannot cancel the promoted job', async () => {
    const harness = makeHarness({ queued: ['B'] });
    const firstStop = harness.cancelActiveAuditResponse();
    const repeatedStop = harness.cancelActiveAuditResponse();

    await repeatedStop;
    assert.equal(harness.fetchCalls.length, 1);
    assert.deepEqual(harness.submitted.map((entry) => entry.message), ['B']);
    assert.deepEqual(harness.pendingAuditJobs.map((job) => job.jobId), ['job-b']);
    assert.equal(harness.stoppingJobId(), 'job-a');

    harness.cancelTransport.resolve({ ok: true });
    await firstStop;
    assert.equal(harness.stoppingJobId(), null);
    assert.equal(harness.refreshCalls() >= 2, true);
});

test('Audit Stop preserves B/C FIFO while advancing one item at a time', async () => {
    const harness = makeHarness({ queued: ['B', 'C'] });
    const stopping = harness.cancelActiveAuditResponse();

    assert.deepEqual(harness.submitted.map((entry) => entry.message), ['B']);
    assert.deepEqual(harness.getQueuedAuditMessages(harness.chatId).map((entry) => entry.text), ['C']);

    harness.pendingChatIds.delete(harness.chatId);
    harness.promoteNextQueuedAuditMessage(harness.chatId);
    assert.deepEqual(harness.submitted.map((entry) => entry.message), ['B', 'C']);
    assert.deepEqual(harness.getQueuedAuditMessages(harness.chatId), []);

    harness.cancelTransport.resolve({ ok: true });
    await stopping;
});

test('Audit refresh promotes an orphaned durable queue without Resume state', () => {
    const harness = makeHarness({ queued: ['B'], pending: false });
    harness.resumePendingAuditJobs();
    assert.deepEqual(harness.polled, []);
    assert.deepEqual(harness.submitted.map((entry) => entry.message), ['B']);
    assert.equal(auditSource.includes('Queue paused because you interrupted'), false);
    assert.equal(auditSource.includes('pausedAuditQueues'), false);
    assert.equal(auditSource.includes('aa-queue-resume'), false);
});

test('queued messages retain independent send-time viewer snapshots', () => {
    const firstContext = { version: 1, capturedAt: 10, artifacts: [{ path: 'round_0/active.md' }] };
    const secondContext = { version: 1, capturedAt: 20, artifacts: [{ path: 'round_1/active.png' }] };
    const harness = makeHarness({
        queued: [
            { text: 'B', viewerContext: firstContext },
            { text: 'C', viewerContext: secondContext },
        ],
        pending: false,
    });

    harness.promoteNextQueuedAuditMessage(harness.chatId);
    assert.equal(harness.submitted[0].viewerContext, firstContext);
    assert.equal(harness.chatSessions[0].messages[0].viewerContext, firstContext);

    harness.pendingChatIds.delete(harness.chatId);
    harness.promoteNextQueuedAuditMessage(harness.chatId);
    assert.equal(harness.submitted[1].viewerContext, secondContext);
    assert.equal(harness.chatSessions[0].messages[1].viewerContext, secondContext);
});
