const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const auditSource = fs.readFileSync(
    path.join(root, 'da_selection_ui/static/project_audit_agent.js'), 'utf8',
);
const workspaceSource = fs.readFileSync(
    path.join(root, 'da_selection_ui/static/round_workspace.js'), 'utf8',
);

function sourceBetween(source, startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert.notEqual(start, -1, `missing ${startMarker}`);
    assert.notEqual(end, -1, `missing ${endMarker}`);
    return source.slice(start, end);
}

const workStateSource = sourceBetween(
    auditSource,
    '    function chatHasQueuedAuditWork(chatId)',
    '    function ensureActiveChat()',
);
const ensureChatSource = sourceBetween(
    auditSource,
    '    function ensureActiveChat()',
    '    function syncActiveChatFromHistory(options)',
);
const lifecycleSource = sourceBetween(
    auditSource,
    '    function showAuditChatLocally(chatId, requestedState)',
    '    function isActiveChatPending()',
);
const restoreSource = sourceBetween(
    auditSource,
    '    function restoreLocalChatInterface()',
    '    function setComposerEnabled(enabled)',
);
const activateSource = sourceBetween(
    auditSource,
    '    async function activateChat(chatId, options)',
    '    async function switchChat(chatId)',
);
const sendSource = sourceBetween(
    auditSource,
    '    function sendMessage()',
    "    newChatBtn && newChatBtn.addEventListener('click', startNewChat)",
);

function deferred() {
    let resolve;
    const promise = new Promise((res) => { resolve = res; });
    return { promise, resolve };
}

