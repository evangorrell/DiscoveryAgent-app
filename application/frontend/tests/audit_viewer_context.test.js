const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspacePath = path.join(__dirname, '..', 'da_selection_ui', 'static', 'round_workspace.js');
const auditPath = path.join(__dirname, '..', 'da_selection_ui', 'static', 'project_audit_agent.js');
const workspaceSource = fs.readFileSync(workspacePath, 'utf8');
const auditSource = fs.readFileSync(auditPath, 'utf8');

const helperStart = workspaceSource.indexOf('  const viewerContextString');
const helperEnd = workspaceSource.indexOf('  function visiblyDisplayed', helperStart);
assert.notEqual(helperStart, -1);
assert.notEqual(helperEnd, -1);
const helperSource = workspaceSource.slice(helperStart, helperEnd);

const buildViewerContextSnapshot = new Function(
    'fileExt', 'isFigureFile', 'isRoundReport', 'viewerReportKind', 'reportArtifactPath', 'validViewerFile',
    `${helperSource}
     return buildViewerContextSnapshot;`,
)(
    (name) => {
        const index = String(name || '').lastIndexOf('.');
        return index < 0 ? '' : String(name).slice(index).toLowerCase();
    },
    (name) => /\.(png|jpe?g|gif|svg|webp|tiff?)$/i.test(String(name || '')),
    (file) => Boolean(file && file.sourceKind !== 'project-code' && file.path === 'round_report.md'),
    (file) => {
        if (!file || file.sourceKind === 'project-code') return null;
        if (file.reportKind === 'round' || file.path === 'round_report.md') return 'round';
        if (file.reportKind === 'memory' || file.path === 'memory_report.md') return 'memory';
        return null;
    },
    (file) => {
        const kind = file.reportKind
            || (file.path === 'round_report.md' ? 'round' : null)
            || (file.path === 'memory_report.md' ? 'memory' : null);
        if (kind && ['summary', 'full'].includes(file.reportView)) {
            return kind + '_report' + (file.reportView === 'summary' ? '_summary' : '') + '.md';
        }
        return file.artifactPath || file.path || '';
    },
    (file) => Boolean(file && (Number.isFinite(file.round) || file.sourceKind === 'project-code')
        && typeof file.path === 'string' && typeof file.name === 'string'),
);

function file(name, round, extra = {}) {
    return {
        round,
        source: `source-${round}`,
        sourceKind: 'checkpoint',
        artifactPath: name,
        path: name,
        name,
        ...extra,
    };
}

test('captures only the active tab from each visible pane', () => {
    const panes = [
        { tabs: [file('background.md', 0), file('active.md', 0)], activeTab: 1 },
        { tabs: [file('other-background.md', 1), file('visible.png', 1)], activeTab: 1 },
    ];
    const snapshot = buildViewerContextSnapshot(
        panes,
        [{ visible: true, figures: [] }, { visible: true, figures: [] }],
        'project-a',
        42,
    );
    assert.deepEqual(snapshot.artifacts.map((entry) => entry.path), ['active.md', 'visible.png']);
    assert.deepEqual(snapshot.artifacts.map((entry) => entry.pane), [0, 1]);
    assert.equal(snapshot.artifacts.some((entry) => entry.path.includes('background')), false);
});

test('hidden panes and an entirely empty viewer contribute no context', () => {
    const panes = [
        { tabs: [file('visible.md', 0)], activeTab: 0 },
        { tabs: [file('hidden.md', 1)], activeTab: 0 },
    ];
    const one = buildViewerContextSnapshot(
        panes,
        [{ visible: true, figures: [] }, { visible: false, figures: [] }],
        'project-a',
        43,
    );
    assert.deepEqual(one.artifacts.map((entry) => entry.path), ['visible.md']);
    assert.equal(buildViewerContextSnapshot(panes, [{ visible: false }, { visible: false }], 'project-a', 44), null);
});

test('tab switches affect only later immutable snapshots', () => {
    const pane = { tabs: [file('first.md', 0), file('second.md', 1)], activeTab: 0 };
    const first = buildViewerContextSnapshot([pane], [{ visible: true, figures: [] }], 'project-a', 45);
    pane.activeTab = 1;
    pane.tabs[0].path = 'mutated-after-send.md';
    const second = buildViewerContextSnapshot([pane], [{ visible: true, figures: [] }], 'project-a', 46);
    assert.equal(first.artifacts[0].path, 'first.md');
    assert.equal(second.artifacts[0].path, 'second.md');
});

test('memory report snapshots expose only the selected persisted representation', () => {
    const memory = file('memory_report.md', 4, {
        reportKind: 'memory',
        reportView: 'summary',
        hasReportSummary: true,
        hasReportFull: true,
    });
    const pane = { tabs: [memory], activeTab: 0 };
    const summary = buildViewerContextSnapshot(
        [pane], [{ visible: true, figures: [] }], 'project-a', 46,
    );
    memory.reportView = 'full';
    const full = buildViewerContextSnapshot(
        [pane], [{ visible: true, figures: [] }], 'project-a', 47,
    );

    assert.equal(summary.artifacts.length, 1);
    assert.equal(summary.artifacts[0].path, 'memory_report.md');
    assert.equal(summary.artifacts[0].artifactPath, 'memory_report_summary.md');
    assert.equal(summary.artifacts[0].reportKind, 'memory');
    assert.equal(summary.artifacts[0].reportView, 'summary');
    assert.equal(full.artifacts[0].artifactPath, 'memory_report.md');
    assert.equal(full.artifacts[0].reportKind, 'memory');
    assert.equal(full.artifacts[0].reportView, 'full');
});

test('only expanded visible figure indexes supplied by the active artifact are captured', () => {
    const pane = { tabs: [file('background.md', 0), file('report.md', 1)], activeTab: 1 };
    const snapshot = buildViewerContextSnapshot(
        [pane],
        [{ visible: true, figures: [{ index: 0 }, { index: 2 }] }],
        'project-a',
        47,
    );
    assert.deepEqual(snapshot.artifacts[0].figures, [{ index: 0 }, { index: 2 }]);
    assert.equal(snapshot.artifacts[0].path, 'report.md');
});

test('Audit messages keep viewer metadata internal and send-time owned', () => {
    const sendSource = auditSource.slice(
        auditSource.indexOf('    function sendMessage()'),
        auditSource.indexOf('    newChatBtn &&', auditSource.indexOf('    function sendMessage()')),
    );
    const exportSource = auditSource.slice(
        auditSource.indexOf('    function exportChat('),
        auditSource.indexOf('    function renameChat', auditSource.indexOf('    function exportChat(')),
    );
    const renderSource = auditSource.slice(
        auditSource.indexOf('    function appendTurn('),
        auditSource.indexOf('    function renderAssistantReplyIntoPendingTurn', auditSource.indexOf('    function appendTurn(')),
    );
    assert.match(sendSource, /const viewerContext = captureViewerContextSnapshot\(\)/);
    assert.match(sendSource, /queueAuditMessage\(requestChatId, message, viewerContext\)/);
    assert.match(sendSource, /appendUserMessageToChat\(requestChatId, message, viewerContext\)/);
    assert.match(sendSource, /submitAuditJob\(requestChatId, message, requestContext, null, viewerContext\)/);
    assert.doesNotMatch(exportSource, /viewerContext|viewer_context/);
    assert.doesNotMatch(renderSource, /viewerContext|viewer_context/);
    assert.equal(workspaceSource.includes('context chip'), false);
    assert.equal(workspaceSource.includes('Viewing:'), false);
});
