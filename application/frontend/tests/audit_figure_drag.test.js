const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'da_selection_ui', 'static');
const workspaceSource = fs.readFileSync(path.join(root, 'round_workspace.js'), 'utf8');
const figureSource = fs.readFileSync(path.join(root, 'report_figures.js'), 'utf8');
const auditSource = fs.readFileSync(path.join(root, 'project_audit_agent.js'), 'utf8');

function sourceSlice(source, startText, endText) {
    const start = source.indexOf(startText);
    const end = source.indexOf(endText, start);
    assert.notEqual(start, -1, `missing ${startText}`);
    assert.notEqual(end, -1, `missing ${endText}`);
    return source.slice(start, end);
}

function canonicalHarness() {
    const helpers = sourceSlice(
        workspaceSource,
        '  function safeArtifactIdentityPath(value)',
        '  function collectRoundFiles',
    );
    const state = {
        codeTree: { children: [{
            type: 'file', visibility: 'research', name: 'atf3.png',
            path: 'analysis_output/atf3.png', artifact_path: 'analysis_output/atf3.png',
            source_kind: 'project-code', size: 42,
        }] },
        rounds: [{
            round_index: 2,
            tree: { children: [{
                type: 'file', visibility: 'research', name: 'historical.png',
                path: 'images/historical.png', artifact_path: 'images/historical.png',
                source_kind: 'external', source: 'round-2-source', storage_round_index: 2,
                size: 24,
            }] },
        }],
        dragFile: null,
        dragSource: null,
        dragKind: null,
    };
    const CFG = { projectSlug: 'project-a' };
    const isFigureFile = value => /\.(?:png|jpe?g|gif|svg|webp|tiff?)$/i.test(value || '');
    const workspaceFile = (item, owner) => ({
        round: owner.sourceScope === 'code' ? null : item.storage_round_index ?? owner.round_index,
        source: item.source,
        sourceKind: item.source_kind || (owner.sourceScope === 'code' ? 'project-code' : 'checkpoint'),
        artifactPath: item.artifact_path || item.path,
        path: item.path,
        name: item.name,
        size: item.size,
    });
    const api = new Function(
        'state', 'CFG', 'isFigureFile', 'workspaceFile', 'hideDropOverlay', 'auditFigureDragMime',
        `${helpers}; return { canonicalAuditFigureFile, startAuditFigureDrag, auditFigureFileFromDrop, auditFigureDragPayload, sameFileIdentity };`,
    )(
        state, CFG, isFigureFile, workspaceFile, () => {},
        'application/x-discoveryagent-audit-figure+json',
    );
    return { api, state };
}

test('trusted Audit figure payload resolves only through the registered project tree', () => {
    const { api } = canonicalHarness();
    const code = api.canonicalAuditFigureFile({
        version: 1, project: 'project-a', round: null, sourceKind: 'project-code',
        source: '', artifactPath: 'analysis_output/atf3.png',
    });
    const historical = api.canonicalAuditFigureFile({
        version: 1, project: 'project-a', round: 2, sourceKind: 'external',
        source: 'round-2-source', artifactPath: 'images/historical.png',
    });
    assert.equal(code.name, 'atf3.png');
    assert.equal(code.round, null);
    assert.equal(historical.name, 'historical.png');
    assert.equal(historical.round, 2);

    for (const invalid of [
        { version: 1, project: 'project-b', round: null, sourceKind: 'project-code', source: '', artifactPath: 'analysis_output/atf3.png' },
        { version: 1, project: 'project-a', round: null, sourceKind: 'project-code', source: '', artifactPath: '/tmp/atf3.png' },
        { version: 1, project: 'project-a', round: null, sourceKind: 'project-code', source: '', artifactPath: '../atf3.png' },
        { version: 1, project: 'project-a', round: null, sourceKind: 'project-code', source: '', artifactPath: 'analysis_output/missing.png' },
        { version: 1, project: 'project-a', round: null, sourceKind: 'project-code', source: '', artifactPath: 'analysis_output/data.csv' },
        { version: 1, project: 'project-a', round: 3, sourceKind: 'external', source: 'round-2-source', artifactPath: 'images/historical.png' },
    ]) assert.equal(api.canonicalAuditFigureFile(invalid), null);
});

