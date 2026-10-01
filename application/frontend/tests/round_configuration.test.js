const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../da_selection_ui');
const workspace = fs.readFileSync(path.join(root, 'templates/round_workspace.html'), 'utf8');
const base = fs.readFileSync(path.join(root, 'templates/base.html'), 'utf8');
const launch = fs.readFileSync(path.join(root, 'templates/_round_launch_dialog.html'), 'utf8');
const client = fs.readFileSync(path.join(root, 'static/round_configuration.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/round_workspace.css'), 'utf8');

test('Round Workspace exposes one explicit read-only project configuration viewer', () => {
    assert.match(workspace, /id="rw-project-configuration"[^>]*>[\s\S]*Configuration/);
    assert.match(workspace, /id="rw-project-configuration-overlay"/);
    assert.match(workspace, /Research objective/);
    assert.match(workspace, /Project defaults/);
    assert.match(workspace, /<h3>Data<\/h3>/);
    assert.doesNotMatch(workspace, /<h3>Context<\/h3>|rw-project-configuration-context/);
    assert.match(workspace, /Project README/);
    assert.match(workspace, /<pre[^>]*id="rw-project-configuration-readme"/);
    assert.match(workspace, /current read-only configuration/);

    const modal = workspace.slice(
        workspace.indexOf('id="rw-project-configuration-overlay"'),
        workspace.lastIndexOf('{% if pre_round_overlay %}'),
    );
    assert.doesNotMatch(modal, /<input|<textarea|<select|contenteditable|type="submit"/);
    assert.match(modal, /id="rw-project-configuration-close"/);
    assert.match(modal, /aria-label="Close project configuration"/);
    assert.doesNotMatch(modal, /v2-section-label|v2-modal-footer|RE#ADME/);
    assert.match(modal, />Project Configuration<\/h2>/);
    assert.deepEqual([...modal.matchAll(/<h3>([^<]+)<\/h3>/g)].map(match => match[1]),
        ['Research objective', 'Project defaults', 'Data', 'Project README']);
    assert.match(client, /event\.key === 'Escape'/);
    assert.match(client, /trigger\.focus\(\)/);
    assert.match(css, /\.rw-project-configuration-modal \{[^}]*display: flex;[^}]*flex-direction: column;[^}]*overflow: hidden;/);
    assert.match(css, /\.rw-project-configuration-body \{[^}]*flex: 1 1 auto;[^}]*min-height: 0;/);
});

test('configuration header is centered and README retains an accessible contained scroll panel', () => {
    assert.match(css, /\.rw-project-configuration-modal \.v2-modal-title \{[^}]*text-align: center;/);
    assert.match(css, /\.rw-project-configuration-modal \.v2-modal-close \{[^}]*position: absolute;[^}]*right: 16px;/);
    assert.match(workspace, /id="rw-project-configuration-readme"\s+tabindex="0" role="region" aria-label="Project README, read only"/);
    assert.match(css, /\.rw-project-configuration-readme \{[^}]*max-height: min\(280px, 34vh\);/);
    assert.match(css, /\.rw-project-configuration-readme:focus-visible/);
    assert.match(client, /querySelectorAll\('button, \[tabindex="0"\]'\)/);
    assert.match(workspace, /<span>Read only<\/span>/);
});

test('focus cycle includes the README only when content is visible, including loading and error states', () => {
    let contentHidden = false;
    let keydown;
    let dismissed = false;
    let prevented = false;
    const document = {
        activeElement: null,
        addEventListener(type, handler) { assert.equal(type, 'keydown'); keydown = handler; },
    };
    const control = hidden => ({
        closest() { return hidden() ? {} : null; },
        focus() { document.activeElement = this; },
    });
    const close = control(() => false), body = control(() => false), readme = control(() => contentHidden);
    const overlay = { hidden: false, querySelectorAll: () => [close, body, readme] };
    const source = client.slice(client.indexOf("    document.addEventListener('keydown'"), client.lastIndexOf('\n})();'));
    new Function('document', 'overlay', 'closeConfiguration', source)(document, overlay, () => { dismissed = true; });
    const tab = shiftKey => keydown({ key: 'Tab', shiftKey, preventDefault() { prevented = true; } });
    document.activeElement = readme;
    tab(false);
    assert.equal(document.activeElement, close);
    assert.equal(prevented, true);
    tab(true);
    assert.equal(document.activeElement, readme);
    contentHidden = true; // Both loading and error states hide the content block.
    document.activeElement = body;
    tab(false);
    assert.equal(document.activeElement, close);
    tab(true);
    assert.equal(document.activeElement, body);
    keydown({ key: 'Escape' });
    assert.equal(dismissed, true);
});

function renderHarness() {
    function element(tag) {
        return { tag, textContent: '', children: [],
            append(...children) { this.children.push(...children); },
            appendChild(child) { this.children.push(child); },
            replaceChildren() { this.children = []; },
        };
    }
    const objective = element('p'), settings = element('dl');
    const dataList = element('ul'), readme = element('pre');
    const document = {
        createElement: element,
        getElementById() { throw new Error('The removed Context element must not be read.'); },
    };
    const rendering = client.slice(client.indexOf('    function setting('), client.indexOf('    async function loadConfiguration('));
    const render = new Function('document', 'objective', 'settings', 'dataList', 'readme',
        rendering + '\nreturn renderProject;')(document, objective, settings, dataList, readme);
    return { render, objective, settings, dataList, readme };
}

