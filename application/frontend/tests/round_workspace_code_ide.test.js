const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const workspaceSource = fs.readFileSync(
    path.join(root, 'da_selection_ui', 'static', 'round_workspace.js'), 'utf8',
);
const workspaceCss = fs.readFileSync(
    path.join(root, 'da_selection_ui', 'static', 'round_workspace.css'), 'utf8',
);
const projectHomeSource = fs.readFileSync(
    path.join(root, 'da_selection_ui', 'static', 'project_home.js'), 'utf8',
);

function sourceBetween(start, end) {
    const from = workspaceSource.indexOf(start);
    const to = workspaceSource.indexOf(end, from);
    assert.notEqual(from, -1, `missing ${start}`);
    assert.notEqual(to, -1, `missing ${end}`);
    return workspaceSource.slice(from, to);
}

test('code identities preserve nested relative breadcrumbs', () => {
    const functionSource = sourceBetween('  function workspaceFile', '  function safeArtifactIdentityPath');
    const workspaceFile = new Function(`${functionSource}; return workspaceFile;`)();
    const owner = { sourceScope: 'code', label: 'Code' };
    const directory = workspaceFile({ name: 'analysis', path: 'analysis', type: 'dir' }, owner);
    const file = workspaceFile({ name: 'pipeline.py', path: 'analysis/pipeline.py', type: 'file' }, owner);

    assert.equal(directory.path, 'code/analysis');
    assert.equal(file.path, 'code/analysis/pipeline.py');
    assert.equal(file.artifactPath, 'analysis/pipeline.py');
    assert.equal(directory.isDirectory, true);
    assert.equal(file.isDirectory, false);
});