function makeLifecycleHarness({
    chats = [], activeChatId: storedActiveId = null, pending = [], queues = {}, stopping = null, visitView = null,
} = {}) {
    const listeners = new Map();
    const writes = [];
    const renders = [];
    let historyOpen = false;
    let expandedFigure = false;
    let resumeCalls = 0;
    const workspaceEl = { scrollTop: 0, scrollHeight: 500 };
    const chatMessagesEl = { scrollTop: 0, scrollHeight: 500 };
    const chatInputEl = { value: '', disabled: false, focus() {} };
    const windowObject = {
        discoveryProjectVisit: { readAuditView() { return visitView; } },
        roundWorkspace: { state: { panelCollapsed: false, panelTab: 'chat' } },
        addEventListener(type, handler) { listeners.set(type, handler); },
    };
    const projectId = 'prj_lifecycle000000000000000000000001';
    let chatSessions = chats.map((chat) => ({ ...chat, messages: (chat.messages || []).slice() }));
    let pendingAuditJobs = pending.map((job) => ({ projectId, ...job }));
    let queuedAuditMessages = JSON.parse(JSON.stringify(queues));
    const pendingChatIds = new Set(pendingAuditJobs.map((job) => job.chatId));
    let stoppingAuditJob = stopping ? { projectId, ...stopping } : null;
    let activeChatId = null;
    let chatHistory = [];
    let auditScreenState = 'new';
    let auditClosed = false;
    let auditHidden = false;
    let auditLifecycleRevision = 0;
    let auditMarkdownRenderSequence = 0;
    let renamingChatId = null;
    let deleteConfirmChatId = null;
    const storedState = {
        chats: chatSessions,
        activeChatId: storedActiveId,
        pendingJobs: pendingAuditJobs,
        queuedMessages: queuedAuditMessages,
    };

    function getQueuedAuditMessages(chatId) {
        return Array.isArray(queuedAuditMessages[chatId]) ? queuedAuditMessages[chatId] : [];
    }
    function normalizeStoredAuditChatMessages(messages) { return Array.isArray(messages) ? messages.slice() : []; }
    function normalizePendingAuditJobs(jobs) { return Array.isArray(jobs) ? jobs.slice() : []; }
    function normalizeQueuedAuditMessages(value) { return value && typeof value === 'object' ? value : {}; }
    function rebuildPendingChatIds() {
        pendingChatIds.clear();
        pendingAuditJobs.forEach((job) => pendingChatIds.add(job.chatId));
    }
    function cleanupUnlistedDraftChats() {
        chatSessions = chatSessions.filter((chat) => (chat.messages || []).length > 0 || pendingChatIds.has(chat.id));
    }
    function readAuditChatStateFromStorage() { return storedState; }
    function clearChatBubbles() { renders.push('clear'); }
    function renderStoredChatHistory(entries) { renders.push(['history', entries.map((entry) => entry.text)]); }
    function renderChatList() { renders.push('list'); }
    function revealActiveChatRow() { renders.push('reveal'); }
    function autoSizeInput() {}
    function updateComposerForActiveChat() {}
    function syncEmptyState() {}
    function setHistoryDrawerOpen(open) { historyOpen = open; }
    function revealMainInterface() {}
    function hideBootOverlay() {}
    function resumePendingAuditJobs() { resumeCalls += 1; }
    function persistAuditChatState() { writes.push('persist'); }

    const build = new Function(
        'window', 'projectId', 'chatSessions', 'pendingAuditJobs', 'queuedAuditMessages',
        'pendingChatIds', 'stoppingAuditJob', 'activeChatId', 'chatHistory',
        'auditScreenState', 'auditClosed', 'auditHidden', 'auditLifecycleRevision',
        'auditMarkdownRenderSequence', 'renamingChatId', 'deleteConfirmChatId',
        'workspaceEl', 'chatMessagesEl', 'chatInputEl', 'getQueuedAuditMessages',
        'normalizeStoredAuditChatMessages', 'normalizePendingAuditJobs',
        'normalizeQueuedAuditMessages', 'rebuildPendingChatIds', 'cleanupUnlistedDraftChats',
        'readAuditChatStateFromStorage', 'clearChatBubbles', 'renderStoredChatHistory',
        'renderChatList', 'revealActiveChatRow', 'autoSizeInput', 'updateComposerForActiveChat',
        'syncEmptyState', 'setHistoryDrawerOpen', 'revealMainInterface', 'hideBootOverlay',
        'resumePendingAuditJobs',
        `let auditViewRestored = false;
         ${workStateSource}
         ${lifecycleSource}
         ${restoreSource}
         return {
             restoreLocalChatInterface,
             showAuditChatLocally,
             showBlankNewChat,
             reconcileAuditScreenAfterWork,
             dispatch(transition) {
                 const handler = window.__listener;
                 handler({ detail: { transition } });
             },
             clearPending() { pendingAuditJobs = []; pendingChatIds.clear(); },
             state() {
                 return { chatSessions, activeChatId, chatHistory, auditScreenState, auditClosed,
                     auditHidden, draft: chatInputEl.value, scrollTop: workspaceEl.scrollTop };
             },
         };`,
    )(
        { ...windowObject, addEventListener(type, handler) { listeners.set(type, handler); this.__listener = handler; } },
        projectId, chatSessions, pendingAuditJobs, queuedAuditMessages, pendingChatIds,
        stoppingAuditJob, activeChatId, chatHistory, auditScreenState, auditClosed,
        auditHidden, auditLifecycleRevision, auditMarkdownRenderSequence, renamingChatId,
        deleteConfirmChatId, workspaceEl, chatMessagesEl, chatInputEl, getQueuedAuditMessages,
        normalizeStoredAuditChatMessages, normalizePendingAuditJobs, normalizeQueuedAuditMessages,
        rebuildPendingChatIds, cleanupUnlistedDraftChats, readAuditChatStateFromStorage,
        clearChatBubbles, renderStoredChatHistory, renderChatList, revealActiveChatRow,
        autoSizeInput, updateComposerForActiveChat, syncEmptyState, setHistoryDrawerOpen,
        revealMainInterface, hideBootOverlay, resumePendingAuditJobs,
    );

    return {
        ...build,
        writes,
        renders,
        chatInputEl,
        workspaceEl,
        get historyOpen() { return historyOpen; },
        set historyOpen(value) { historyOpen = value; },
        get expandedFigure() { return expandedFigure; },
        set expandedFigure(value) { expandedFigure = value; },
        get resumeCalls() { return resumeCalls; },
    };
}

const historyChat = {
    id: 'history-chat',
    title: 'Persisted result',
    messages: [{ role: 'user', text: 'Prior question' }, { role: 'assistant', text: 'Prior answer' }],
};

test('new project visit starts blank but panel close/reopen preserves the chosen chat without writes', async () => {
    const harness = makeLifecycleHarness({ chats: [historyChat], activeChatId: historyChat.id });
    harness.restoreLocalChatInterface();
    await Promise.resolve();

    assert.equal(harness.state().auditScreenState, 'new');
    assert.equal(harness.state().activeChatId, null);
    assert.deepEqual(harness.state().chatSessions.map((chat) => chat.id), ['history-chat']);
    assert.deepEqual(harness.writes, []);

    harness.showAuditChatLocally('history-chat', 'history');
    harness.chatInputEl.value = 'unsaved draft';
    harness.dispatch('closed');
    await Promise.resolve();
    assert.equal(harness.state().auditClosed, true);
    assert.equal(harness.state().activeChatId, historyChat.id);
    assert.equal(harness.state().draft, 'unsaved draft');
    assert.deepEqual(harness.writes, []);

    harness.dispatch('reopened');
    await Promise.resolve();
    assert.equal(harness.state().auditClosed, false);
    assert.equal(harness.state().auditScreenState, 'history');
    assert.equal(harness.state().activeChatId, historyChat.id);
    assert.deepEqual(harness.state().chatSessions.map((chat) => chat.id), ['history-chat']);
    assert.deepEqual(harness.writes, []);
});

