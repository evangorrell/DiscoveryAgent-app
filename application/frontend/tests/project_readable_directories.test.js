const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(ROOT, 'da_selection_ui/static/projects.js'), 'utf8');
const template = fs.readFileSync(path.join(ROOT, 'da_selection_ui/templates/projects.html'), 'utf8');

function sourceBlock(start, end) {
    const startIndex = client.indexOf(start);
    assert.notEqual(startIndex, -1, `missing ${start}`);
    const endIndex = client.indexOf(end, startIndex);
    assert.notEqual(endIndex, -1, `missing ${end}`);
    return client.slice(startIndex, endIndex);
}

function locationHelpers(overrides = {}) {
    const context = {
        modalMode: overrides.modalMode || 'create',
        editingProjectId: overrides.editingProjectId || null,
        currentProjectRoot: overrides.currentProjectRoot || '',
        currentProjectParent: overrides.currentProjectParent || '',
        editingProjectImportedInPlace: overrides.editingProjectImportedInPlace === true,
        els: { name: { value: overrides.name || '' } },
    };
    vm.runInNewContext(
        sourceBlock('const PROJECT_DIRECTORY_SLUG_MAX_LENGTH', 'function renderProjectLocation()') +
        '\nthis.api = { projectDirectorySlug, projectDirectoryName, projectDestination };',
        context
    );
    return context.api;
}

test('Project Location preview uses only the exact readable slug', () => {
    const api = locationHelpers({ name: 'Multiregion Alzheimers Disease' });
    assert.equal(
        api.projectDirectoryName('Multiregion Alzheimers Disease'),
        'multiregion-alzheimers-disease'
    );
    assert.equal(
        api.projectDestination('/Users/scientist/DiscoveryAgent/projects'),
        '/Users/scientist/DiscoveryAgent/projects/multiregion-alzheimers-disease'
    );
});

test('browser preview mirrors unsafe, Unicode, empty, and bounded slug behavior', () => {
    const api = locationHelpers();
    assert.equal(api.projectDirectorySlug(' Optic_Nerve / Regeneration '), 'optic-nerve-regeneration');
    assert.equal(api.projectDirectorySlug('Café 東京'), 'café-東京');
    assert.equal(api.projectDirectorySlug('../___💥'), '');
    assert.equal(Array.from(api.projectDirectorySlug('界'.repeat(100))).length, 48);
});

test('managed rename previews its new readable leaf while imported rename preserves its external root', () => {
    const api = locationHelpers({
        modalMode: 'edit',
        editingProjectId: 'prj_' + 'a'.repeat(32),
        currentProjectRoot: '/old/prj_' + 'a'.repeat(32),
        currentProjectParent: '/old',
        name: 'Renamed Project',
    });
    assert.equal(
        api.projectDestination('/old', 'Renamed Project'),
        '/old/renamed-project'
    );
    assert.equal(
        api.projectDestination('/new', 'Renamed Project'),
        '/new/renamed-project'
    );

    const imported = locationHelpers({
        modalMode: 'edit',
        editingProjectId: 'legacy-external-project',
        currentProjectRoot: '/external/legacy-folder',
        currentProjectParent: '/external',
        editingProjectImportedInPlace: true,
        name: 'Renamed External Project',
    });
    assert.equal(
        imported.projectDestination('/external', 'Renamed External Project'),
        '/external/legacy-folder'
    );
});

