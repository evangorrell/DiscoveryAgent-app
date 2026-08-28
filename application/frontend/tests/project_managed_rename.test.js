const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(root, 'da_selection_ui/static/projects.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'da_selection_ui/templates/projects.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'da_selection_ui/static/style.css'), 'utf8');

function sourceBlock(start, end) {
    const from = client.indexOf(start);
    const to = client.indexOf(end, from + start.length);
    assert.notEqual(from, -1, `missing ${start}`);
    assert.notEqual(to, -1, `missing ${end}`);
    return client.slice(from, to);
}

test('managed name edits preview a normalized folder while imported edits retain their root', () => {
    const destination = sourceBlock('function projectDestination(', 'function renderProjectLocation');
    const render = sourceBlock('function renderProjectLocation()', 'async function chooseProjectLocation');
    const edit = sourceBlock('async function openEdit(project)', 'function openDeleteConfirmation');

    assert.match(destination, /editingProjectImportedInPlace/);
    assert.match(destination, /return currentProjectRoot/);
    assert.match(render, /managedNameRename/);
    assert.match(render, /managed project folder will be renamed when you save changes/i);
    assert.match(edit, /editingProjectImportedInPlace = project\.imported_in_place === true/);
    assert.match(edit, /editingProjectImportedInPlace = data\.project\.imported_in_place === true/);
});

test('automatic managed rename saves directly while explicit relocation still confirms', () => {
    const submit = sourceBlock('async function submitPreparedProject(body)', 'async function submitCurrentProject()');

    assert.match(submit, /automaticManagedRename = relocating && !editingProjectImportedInPlace/);
    assert.match(submit, /if \(!automaticManagedRename\) \{\s*openRelocationConfirmation\(body, destination\);\s*return;/s);
    assert.match(submit, /return await persistProject\(body\)/);
});

test('running rename and server failures remain inline and focus-safe', () => {
    const submit = sourceBlock('async function submitPreparedProject(body)', 'async function submitCurrentProject()');
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const nameError = sourceBlock('function showProjectNameValidationError(message)', 'function showProjectLocationValidationError');

    assert.match(submit, /projectHasActiveRound[\s\S]*automaticManagedRename[\s\S]*showProjectNameValidationError\(message\)/);
    assert.match(persist, /setFormError\(message\)/);
    assert.match(persist, /resetSubmitState\(\)/);
    assert.match(nameError, /els\.name\.classList\.add\('v2-input-invalid'\)/);
    assert.match(nameError, /els\.name\.focus/);
    assert.match(template, /id="np-form-error"[^>]*role="alert"[^>]*hidden/);
    assert.match(css, /\.v2-modal-form-error\s*\{[^}]*color:\s*var\(--v2-danger/s);
});

test('successful edit closes the draft and refreshes both displayed name and location', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const editSuccess = persist.slice(
        persist.indexOf("if (modalMode === 'edit')"),
        persist.indexOf("if (modalMode === 'import')"),
    );

    assert.match(editSuccess, /closeModal\(\{ force: true \}\)/);
    assert.match(editSuccess, /loadProjects\(\)/);
    assert.doesNotMatch(editSuccess, /window\.location/);
});

test('managed rename does not alter the stable project-card navigation identity', () => {
    const render = sourceBlock('function renderRow(p)', 'function renderList');
    assert.match(render, /p\.home_url/);
    assert.doesNotMatch(render, /projectDirectorySlug\(p\.display_name/);
});
