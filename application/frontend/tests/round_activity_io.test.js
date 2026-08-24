const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'round_workspace.js'),
    'utf8',
);

function functionSource(name, nextName) {
    const start = source.indexOf(`  function ${name}`);
    const end = source.indexOf(`  function ${nextName}`, start);
    assert.notEqual(start, -1, `${name} exists`);
    assert.notEqual(end, -1, `${nextName} exists after ${name}`);
    return source.slice(start, end);
}

const normalizeSource = functionSource('normalizeActivity', 'activityQueryArtifactFile');
const normalizeActivity = new Function(
    'queryNumber',
    'activityMilestoneType',
    `${normalizeSource}; return normalizeActivity;`,
)(
    (item, index) => Number(String(item.query_id || '').match(/(\d+)$/)?.[1] || index + 1),
    () => null,
);

test('normalization adds trusted Input identity without replacing the concise summary or sub-agent calls', () => {
    const inputArtifact = {path:'query_03/query_input_03.md',artifact_role:'query_input',query_number:3};
    const call = { id: 'call-1', sequence: 1, task_summary: 'Existing call' };
    const [query] = normalizeActivity({
        round_status: 'running',
        discovery_activity: [{
            kind: 'query',
            query_id: 'query_003',
            status: 'running',
            query_summary: 'Concise scan-friendly summary.',
            query_input: inputArtifact,
            query_report: null,
            subagent_calls: [call],
        }],
    });

    assert.equal(query.summary, 'Concise scan-friendly summary.');
    assert.deepEqual(query.input, inputArtifact);
    assert.notEqual(query.input, query.summary);
    assert.deepEqual(query.details, [call]);
});

test('activity details render Input then Output then the unchanged sub-agent block', () => {
    const render = functionSource('renderActivityQuery', 'renderActivityQueryStream');
    const input = render.indexOf("'Input'");
    const output = render.indexOf("'Output'");
    const calls = render.indexOf("'Sub-agent call'");
    assert.ok(input >= 0 && input < output && output < calls);
    assert.match(render, /item\.details\.forEach\(detail=>/);
    assert.match(render, /detail\.sequence\?detail\.sequence\+'\. '/);
    assert.match(render, /detail\.agent_role/);
    assert.match(render, /detail\.task_summary/);
    assert.match(render, /Query input ↗/);
    assert.doesNotMatch(render, /rw-activity-query-input|node\(item\.input\?'pre'/);
});

test('query input and report opening use the trusted tree and focus existing viewer tabs', () => {
    const resolver = functionSource('activityQueryArtifactFile', 'activityDataWithRoundFallback');
    const render = functionSource('renderActivityQuery', 'renderActivityQueryStream');
    assert.match(resolver, /findTreeFile\(tree,descriptor\.path\)/);
    assert.match(resolver, /artifact\.visibility!=='research'/);
    assert.match(resolver, /artifact\.artifact_role!==role/);
    assert.match(resolver, /artifact\.query_number!==item\.number/);
    assert.match(render, /openFile\(report,false,'center',false,true\)/);
    assert.match(render, /openFile\(input,false,'center',false,true\)/);
    assert.match(render, /Query input ↗/);
    assert.match(render, /Query report ↗/);
    assert.match(render, /Open Query '\+item\.number\+' report in file viewer/);
});

test('pending, running, failed, and interrupted states never get a completed report label', () => {
    const render = functionSource('renderActivityQuery', 'renderActivityQueryStream');
    const resolver = functionSource('activityQueryArtifactFile', 'activityDataWithRoundFallback');
    assert.match(render, /Query report will be available after completion\./);
    assert.match(render, /No completed query report is available\./);
    assert.match(render, /\['failed','interrupted'\]/);
    assert.doesNotMatch(resolver, /item\.status!=='completed'/);
});

test('viewer snapshot distinguishes selected query input, report, and execution log artifacts', () => {
    const snapshot = source.slice(
        source.indexOf('  function buildViewerContextSnapshot'),
        source.indexOf('  function visiblyDisplayed'),
    );
    assert.match(snapshot, /\['query_input','query_report','execution_log'\]\.includes\(file\.artifactRole\)/);
    assert.match(snapshot, /artifactRole:queryArtifact\?file\.artifactRole:null/);
    assert.match(snapshot, /queryNumber:queryArtifact\?file\.queryNumber:null/);
    assert.match(snapshot, /reportKind/);
    assert.match(snapshot, /reportView/);
});

test('rendering activity details has no network, model, or persistence path', () => {
    const render = functionSource('renderActivityQuery', 'renderActivityQueryStream');
    assert.doesNotMatch(render, /fetch\(|api\(|XMLHttpRequest|localStorage|write|model/i);
    assert.match(render, /textContent|rw-activity-artifact-link/);
});

test('paired query folders only expand while their files use normal round artifact actions', () => {
    const tree = functionSource('renderTreeNode', 'renderCodeTreeNode');
    const workspace = functionSource('workspaceFile', 'safeArtifactIdentityPath');
    const controls = functionSource('artifactViewControls', 'renderPanes');
    assert.match(tree, /item\.type === 'dir'/);
    assert.match(tree, /state\.expanded\.add\(key\)/);
    assert.match(tree, /else \{[\s\S]*openFile\(file, false\)/);
    assert.match(workspace, /\['query_input','query_report','execution_log'\]\.includes\(item\.artifact_role\)/);
    assert.match(controls, /const code=file&&file\.sourceKind==='project-code'/);
    assert.match(controls, /if\(code\)controls\.append\(codePathControl\(file\)\)/);
});
