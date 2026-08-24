const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'round_workspace.js'),
    'utf8',
);

function sourceBetween(start, end) {
    const first = source.indexOf(start);
    const last = source.indexOf(end, first);
    assert.notEqual(first, -1, `missing ${start}`);
    assert.notEqual(last, -1, `missing ${end}`);
    return source.slice(first, last);
}

function workspaceFileFor() {
    const body = sourceBetween('  function workspaceFile', '  function safeArtifactIdentityPath');
    return new Function(`${body}; return workspaceFile;`)();
}

function memoryItem(summary, full) {
    return {
        name: 'memory_report.md',
        path: 'memory_report.md',
        artifact_path: 'memory_report.md',
        source: 'checkpoint',
        source_kind: 'checkpoint',
        type: 'file',
        report_kind: 'memory',
        has_report_summary: summary,
        has_report_full: full,
    };
}

const owner = { round_index: 3, storage_round_index: 3 };

test('Summary is the default when both persisted memory representations exist', () => {
    const file = workspaceFileFor()(memoryItem(true, true), owner);
    assert.equal(file.reportKind, 'memory');
    assert.equal(file.reportView, 'summary');
    assert.equal(file.hasReportSummary, true);
    assert.equal(file.hasReportFull, true);
});

test('a newly opened report ignores a Full view selected for another report', () => {
    const file = workspaceFileFor()(memoryItem(true, true), owner);
    assert.equal(file.path, 'memory_report.md');
    assert.equal(file.artifactPath, 'memory_report.md');
    assert.equal(file.reportView, 'summary');
});

test('one-sided historical memory reports choose the only available representation', () => {
    const workspaceFile = workspaceFileFor();
    assert.equal(workspaceFile(memoryItem(false, true), owner).reportView, 'full');
    assert.equal(workspaceFile(memoryItem(true, false), owner).reportView, 'summary');
});

test('switching report views is local, read-only, and uses Summary as the canonical URL default', () => {
    const setView = sourceBetween('  function setReportView', '  function codePathControl');
    assert.match(setView, /file\.reportView=view/);
    assert.match(setView, /if\(view==='full'\)params\.set\('report_view','full'\)/);
    assert.match(setView, /else params\.delete\('report_view'\)/);
    assert.doesNotMatch(setView, /fetch\(|api\(|POST|write|summar/i);
});

test('the shared accessible toggle appears only when both report views are available', () => {
    const controls = sourceBetween('  function artifactViewControls', '  function renderPanes');
    assert.match(controls, /file\.hasReportSummary&&file\.hasReportFull/);
    assert.match(controls, /'Summary'/);
    assert.match(controls, /'Full Report'/);
    assert.match(controls, /setAttribute\('aria-pressed'/);
    assert.match(controls, /Memory':'Round'/);
});

test('the review deep link is the only initial path that may request Full Report', () => {
    const reportLookup = sourceBetween('  function reportFile', '  function normalizeActivity');
    assert.match(reportLookup, /roundReportFile\(reviewRound,requestedReportView\)/);
    const workspace = sourceBetween('  function workspaceFile', '  function safeArtifactIdentityPath');
    assert.doesNotMatch(workspace, /requestedReportView|requestedRound/);
});

test('closing a report clears its URL view so a fresh reopen defaults to Summary', () => {
    const clearView = sourceBetween('  function clearReportViewUrl', '  function codePathControl');
    const render = sourceBetween('  function renderPanes', '  function openFile');
    assert.match(clearView, /params\.delete\('report_view'\)/);
    assert.match(render, /if\(isReport\(tabFile\)\)clearReportViewUrl\(\)/);
});

test('reselecting a logical report merges into its existing tab and preserves a valid view', () => {
    const merge = sourceBetween('  function reportViewAvailable', '  function hydrateRestoredViewerSources');
    const open = sourceBetween('  function openFile', '  function detachDraggedTab');
    assert.match(merge, /merged\.reportView=existing\.reportView/);
    assert.match(open, /sameFileIdentity\(tab,file\)/);
    assert.match(open, /mergeViewerFile\(pane\.tabs\[existing\],file\)/);
});

test('ordinary and project-code Markdown use their appropriate trusted content paths', () => {
    const render = sourceBetween('  async function renderFileBody', '  function applyPaneRatio');
    assert.match(render, /trustedMarkdownPayload=markdownFile&&file\.sourceKind!=='project-code'/);
    assert.match(render, /trustedMarkdownPayload\?markdownDisplayUrl\(file\):artifactUrl\(file\)/);
    assert.match(render, /renderReportMarkdown\(article,text,figures\)/);
    assert.match(source, /if\(!report&&!downloadable\)return null/);
});