test('Activity hide/show and ordinary workspace changes preserve the complete mounted Audit screen', async () => {
    const harness = makeLifecycleHarness({ chats: [historyChat] });
    harness.restoreLocalChatInterface();
    harness.showAuditChatLocally('history-chat', 'history');
    harness.chatInputEl.value = 'draft survives';
    harness.workspaceEl.scrollTop = 173;
    harness.historyOpen = true;
    harness.expandedFigure = true;
    const before = harness.state();

    harness.dispatch('hidden');
    await Promise.resolve();
    harness.dispatch('shown');
    await Promise.resolve();
    // File/viewer tab changes emit no Audit lifecycle transition at all.
    const after = harness.state();

    assert.equal(after.activeChatId, before.activeChatId);
    assert.equal(after.auditScreenState, 'history');
    assert.equal(after.draft, 'draft survives');
    assert.equal(after.scrollTop, 173);
    assert.equal(harness.historyOpen, true);
    assert.equal(harness.expandedFigure, true);
    assert.deepEqual(harness.writes, []);
});

test('running jobs and persisted queues restore their owning chat across true reopen', async () => {
    const running = makeLifecycleHarness({
        chats: [historyChat], activeChatId: historyChat.id,
        pending: [{ jobId: 'job-1', chatId: historyChat.id }],
    });
    running.restoreLocalChatInterface();
    assert.equal(running.state().auditScreenState, 'active');
    assert.equal(running.state().activeChatId, historyChat.id);
    running.dispatch('closed');
    running.dispatch('reopened');
    await Promise.resolve();
    assert.equal(running.state().activeChatId, historyChat.id);
    assert.equal(running.state().auditScreenState, 'active');

    const queued = makeLifecycleHarness({
        chats: [historyChat], activeChatId: historyChat.id,
        queues: { [historyChat.id]: [{ text: 'Next queued question', createdAt: 2 }] },
    });
    queued.restoreLocalChatInterface();
    await Promise.resolve();
    assert.equal(queued.state().activeChatId, historyChat.id);
    assert.equal(queued.state().auditScreenState, 'active');
    assert.equal(queued.resumeCalls, 1);
});

test('a stopping operation remains observable across a true panel close/reopen', async () => {
    const harness = makeLifecycleHarness({
        chats: [historyChat], activeChatId: historyChat.id,
        stopping: { jobId: 'stopping-job', chatId: historyChat.id },
    });
    harness.restoreLocalChatInterface();
    harness.dispatch('closed');
    harness.dispatch('reopened');
    await Promise.resolve();
    assert.equal(harness.state().auditScreenState, 'active');
    assert.equal(harness.state().activeChatId, historyChat.id);
});

test('terminal work remains visible through panel close/reopen within the project', async () => {
    const harness = makeLifecycleHarness({
        chats: [historyChat], activeChatId: historyChat.id,
        pending: [{ jobId: 'job-1', chatId: historyChat.id }],
    });
    harness.restoreLocalChatInterface();
    harness.clearPending();
    harness.reconcileAuditScreenAfterWork(historyChat.id);
    await Promise.resolve();
    assert.equal(harness.state().auditScreenState, 'history');
    assert.equal(harness.state().activeChatId, historyChat.id);

    harness.dispatch('closed');
    harness.dispatch('reopened');
    await Promise.resolve();
    assert.equal(harness.state().auditScreenState, 'history');
    assert.equal(harness.state().activeChatId, historyChat.id);
});

test('a job that completes while collapsed remains visible on reopening', async () => {
    const harness = makeLifecycleHarness({
        chats: [historyChat], activeChatId: historyChat.id,
        pending: [{ jobId: 'job-1', chatId: historyChat.id }],
    });
    harness.restoreLocalChatInterface();
    harness.dispatch('closed');
    harness.clearPending();
    harness.reconcileAuditScreenAfterWork(historyChat.id);
    await Promise.resolve();
    harness.dispatch('reopened');
    await Promise.resolve();
    assert.equal(harness.state().auditScreenState, 'history');
    assert.equal(harness.state().activeChatId, historyChat.id);
});

