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
    assert.match(workspace, /<h3>Context<\/h3>/);
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
    assert.match(client, /event\.key === 'Escape'/);
    assert.match(client, /trigger\.focus\(\)/);
    assert.match(css, /\.rw-project-configuration-modal \{[^}]*display: flex;[^}]*flex-direction: column;[^}]*overflow: hidden;/);
    assert.match(css, /\.rw-project-configuration-body \{[^}]*flex: 1 1 auto;[^}]*min-height: 0;/);
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
