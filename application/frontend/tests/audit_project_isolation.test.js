const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const auditJs = fs.readFileSync(
    path.join(root, 'da_selection_ui/static/project_audit_agent.js'),
    'utf8',
);
const template = fs.readFileSync(
    path.join(root, 'da_selection_ui/templates/round_workspace.html'),
    'utf8',
);

test('Audit browser state is keyed only by immutable project identity', () => {
    assert.match(template, /projectId: \{\{ project_id/);
    assert.doesNotMatch(template, /legacyChatStorageScopes/);
    assert.match(
        auditJs,
        /da_audit_chat_state_v\$\{CHAT_STORAGE_VERSION\}_project_\$\{projectId\}/,
    );
    assert.match(auditJs, /parsed\.projectId !== projectId/);
    assert.match(auditJs, /projectId,\s*\n\s*updatedAt:/);
    assert.doesNotMatch(auditJs, /legacyAuditChatStorageKeys/);
    assert.doesNotMatch(auditJs, /localStorage\.length/);

    const key = (projectId) => `da_audit_chat_state_v2_project_${projectId}`;
    assert.notEqual(key('prj_old'), key('prj_new'));
    assert.equal(key('prj_old'), key('prj_old'));
});

test('all Audit mutation and job transports carry and verify project identity', () => {
    for (const endpoint of [
        '/api/conversation_state',
        '/api/audit_chat_restore',
        '/api/reset_chat_context',
        '/api/initialize',
        '/api/audit_chat_jobs',
        '/api/chat',
    ]) {
        assert.ok(auditJs.includes(endpoint), `missing ${endpoint}`);
    }
    assert.match(auditJs, /project_id: projectId/);
    assert.match(auditJs, /\?project_id=\$\{encodeURIComponent\(projectId\)\}/);
    assert.match(auditJs, /responseBelongsToActiveProject/);
    assert.match(auditJs, /job\.projectId !== projectId/);
    assert.match(auditJs, /payload\.project_id === projectId/);
});

test('ambiguous old slug and global browser keys are preserved but never adopted', () => {
    assert.doesNotMatch(auditJs, /removeItem\(/);
    assert.doesNotMatch(auditJs, /projectSuffix/);
    assert.doesNotMatch(auditJs, /config\.legacyChatStorageScopes/);
    assert.doesNotMatch(auditJs, /chatStorageScope/);
    assert.match(auditJs, /source !== 'durable' && parsed\.projectId !== projectId/);
});
