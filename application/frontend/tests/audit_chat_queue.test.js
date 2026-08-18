const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const auditPath = path.join(__dirname, '..', 'da_selection_ui', 'static', 'project_audit_agent.js');
const auditSource = fs.readFileSync(auditPath, 'utf8');

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

function deferred() {
    let resolve;
    const promise = new Promise((res) => { resolve = res; });
    return { promise, resolve };
}

function makeHarness({ queued = [], pending = true } = {}) {
    const chatId = 'chat-1';
    const pendingAuditJobs = pending
        ? [{ jobId: 'job-a', chatId, message: 'A', createdAt: 1 }]
        : [];
    const pendingChatIds = new Set(pending ? [chatId] : []);
    const cancelledAuditJobIds = new Set();
    const queuedAuditMessages = queued.length
        ? { [chatId]: queued.map((text, index) => ({ text, createdAt: index + 2 })) }
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
    const cancelTransport = deferred();

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
    function appendUserMessageToChat(targetChatId, text) {
        const chat = chatSessions.find((entry) => entry.id === targetChatId);
        chat.messages.push({ role: 'user', text });
        if (targetChatId === activeChatId) chatHistory = chat.messages;
    }
    function submitAuditJob(targetChatId, message, requestContext) {
        submitted.push({ chatId: targetChatId, message, requestContext });
        pendingChatIds.add(targetChatId);
    }
    function pollAuditChatJob(job) { polled.push(job.jobId); }
    function fetch(url) {
        assert.match(url, /\/api\/audit_chat_jobs\/job-a\/cancel$/);
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
        `${queueSource}
         ${resumeSource}
         ${promoteSource}
         ${cancelSource}
         return {
             cancelActiveAuditResponse,
             getQueuedAuditMessages,
             promoteNextQueuedAuditMessage,
             resumePendingAuditJobs,
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
        fetch
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
        cancelTransport,
        ...build,
    };
}

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