test('Audit drag emits only the private canonical identity and suppresses native URL payloads', () => {
    const { api, state } = canonicalHarness();
    const data = new Map([
        ['text/plain', '/private/project/analysis_output/atf3.png'],
        ['text/uri-list', 'file:///private/project/analysis_output/atf3.png'],
    ]);
    const dataTransfer = {
        effectAllowed: 'all',
        clearData() { data.clear(); },
        setData(type, value) { data.set(type, value); },
        getData(type) { return data.get(type) || ''; },
    };
    const accepted = api.startAuditFigureDrag({ dataTransfer }, {
        project: 'project-a', round: null, sourceKind: 'project-code',
        source: '', artifactPath: 'analysis_output/atf3.png',
    });
    assert.equal(accepted, true);
    assert.equal(state.dragKind, 'audit-figure');
    assert.deepEqual([...data.keys()], ['application/x-discoveryagent-audit-figure+json']);
    assert.doesNotMatch([...data.values()][0], /private|file:\/\//);
    assert.equal(api.auditFigureFileFromDrop(dataTransfer).name, 'atf3.png');

    data.set('application/x-discoveryagent-audit-figure+json', JSON.stringify({
        version: 1, project: 'project-b', round: null, sourceKind: 'project-code',
        source: '', artifactPath: 'analysis_output/atf3.png',
    }));
    assert.equal(api.auditFigureFileFromDrop(dataTransfer), null);
});

test('canonical opener focuses an existing figure tab across panes instead of duplicating it', () => {
    const openFileSource = sourceSlice(workspaceSource, '  function openFile(', '  function detachDraggedTab');
    const existing = {
        round: 2, sourceKind: 'external', source: 'round-2-source',
        artifactPath: 'images/historical.png', path: 'images/historical.png', name: 'historical.png',
    };
    const state = {
        panes: [
            { tabs: [{ round: 0, sourceKind: 'checkpoint', source: 'round-0', artifactPath: 'round_report.md', path: 'round_report.md', name: 'round_report.md' }], activeTab: 0 },
            { tabs: [existing], activeTab: 0 },
        ],
        activePane: 0,
        orientation: 'horizontal',
        paneRatio: 50,
    };
    let renders = 0;
    const sameFileIdentity = (left, right) => left.round === right.round
        && left.sourceKind === right.sourceKind
        && String(left.source || '') === String(right.source || '')
        && (left.artifactPath || left.path) === (right.artifactPath || right.path);
    const openFile = new Function(
        'state', 'sameFileIdentity', 'mergeViewerFile', 'renderPanes',
        `${openFileSource}; return openFile;`,
    )(state, sameFileIdentity, (left, right) => ({ ...left, ...right }), () => { renders += 1; });
    openFile({ ...existing, size: 99 }, false, 'center', false, true);
    assert.equal(state.activePane, 1);
    assert.equal(state.panes.flatMap(pane => pane.tabs).filter(tab => tab.name === 'historical.png').length, 1);
    assert.equal(state.panes[1].tabs[0].size, 99);
    assert.equal(renders, 1);
});

test('shared renderer enables drag sources only behind the explicit Audit option', () => {
    const attachSource = sourceSlice(
        figureSource,
        '    function attachAuditFigureDragging(',
        '    function renderMarkdown(',
    );
    const listeners = new Map();
    const makeSource = () => ({
        draggable: false,
        addEventListener(type, handler) { listeners.set(this.label + ':' + type, handler); },
    });
    const image = makeSource(); image.label = 'image';
    const stage = makeSource(); stage.label = 'stage'; stage.querySelector = selector => selector === 'img' ? image : null;
    const caption = makeSource(); caption.label = 'caption';
    const control = makeSource(); control.label = 'control';
    const classes = new Set();
    const details = {
        classList: { add(value) { classes.add(value); } },
        querySelector(selector) {
            if (selector === '.rw-figure-image-stage') return stage;
            if (selector === 'figcaption') return caption;
            return null;
        },
        querySelectorAll() { return [control]; },
    };
    const element = { querySelectorAll: () => [details] };
    const attach = new Function(`${attachSource}; return attachAuditFigureDragging;`)();

    attach(element, [{ available: true, artifact: { artifactPath: 'figure.png' } }], {});
    assert.equal(stage.draggable, false);

    let started = 0;
    let ended = 0;
    attach(element, [{ available: true, artifact: { artifactPath: 'figure.png' } }], {
        auditFigureDrag: {
            start() { started += 1; return true; },
            end() { ended += 1; },
        },
    });
    assert.equal(stage.draggable, true);
    assert.equal(caption.draggable, true);
    assert.equal(image.draggable, true);
    assert.equal(control.draggable, false);
    assert.ok(classes.has('is-audit-artifact-draggable'));
    listeners.get('image:dragstart')({ dataTransfer: {}, stopPropagation() {}, preventDefault() {} });
    listeners.get('image:dragend')();
    assert.equal(started, 1);
    assert.equal(ended, 1);
});

test('Audit opts in while every other shared Markdown call remains unchanged', () => {
    assert.match(auditSource, /renderReportMarkdown\([\s\S]*auditFigureDragOptions/);
    assert.match(figureSource, /renderReportMarkdown\(element, text, figures, options = \{\}\)/);
    assert.match(figureSource, /attachAuditFigureDragging\(element, figures, options\)/);
    assert.doesNotMatch(
        sourceSlice(workspaceSource, '  const renderReportMarkdown', '  function renderStandaloneImage'),
        /auditFigureDrag|draggable/,
    );
    assert.match(figureSource, /attachEmbeddedFigureResizer\(shell, stage\)/);
    assert.match(figureSource, /details\.open = false/);
});