test('root and nested code directories toggle only and never open viewer tabs', () => {
    const nested = sourceBetween('  function renderCodeTreeNode', '  function renderExplorer');
    const explorer = sourceBetween('  function renderExplorer', '  async function loadExplorer');
    const opener = sourceBetween('  function openFile', '  function detachDraggedTab');

    assert.match(nested, /if\(item\.type==='dir'\).*select\.setAttribute\('aria-expanded'/s);
    assert.match(nested, /select\.addEventListener\('click',\(\)=>\{open\?state\.expanded\.delete\(key\):state\.expanded\.add\(key\);renderExplorer\(\);\}\)/);
    assert.match(nested, /else\{select\.setAttribute\('aria-label','Open '\+item\.name\);select\.addEventListener\('click',\(\)=>openFile/);
    assert.doesNotMatch(explorer, /openFile\(workspaceFile\(state\.codeTree/);
    assert.match(explorer, /title\.setAttribute\('aria-expanded',String\(open\)\)/);
    assert.match(explorer, /title\.addEventListener\('click',toggle\)/);
    assert.match(opener, /sourceKind==='project-code'&&file\.isDirectory===true\)return/);
});

test('restored code-directory tabs are rejected or pruned without touching file tabs', () => {
    const restore = sourceBetween('  function validViewerFile', '  function normalizeLegacyRoundTree');
    assert.match(restore, /file\.sourceKind==='project-code'&&file\.isDirectory===true/);
    assert.match(restore, /if\(isCode&&item&&item\.type==='dir'\)\{changed=true;return;\}/);
    assert.match(restore, /pane\.tabs=tabs/);
    assert.match(restore, /compactEmptyPanes\(\)/);
});

test('project-code files use the contained artifact route and normal renderer', () => {
    const render = sourceBetween('  async function renderFileBody', '  function applyPaneRatio');
    const imageRender = sourceBetween('  function renderStandaloneImage', '  async function renderFileBody');
    assert.match(workspaceSource, /\/api\/round_workspace_code_artifact\?path=/);
    assert.match(render, /renderStandaloneImage\(body,file\)/);
    assert.match(imageRender, /img\.src=artifactUrl\(file\)/);
    assert.match(render, /frame\.src = artifactUrl\(file\)/);
    assert.match(render, /trustedMarkdownPayload=markdownFile&&file\.sourceKind!=='project-code'/);
    assert.match(render, /fetch\(trustedMarkdownPayload\?markdownDisplayUrl\(file\):artifactUrl\(file\)/);
    assert.match(render, /pre\.textContent = text/);
    assert.doesNotMatch(render, /sourceKind==='project-code'.*return/s);
});

test('code Copy precedes Download and uses the exact canonical file path', () => {
    const action = sourceBetween('  function codePathControl', '  function downloadControl');
    const controls = sourceBetween('  function artifactViewControls', '  function renderPanes');
    const icon = action.indexOf('rw-code-path-copy-icon');
    const label = action.indexOf('<span>Copy Absolute Path</span>');
    assert.ok(icon >= 0 && icon < label, 'copy icon must precede the visible label in the DOM');
    assert.match(action, /class="rw-report-download-icon rw-code-path-copy-icon"/);
    assert.match(action, /width="16" height="16" aria-hidden="true" focusable="false"/);
    assert.match(action, /stroke="currentColor" stroke-width="1\.4"/);
    assert.match(action, /setAttribute\('aria-label','Copy Absolute Path'\)/);
    assert.match(action, /status\.setAttribute\('role','status'\)/);
    assert.match(action, /status\.setAttribute\('aria-live','polite'\)/);
    assert.match(action, /copyCodePath\(payload\.absolute_path,status\)/);
    assert.doesNotMatch(action, /payload\.code_root|payload\.relative_path/);
    assert.ok(controls.indexOf('if(code)controls.append(codePathControl(file))') < controls.indexOf('controls.append(downloadControl(file,current))'));
    assert.match(controls, /const downloadable=isDownloadableMarkdown\(file\)\|\|code\|\|figure/);
    assert.match(controls, /const code=file&&file\.sourceKind==='project-code'&&file\.isDirectory!==true/);
    assert.doesNotMatch(controls, /if\((?:report|figure)\)controls\.append\(codePathControl/);
});

test('copy feedback says Copied. transiently and leaves no visible absolute path', async () => {
    const helpers = sourceBetween('  async function copyCodePath', '  async function renderFileBody');
    let copied = null;
    let clearFeedback = null;
    const { copyCodePath } = new Function(
        'window', 'document', 'navigator',
        `${helpers}; return {copyCodePath};`,
    )(
        { setTimeout(callback) { clearFeedback = callback; } },
        {},
        { clipboard: { writeText: async (value) => { copied = value; } } },
    );
    const status = { textContent: '' };
    await copyCodePath('/absolute/project/code/analysis/pipeline.py', status);
    assert.equal(copied, '/absolute/project/code/analysis/pipeline.py');
    assert.equal(status.textContent, 'Copied.');
    assert.equal(typeof clearFeedback, 'function');
    clearFeedback();
    assert.equal(status.textContent, '');
});

test('copy failure remains accessible and leaves no visible path', async () => {
    const helpers = sourceBetween('  async function copyCodePath', '  async function renderFileBody');
    let removed = false;
    const field = {
        style: {},
        setAttribute() {},
        select() {},
        remove() { removed = true; },
    };
    const { copyCodePath } = new Function(
        'window', 'document', 'navigator',
        `${helpers}; return {copyCodePath};`,
    )(
        { setTimeout() {} },
        {
            body: { append() {} },
            createElement() { return field; },
            execCommand() { return false; },
        },
        { clipboard: { writeText: async () => { throw new Error('denied'); } } },
    );
    const status = { textContent: '' };
    const copied = await copyCodePath('/absolute/project/code/analysis/pipeline.py', status);
    assert.equal(copied, false);
    assert.equal(status.textContent, 'Copy unavailable.');
    assert.equal(removed, true);
});

test('all opened figures and PDFs receive direct artifact downloads', () => {
    const controls = sourceBetween('  function downloadControl', '  function renderPanes');
    assert.match(controls, /isFigureFile\(file\.name\)\|\|fileExt\(file\.name\)==='\.pdf'/);
    assert.match(controls, /direct\?withDownload\(artifactUrl\(file\)\)/);
    assert.match(workspaceSource, /const withDownload = \(url\) => url \+ \(url\.includes\('\?'\)\?'&':'\?'\) \+ 'download=1'/);
    assert.match(controls, /const code=file&&file\.sourceKind==='project-code'/);
    assert.match(controls, /if\(code\)controls\.append\(codePathControl\(file\)\)/);
    assert.doesNotMatch(controls, /if\(figure\)controls\.append\(codePathControl/);
});

test('breadcrumb and right-side actions wrap cleanly', () => {
    const render = sourceBetween('  function renderPanes', '  function openFile');
    assert.match(render, /breadcrumb\.append\(breadcrumbPath\);const viewControls=artifactViewControls\(file\);if\(viewControls\)breadcrumb\.append\(viewControls\)/);
    assert.match(workspaceCss, /\.rw-report-controls\{[^}]*margin-left:auto/);
    assert.match(workspaceCss, /@container \(max-width:520px\)\{\.rw-pane-breadcrumb\{[^}]*flex-wrap:wrap/);
    assert.match(workspaceCss, /\.rw-report-download\{display:inline-flex;align-items:center;gap:6px/);
    assert.match(workspaceCss, /\.rw-report-download:hover,\.rw-report-download:focus-visible/);
});

test('retired location viewer language and Project Home Data Folder presentation are absent', () => {
    for (const forbidden of ['Open in your IDE', 'Project code workspace', 'Selected location', 'code-location']) {
        assert.equal(workspaceSource.includes(forbidden), false);
        assert.equal(workspaceCss.includes(forbidden), false);
    }
    assert.equal(projectHomeSource.includes('buildDataFolder'), false);
    assert.equal(projectHomeSource.includes('Data Folder'), false);
    assert.equal(projectHomeSource.includes('Open in Finder'), false);
    assert.equal(projectHomeSource.includes('Copy path'), false);
});