test('Edit Use Default preview is independent of Create pending identity', () => {
    const api = locationHelpers({
        modalMode: 'edit',
        editingProjectId: 'legacy-optic-project',
        currentProjectRoot: '/custom/location/legacy-optic-project',
        currentProjectParent: '/custom/location',
        name: 'Optic Nerve Regeneration',
    });
    assert.equal(
        api.projectDestination('/Users/scientist/DiscoveryAgent/projects'),
        '/Users/scientist/DiscoveryAgent/projects/optic-nerve-regeneration'
    );

    const wiring = sourceBlock('function wire()', "document.addEventListener('DOMContentLoaded'");
    const useDefault = wiring.slice(
        wiring.indexOf("els.useDefaultLocation.addEventListener('click'"),
        wiring.indexOf("els.selectData.addEventListener('click'")
    );
    assert.match(useDefault, /pendingProjectParent = els\.form\.dataset\.defaultProjectParent/);
    assert.match(useDefault, /pendingCanonicalDefaultLocation = modalMode === 'edit'/);
    assert.match(useDefault, /renderProjectLocation\(\)/);
    assert.doesNotMatch(useDefault, /fetch\(|pendingProjectId|project_id/);
});

test('Create keeps pending identity at submit while Edit destination helpers do not consume it', () => {
    const submission = sourceBlock('async function submitCurrentProject()', 'async function submitForm');
    const destination = sourceBlock('function projectDirectoryName', 'function renderProjectLocation()');
    assert.match(submission, /if \(modalMode === 'create'\) body\.append\('project_id', els\.form\.dataset\.pendingProjectId\)/);
    assert.match(submission, /modalMode === 'edit' && pendingCanonicalDefaultLocation/);
    assert.match(submission, /body\.append\('use_canonical_default_location', 'true'\)/);
    assert.doesNotMatch(destination, /pendingProjectId|_validate|Close and reopen Create Project/);
});

test('Create and Edit openings reset only temporary default-location intent', () => {
    const create = sourceBlock('function openCreate()', 'async function openEdit');
    const edit = sourceBlock('async function openEdit(project)', 'function openDeleteConfirmation');
    assert.match(create, /modalMode = 'create'; editingProjectId = null/);
    assert.match(create, /pendingCanonicalDefaultLocation = false/);
    assert.match(edit, /modalMode = 'edit'; editingProjectId = project\.id/);
    assert.ok((edit.match(/pendingCanonicalDefaultLocation = false/g) || []).length >= 2);
});

test('name input refreshes only the pending preview and project creation remains outer-submit owned', () => {
    const wiring = sourceBlock('function wire()', "document.addEventListener('DOMContentLoaded'");
    const submission = sourceBlock('async function submitCurrentProject()', 'async function submitForm');
    assert.match(wiring, /els\.name\.addEventListener\('input',[\s\S]*renderProjectLocation\(\)/);
    assert.doesNotMatch(wiring, /els\.name\.addEventListener\('input',[\s\S]{0,160}fetch\(/);
    assert.match(submission, /body\.append\('project_name', name\)/);
    assert.match(submission, /return await submitPreparedProject\(body\)/);
});

test('duplicate-name response uses inline V2 error and restores Project Name focus', () => {
    const errorHelper = sourceBlock('function showProjectNameValidationError', 'const PROJECT_DIRECTORY_SLUG_MAX_LENGTH');
    const locationError = sourceBlock('function showProjectLocationValidationError', 'const PROJECT_DIRECTORY_SLUG_MAX_LENGTH');
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    assert.match(errorHelper, /els\.name\.classList\.add\('v2-input-invalid'\)/);
    assert.match(errorHelper, /setFormError\(message/);
    assert.match(errorHelper, /els\.name\.focus\(\{ preventScroll: true \}\)/);
    assert.match(persist, /'duplicate_project_name', 'invalid_project_name'/);
    assert.match(persist, /showProjectNameValidationError\(message\)/);
    assert.match(persist, /restoreFocus: !projectNameError/);
    assert.match(persist, /'duplicate_project_slug', 'invalid_project_location'/);
    assert.match(persist, /showProjectLocationValidationError\(message\)/);
    assert.match(locationError, /els\.chooseLocation\.focus\(\{ preventScroll: true \}\)/);
});

test('slug-only preview ships under a fresh ordinary-reload cache key', () => {
    assert.match(template, /projects\.js'\) \}\}\?v=20261001-preserve-data-1/);
});
