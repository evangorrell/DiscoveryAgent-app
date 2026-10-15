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

const titleHelpers = functionSource('canonicalRoundStatus', 'queryNumber');
const roundStatusTitle = new Function(`${titleHelpers}; return roundStatusTitle;`)();

test('selected-round titles preserve canonical, distinct statuses', () => {
    assert.equal(roundStatusTitle('completed', 0), 'Round 0 complete');
    assert.equal(roundStatusTitle('interrupted', 1), 'Round 1 interrupted');
    assert.equal(roundStatusTitle('running', 2), 'Round 2 in progress');
    assert.equal(roundStatusTitle('stopped', 3), 'Round 3 stopped');
    assert.equal(roundStatusTitle('failed', 4), 'Round 4 failed');
    assert.equal(roundStatusTitle('unexpected', 7), 'Round 7 status unavailable');
});

test('round selection immediately clears old activity and renders the chosen round', () => {
    const selection = functionSource('setActiveRound', 'reportFile');
    assert.ok(selection.indexOf('state.activityRequestToken+=1') < selection.indexOf('renderActivitySelection(round)'));
    assert.ok(selection.indexOf('renderActivitySelection(round)') < selection.indexOf('refreshActivity()'));
    assert.match(selection, /state\.activeRound = round/);
    const immediate = source.slice(source.indexOf('  function renderActivitySelection'), source.indexOf('  async function refreshActivity'));
    assert.match(immediate, /round_index:requestedRound/);
});

test('status content, report action, and query progress remain selected-round-specific', () => {
    const report = functionSource('activityRoundReportFile', 'openReviewRoundReport');
    const fallback = functionSource('activityDataWithRoundFallback', 'activeActivityQuery');
    const render = functionSource('renderActivity', 'renderActivitySelection');
    assert.match(report, /roundReportFile\(roundIndex\)/);
    assert.match(report, /round:roundIndex/);
    assert.match(fallback, /state\.rounds\.find\(item=>item\.round_index===requestedRound\)/);
    assert.match(render, /renderActivityQueryStream\(items,requestedRound/);
    assert.match(render, /renderActivitySummary\(compatibleData,items,requestedRound\)/);
});

test('stopped content stays distinct and stale activity responses are rejected', () => {
    const finalizer = functionSource('finalizeActivitySummary', 'renderActivity');
    const refresh = source.slice(source.indexOf('  async function refreshActivity'), source.indexOf('  function applyPanelTab'));
    assert.match(finalizer, /roundStatus==='stopped'/);
    assert.match(finalizer, /The round was stopped\./);
    assert.match(refresh, /state\.activeRound!==requestedRound\|\|requestToken!==state\.activityRequestToken/);
    assert.match(refresh, /runResponseMatches\(data,requestedRound\)/);
    assert.match(refresh, /acceptRunStatus\(data\)/);
    assert.match(source, /renderActivityQueryStream\.lastRound===state\.activeRound/);
});

test('terminal failure cannot render running controls or waiting copy', () => {
    const controls = functionSource('appendRunControl', 'renderActivitySummary');
    const render = functionSource('renderActivitySummary', 'finalizeActivitySummary');
    assert.match(render, /else if\(roundStatus==='failed'\)/);
    assert.match(render, /A native checkpoint is available\. You can resume this round\./);
    assert.match(render, /else if\(\['running','selected','exploring','pending'\]\.includes\(roundStatus\)\)/);
    assert.match(render, /Waiting for the next research query/);
    assert.match(controls, /if\(roundStatus==='running'\)/);
    assert.match(controls, /else if\(\['interrupted','stopped','failed'\]\.includes\(roundStatus\)\)/);
    assert.match(controls, /if\(data\.resumable\)/);
});


test('project and run identity reject cross-project and stale terminal responses', () => {
    const start = source.indexOf('  function runResponseMatches');
    const end = source.indexOf('  async function refreshActivity', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const helpers = source.slice(start, end);
    const evaluate = new Function(`
        const CFG = { projectId: 'project-a' };
        const state = { runStatuses: new Map() };
        ${titleHelpers}
        ${helpers}
        return { runResponseMatches, acceptRunStatus, state };
    `);
    const { runResponseMatches, acceptRunStatus, state } = evaluate();
    const runA = 'a'.repeat(32);
    const runB = 'b'.repeat(32);

    assert.equal(runResponseMatches({ project_id: 'project-b', round: 0, run_id: runB }, 0), false);
    assert.equal(runResponseMatches({ project_id: 'project-a', round: 1, run_id: runA }, 0), false);
    assert.equal(runResponseMatches({ project_id: 'project-a', round: 0, run_id: runA }, 0), true);

    assert.equal(acceptRunStatus({ run_id: runA, status: 'interrupted' }), true);
    assert.equal(acceptRunStatus({ run_id: runA, status: 'running' }), false);
    assert.equal(state.runStatuses.get(runA), 'interrupted');
    assert.equal(acceptRunStatus({ run_id: runB, status: 'running' }), true);
    assert.equal(state.runStatuses.get(runB), 'in progress');
});

test('completed run resists a late running poll for the same run', () => {
    const helpers = source.slice(
        source.indexOf('  function runResponseMatches'),
        source.indexOf('  async function refreshActivity'),
    );
    const { acceptRunStatus, state } = new Function(`
        const CFG = { projectId: 'project-a' };
        const state = { runStatuses: new Map() };
        ${titleHelpers}
        ${helpers}
        return { acceptRunStatus, state };
    `)();
    const runId = 'c'.repeat(32);
    assert.equal(acceptRunStatus({ run_id: runId, status: 'completed' }), true);
    assert.equal(state.runStatuses.get(runId), 'complete');
    assert.equal(acceptRunStatus({ run_id: runId, status: 'running' }), false);
    assert.equal(acceptRunStatus({ run_id: runId, status: 'completed' }), true);
});

test('Activity is round-local and excludes successor lifecycle presentation', () => {
    const render = functionSource('renderActivity', 'renderActivitySelection');
    assert.match(render, /replaceChildren\(summary,renderActivityQueryStream/);
    for (const forbidden of [
        'renderProjectLifecycle', 'projectLifecycle', 'project_lifecycle',
        'Develop the hypothesis', 'Ready for investigation', 'Setup required',
    ]) {
        assert.doesNotMatch(source, new RegExp(forbidden));
    }
    const milestones = functionSource('activityMilestoneType', 'normalizeActivity');
    for (const eventType of [
        'round_started', 'round_resumed', 'report_writing_started',
        'round_report_saved', 'memory_report_saved', 'workflow_completed', 'round_error',
    ]) {
        assert.match(milestones, new RegExp(eventType));
    }
    assert.match(source, /roundStatus==='completed'/);
});