test('rendering preserves all default values, objective, data scope and full README without mutating context', () => {
    const h = renderHarness();
    const narrative = '# Scientific context\n\n' + 'Long narrative remains available in README. '.repeat(100);
    const project = {
        task_description: 'Identify mechanisms of neuronal regeneration.',
        dataset_context: narrative,
        reasoning_model: 'claude-opus-4-6', execution_model: 'claude-sonnet-4-6',
        query_count: 12, deep_research: true, web_search: false,
        data_sources: { sources: [{ display_name: 'optic-nerve-regeneration-data', selected_paths: ['.'] }] },
    };
    const before = JSON.stringify(project);
    h.render(project, { success: true, content: narrative });
    assert.equal(h.objective.textContent, project.task_description);
    assert.deepEqual(h.settings.children.map(child => child.textContent), [
        'Reasoning model', 'claude-opus-4-6', 'Execution model', 'claude-sonnet-4-6',
        'Queries for Round 1+', '12', 'Deep Research', 'Enabled', 'Web Search', 'Disabled',
    ]);
    assert.deepEqual(h.dataList.children[0].children.map(child => child.textContent),
        ['optic-nerve-regeneration-data', 'Entire source']);
    assert.equal(h.readme.textContent, narrative);
    assert.equal(JSON.stringify(project), before);
});

test('missing README and empty project information retain existing fallbacks', () => {
    const h = renderHarness();
    h.render({ query_count: 1, deep_research: false, web_search: false },
        { success: false, error: 'File not found' });
    assert.equal(h.objective.textContent, 'No research objective saved.');
    assert.equal(h.dataList.children[0].textContent, 'No shared data sources selected.');
    assert.equal(h.readme.textContent, 'README unavailable: File not found');
    assert.deepEqual(h.settings.children.map(child => child.textContent), [
        'Reasoning model', 'Unavailable', 'Execution model', 'Unavailable',
        'Queries for Round 1+', '1', 'Deep Research', 'Disabled', 'Web Search', 'Disabled',
    ]);
});

test('repeat rendering replaces prior settings and displays partial source paths as plain text', () => {
    const h = renderHarness();
    const project = { query_count: 50, data_sources: { sources: [
        { display_name: '<source>', selected_paths: ['counts.csv', 'metadata/cohorts.csv'] },
    ] } };
    h.render(project, { success: true, content: '<script>not executable</script>' });
    h.render(project, { success: true, content: '<script>still plain text</script>' });
    assert.equal(h.settings.children.length, 10);
    assert.equal(h.dataList.children.length, 1);
    assert.deepEqual(h.dataList.children[0].children.map(child => child.textContent),
        ['<source>', 'counts.csv, metadata/cohorts.csv']);
    assert.equal(h.readme.textContent, '<script>still plain text</script>');
});

test('configuration viewer reads existing project detail and README without mutation calls', () => {
    assert.match(client, /fetch\('\/api\/projects\/'[\s\S]*\?selection_only=1/);
    assert.match(client, /fetch\('\/api\/readme'/);
    assert.match(client, /readme\.textContent =/);
    assert.doesNotMatch(client, /method:\s*['"](?:POST|PUT|PATCH|DELETE)['"]/);
    assert.doesNotMatch(client, /innerHTML|contenteditable/);
    assert.match(workspace, /round_configuration\.js/);
});

test('shared new-round dialog contains only the supported editable settings', () => {
    assert.match(base, /include '_round_launch_dialog\.html'/);
    assert.doesNotMatch(launch, /NEW ROUND/);
    assert.equal((launch.match(/<input/g) || []).length, 3);
    assert.equal((launch.match(/<select/g) || []).length, 2);
    for (const id of [
        'round-launch-queries', 'round-launch-deep-research', 'round-launch-web-search',
        'round-launch-reasoning-model', 'round-launch-execution-model',
    ]) assert.match(launch, new RegExp(`id="${id}"`));
    assert.match(launch, /objective, README, data, skills, paths, and safety settings remain unchanged/);
    assert.doesNotMatch(launch, /name="(?:task_description|dataset_context|readme|data_sources|project_root)"/);
});

test('Round Launch polish keeps semantic checkboxes and their clickable labels', () => {
    for (const id of ['round-launch-deep-research', 'round-launch-web-search']) {
        assert.match(launch, new RegExp(`<label[^>]*for="${id}"[\\s\\S]*?<input type="checkbox" id="${id}">`));
    }
    assert.doesNotMatch(launch, /role="switch"|role="checkbox"|tabindex=/);
    const controls = fs.readFileSync(path.join(root, 'static/round_launch.css'), 'utf8');
    assert.match(base, /filename='round_launch\.css'/);
    assert.match(controls, /:checked \{[^}]*background: var\(--v2-accent\)/);
    assert.match(controls, /:checked::before \{ visibility: visible/);
    assert.match(controls, /:focus-visible \{[^}]*outline: 2px solid var\(--v2-accent\)/);
    assert.match(controls, /forced-colors: active/);
    assert.match(controls, /appearance: auto/);
});

test('compact Round Launch sizing is scoped and retains flexible model columns', () => {
    const controls = fs.readFileSync(path.join(root, 'static/round_launch.css'), 'utf8');
    assert.match(controls, /\.v2-round-launch-modal #round-launch-queries \{ width: 160px; max-width: 100%/);
    assert.match(controls, /\.v2-round-launch-modal \.v2-input \{[^}]*min-height: 42px/);
    assert.match(controls, /\.v2-round-launch-modal \.v2-select \{ padding-right: 40px/);
    assert.doesNotMatch(controls, /\.v2-modal\s|\.v2-modal-body\s|\.v2-modal-footer\s|\.v2-btn\s/);
});