test('same-project navigation restores the selected chat and draft without conversation writes', () => {
    const harness = makeLifecycleHarness({ chats: [historyChat],
        visitView: { chatId: historyChat.id, draft: 'Follow-up in progress' } });
    harness.restoreLocalChatInterface();
    assert.equal(harness.state().activeChatId, historyChat.id);
    assert.equal(harness.state().draft, 'Follow-up in progress');
    assert.deepEqual(harness.state().chatHistory, historyChat.messages);
    assert.deepEqual(harness.writes, []);
});

test('explicit New Chat survives same-project navigation even with a durable active chat', () => {
    const harness = makeLifecycleHarness({ chats: [historyChat], activeChatId: historyChat.id,
        visitView: { chatId: null, draft: 'A new question' } });
    harness.restoreLocalChatInterface();
    assert.equal(harness.state().activeChatId, null);
    assert.equal(harness.state().draft, 'A new question');
    assert.deepEqual(harness.writes, []);
});

test('deleted visit chat cannot be revived or leak its draft into New Chat', () => {
    const harness = makeLifecycleHarness({ chats: [historyChat],
        visitView: { chatId: 'deleted-chat', draft: 'Old draft' } });
    harness.restoreLocalChatInterface();
    assert.equal(harness.state().activeChatId, null);
    assert.equal(harness.state().draft, '');
});

