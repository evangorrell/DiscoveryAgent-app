const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'round_workspace.js'),
    'utf8',
);
const css = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'round_workspace.css'),
    'utf8',
);

function between(startName, endName) {
    const start = source.indexOf(`function ${startName}`);
    const end = source.indexOf(`function ${endName}`, start + 1);
    assert.notEqual(start, -1, `${startName} exists`);
    assert.notEqual(end, -1, `${endName} exists`);
    return source.slice(start, end);
}

test('execution logs use cursor polling, bounded rendering, and text-only output', () => {
    const render = between('renderExecutionLogBody', 'renderFileBody');
    assert.match(source, /round_workspace_execution_log/);
    assert.match(source, /cursor='\+encodeURIComponent\(cursor\).*count=/);
    assert.match(render, /Number\(payload\.cursor\)!==cursor/);
    assert.match(render, /cursor=payload\.next_cursor/);
    assert.match(render, /retained\.slice\(-executionLogRetainedChars\)/);
    assert.match(render, /output\.textContent=retained/);
    assert.doesNotMatch(render, /innerHTML|insertAdjacentHTML/);
    assert.match(render, /if\(stopped\|\|inFlight\)return/);
    assert.match(render, /document\.hidden/);
    assert.match(render, /Math\.min\(8000/);
    assert.match(render, /terminalIdlePolls>=2/);
    assert.match(render, /schedule\(500\)/);
});

test('scrolling pauses auto-follow and Latest restores it', () => {
    const render = between('renderExecutionLogBody', 'renderFileBody');
    assert.match(render, /scrollHeight-viewport\.scrollTop-viewport\.clientHeight<=48/);
    assert.match(render, /latest\.hidden=nearBottom/);
    assert.match(render, /latest\.addEventListener\('click',followLatest\)/);
    assert.match(render, /const shouldFollow=nearBottom/);
});

test('polling is released on rerender and page unload', () => {
    const panes = between('renderPanes', 'openFile');
    assert.match(panes, /stopExecutionLogPolls\(\)/);
    assert.match(source, /beforeunload',stopExecutionLogPolls/);
    assert.match(source, /request\.abort\(\)/);
    assert.match(source, /executionLogPolls\.delete\(controller\)/);
});

test('Activity links only a validated Execution Agent artifact and deduplicates tabs', () => {
    const resolver = between('activityExecutionLogFile', 'activityDataWithRoundFallback');
    const render = between('renderActivityQuery', 'renderActivityQueryStream');
    assert.match(resolver, /descriptor\.artifact_role!=='execution_log'/);
    assert.match(resolver, /artifact\.visibility!=='research'/);
    assert.match(resolver, /artifact\.query_number!==item\.number/);
    assert.match(render, /Execution log/);
    assert.match(render, /detail\.execution_log_live\?' · Live'/);
    assert.match(render, /openFile\(logFile,false,'center',false,true\)/);
});

test('execution log gets Download but never Copy Absolute Path', () => {
    const controls = source.slice(source.indexOf('  function downloadControl'), source.indexOf('  function renderPanes'));
    assert.match(controls, /\|\|isExecutionLog\(file\)/);
    assert.match(controls, /const code=file&&file\.sourceKind==='project-code'/);
    assert.match(controls, /if\(code\)controls\.append\(codePathControl\(file\)\)/);
    assert.doesNotMatch(between('renderExecutionLogBody', 'renderFileBody'), /Copy Absolute Path/);
});

test('viewer styling is read-only, contained, and monospace', () => {
    assert.match(css, /\.rw-execution-log-body\{display:flex;overflow:hidden\}/);
    assert.match(css, /\.rw-execution-log-scroll\{[^}]*overflow:auto/);
    assert.match(css, /\.rw-execution-log-output\{[^}]*white-space:pre-wrap/);
    assert.match(css, /font:400 \.75rem\/1\.55 var\(--v2-mono,monospace\)/);
    assert.match(css, /\.rw-execution-log-latest:focus-visible/);
});
