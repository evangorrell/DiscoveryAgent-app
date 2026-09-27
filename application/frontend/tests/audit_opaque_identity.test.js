const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../da_selection_ui/static/project_audit_agent.js'), 'utf8');

function identityHarness(projectId, storage = new Map()) {
    const slice = (start, end) => {
        const first = source.indexOf(start);
        const last = source.indexOf(end, first);
        assert.ok(first >= 0 && last > first);
        return source.slice(first, last);
    };
    const context = vm.createContext({
        config: { projectId }, CHAT_STORAGE_VERSION: 2,
        AUDIT_CHAT_STORAGE_MAX_CHATS: 50, activeChatId: 'chat-1',
        chatSessions: [{ id: 'chat-1', messages: [{ role: 'user', text: 'Explain this result' }] }],
        pendingAuditJobs: [], queuedAuditMessages: {}, saved: [],
        syncActiveChatFromHistory() {}, isUnlistedDraftChat: () => false,
        normalizeChatSession: chat => chat,
        normalizePendingAuditJobs: jobs => jobs || [],
        normalizeQueuedAuditMessages: queues => queues || {},
        localStorage: {
            setItem: (key, value) => storage.set(key, value),
            getItem: key => storage.get(key) || null,
        },
    });
    context.queueDurableAuditChatState = state => context.saved.push(state);
    vm.runInContext([
        slice('    const projectId =', '    function setHistoryDrawerOpen('),
        slice('    function normalizeAuditChatStatePayload(', '    function readAuditChatStateFromStorage('),
        slice('    function persistAuditChatState(', '    function renderChatList('),
    ].join('\n'), context);
    return { context, storage, run: code => vm.runInContext(code, context) };
}

for (const id of ['optic-nerve-regeneration-734492', 'prj_audit123', 'project.with.dots', '42', '研究 project']) {
    test(`Audit accepts opaque ID ${id}, saves messages and restores them after reload`, () => {
        const first = identityHarness(id);
        assert.equal(first.run('responseBelongsToActiveProject({project_id: projectId})'), true);
        assert.equal(first.run('responseBelongsToActiveProject({project_id: "another-project"})'), false);
        first.run('persistAuditChatState()');
        assert.equal(first.context.saved.length, 1);
        assert.equal(first.context.saved[0].projectId, id);
        const reloaded = identityHarness(id, first.storage);
        const restored = reloaded.run('readAuditChatStateAtKey(auditChatStorageKey)');
        assert.equal(restored.chats[0].messages[0].text, 'Explain this result');
        const other = identityHarness('another-project', first.storage);
        assert.equal(other.run('readAuditChatStateAtKey(auditChatStorageKey)'), null);
    });
}

test('Audit refuses missing project identity without writing chat state', () => {
    for (const id of ['', '   ', null, undefined]) {
        const harness = identityHarness(id);
        assert.equal(harness.run('responseBelongsToActiveProject({project_id: projectId})'), false);
        harness.run('persistAuditChatState()');
        assert.equal(harness.storage.size, 0);
        assert.equal(harness.context.saved.length, 0);
    }
});