test('Round Workspace emits distinct true-close and temporary-hide lifecycle transitions', () => {
    const notifySource = sourceBetween(
        workspaceSource,
        '  function notifyAuditLifecycle(transition)',
        '  function applyPanelTab()',
    );
    const selectSource = sourceBetween(
        workspaceSource,
        '  function selectPanelTab(nextTab)',
        "  document.querySelectorAll('[data-panel-tab]')",
    );
    const collapseSource = sourceBetween(
        workspaceSource,
        '  function setPanelCollapsed(collapsed)',
        "  els.collapse.addEventListener('click'",
    );
    const transitions = [];
    const state = { panelTab: 'chat', panelCollapsed: false };
    const windowObject = { dispatchEvent(event) { transitions.push(event.detail.transition); } };
    class CustomEventObject { constructor(_name, options) { this.detail = options.detail; } }
    const controls = new Function(
        'window', 'CustomEvent', 'state', 'sessionStorage', 'panelTabStorageKey',
        'applyPanelTab', 'applyPanel',
        `${notifySource}${selectSource}${collapseSource}
         return { selectPanelTab, setPanelCollapsed };`,
    )(
        windowObject, CustomEventObject, state, { setItem() {} }, 'panel-key', () => {}, () => {},
    );

    controls.selectPanelTab('activity');
    controls.selectPanelTab('chat');
    controls.setPanelCollapsed(true);
    controls.setPanelCollapsed(false);
    assert.deepEqual(transitions, ['hidden', 'shown', 'closed', 'reopened']);
    assert.equal((workspaceSource.match(/notifyAuditLifecycle\(/g) || []).length >= 3, true);
    const viewerHandlers = sourceBetween(
        workspaceSource,
        '  function renderPanes()',
        '  function notifyAuditLifecycle(transition)',
    );
    assert.doesNotMatch(viewerHandlers, /notifyAuditLifecycle|roundworkspace:audit-lifecycle/);
});

test('late explicit-history restoration cannot reselect after a true close', async () => {
    const reset = deferred();
    let activeChatId = null;
    let chatHistory = [];
    let auditScreenState = 'new';
    let auditClosed = false;
    let auditLifecycleRevision = 0;
    const chatSessions = [{ ...historyChat, unread: false }];
    const pendingChatIds = new Set();
    const chatInputEl = { disabled: false, focus() {} };
    const chatSendBtn = { disabled: false };
    let restoreCalls = 0;
    const activate = new Function(
        'chatSessions', 'activeChatId', 'chatHistory', 'auditScreenState', 'auditClosed',
        'auditLifecycleRevision', 'pendingChatIds', 'chatInputEl', 'chatSendBtn',
        'isUnlistedDraftChat', 'getActiveChat', 'cleanupUnlistedDraftChats',
        'persistAuditChatState', 'chatHasActiveAuditWork', 'normalizeStoredAuditChatMessages',
        'clearChatBubbles', 'renderStoredChatHistory', 'renderChatList', 'revealActiveChatRow',
        'scrollToBottomAfterLayout', 'sessionReady', 'resetAuditConversationOnServer',
        'restoreAuditConversationOnServer', 'refreshSendEnabled', 'updateComposerForActiveChat',
        'setHistoryDrawerOpen',
        `${activateSource}
         return {
             activateChat,
             close() { auditClosed = true; auditLifecycleRevision += 1; activeChatId = null; chatHistory = []; },
             state() { return { activeChatId, chatHistory, auditScreenState, auditClosed }; },
         };`,
    )(
        chatSessions, activeChatId, chatHistory, auditScreenState, auditClosed,
        auditLifecycleRevision, pendingChatIds, chatInputEl, chatSendBtn,
        () => false, () => null, () => {}, () => {}, () => false,
        (entries) => entries.slice(), () => {}, () => {}, () => {}, () => {}, () => {},
        true, () => reset.promise, async () => { restoreCalls += 1; }, () => {}, () => {}, () => {},
    );

    const selecting = activate.activateChat('history-chat');
    activate.close();
    reset.resolve();
    await selecting;
    await Promise.resolve();
    assert.equal(activate.state().activeChatId, null);
    assert.deepEqual(activate.state().chatHistory, []);
    assert.equal(restoreCalls, 0);
});

test('the first send from blank New Chat creates exactly one conversation identity', () => {
    let activeChatId = null;
    let auditScreenState = 'new';
    let chatHistory = [];
    const chatSessions = [];
    const pendingChatIds = new Set();
    const chatInputEl = { value: 'First question', focus() {} };
    let created = 0;
    let submitted = 0;
    const send = new Function(
        'activeChatId', 'auditScreenState', 'chatHistory', 'chatSessions', 'pendingChatIds',
        'chatInputEl', 'createEmptyChat', 'captureViewerContextSnapshot', 'autoSizeInput',
        'refreshSendEnabled', 'renderChatList', 'queueAuditMessage', 'persistAuditChatState',
        'renderQueuedMessages', 'syncEmptyState', 'normalizeStoredAuditChatMessages',
        'appendUserMessageToChat', 'renderStoredChatHistory', 'submitAuditJob',
        `function getActiveChat() { return chatSessions.find((chat) => chat.id === activeChatId) || null; }
         ${ensureChatSource}${sendSource}
         return { sendMessage, state() { return { activeChatId, auditScreenState, count: chatSessions.length }; } };`,
    )(
        activeChatId, auditScreenState, chatHistory, chatSessions, pendingChatIds, chatInputEl,
        () => ({ id: `chat-${++created}`, title: 'New chat', messages: [] }),
        () => null, () => {}, () => {}, () => {}, () => {}, () => {}, () => {}, () => {},
        (entries) => entries.slice(), (chatId, text) => {
            const chat = chatSessions.find((entry) => entry.id === chatId);
            chat.messages.push({ role: 'user', text });
            chatHistory = chat.messages;
        },
        () => {}, () => { submitted += 1; pendingChatIds.add('chat-1'); },
    );

    send.sendMessage();
    assert.equal(created, 1);
    assert.equal(submitted, 1);
    assert.deepEqual(send.state(), { activeChatId: 'chat-1', auditScreenState: 'history', count: 1 });
    assert.match(sendSource, /if \(!activeChatId\) \{[\s\S]*ensureActiveChat\(\)/);
});

test('idle state loading and New Chat contain no persistence or destructive reset path', () => {
    const loader = sourceBetween(
        auditSource,
        '    function readAuditChatStateFromStorage()',
        '    function writeDurableAuditChatState',
    );
    const blank = sourceBetween(
        auditSource,
        '    function showBlankNewChat(options)',
        '    function reconcileAuditScreenAfterWork',
    );
    const startNew = sourceBetween(
        auditSource,
        '    function startNewChat()',
        '    async function restoreAuditConversationOnServer',
    );
    assert.doesNotMatch(loader, /setItem|queueDurableAuditChatState|writeDurableAuditChatState/);
    assert.doesNotMatch(blank, /persistAuditChatState|resetAuditConversationOnServer|createEmptyChat/);
    assert.doesNotMatch(startNew, /persistAuditChatState|resetAuditConversationOnServer|createEmptyChat/);
    const normalize = sourceBetween(
        auditSource,
        '    function normalizeChatSession(chat, fallbackIndex)',
        '    function sortChatSessions()',
    ) + sourceBetween(
        auditSource,
        '    function normalizeAuditChatStatePayload(parsed, source)',
        '    function readAuditChatStateAtKey(storageKey)',
    );
    assert.doesNotMatch(normalize, /createChatId|createEmptyChat/);
    assert.match(normalize, /legacyChatId/);
    assert.doesNotMatch(auditSource, /beforeunload'[\s\S]{0,120}persistAuditChatState/);
});
