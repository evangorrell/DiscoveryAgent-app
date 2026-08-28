const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'da_selection_ui/templates/projects.html'), 'utf8');
const client = fs.readFileSync(path.join(root, 'da_selection_ui/static/projects.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'da_selection_ui/static/style.css'), 'utf8');

function sourceBlock(start, end) {
    const startIndex = client.indexOf(start);
    assert.notEqual(startIndex, -1, `missing ${start}`);
    const endIndex = client.indexOf(end, startIndex);
    assert.notEqual(endIndex, -1, `missing ${end}`);
    return client.slice(startIndex, endIndex);
}

test('both Projects states expose the exact secondary Import action', () => {
    assert.equal((template.match(/>Import Existing Project<\/button>/g) || []).length, 1);
    assert.match(template, /project_actions\('Create Your First Project', 'open-import-project-empty'/);
    assert.match(template, /project_actions\('New Project', 'open-import-project'/);
    assert.match(template, /class="v2-project-import-inline-error"[^>]*role="alert"[^>]*hidden/);
    assert.match(css, /\.v2-import-project-action:hover[^}]*text-decoration:\s*underline/s);
    assert.match(css, /\.v2-import-project-action:focus-visible[^}]*text-decoration:\s*underline/s);
});

test('picker cancellation is silent and restores focus to the invoking action', () => {
    const choose = sourceBlock('async function chooseImportProject(invoker)', 'function openCreate()');
    assert.match(choose, /fetch\('\/api\/project-import\/choose'/);
    assert.match(choose, /if \(data\.cancelled\) \{\s*restoreFocus = true;\s*return;/);
    assert.match(choose, /setImportInlineError\(\s*invoker,/);
    assert.match(choose, /finally \{\s*setImportPickerBusy\(invoker, false\);\s*if \(restoreFocus\) invoker\.focus\(\);/);
    assert.doesNotMatch(choose, /alert\(|confirm\(/);
});

test('confirmed import reuses Create fields while locking the exact selected root', () => {
    const open = sourceBlock('function openImportForm(root, metadata, previewToken, invoker)', 'async function chooseImportProject');
    const location = sourceBlock('function renderProjectLocation()', 'async function chooseProjectLocation');
    assert.match(open, /modalMode = 'import'/);
    assert.match(open, /importPreviewToken = String\(previewToken \|\| ''\)/);
    assert.match(open, /els\.name\.value = \(metadata && metadata\.project_name\)/);
    assert.match(open, /els\.objective\.value = \(metadata && metadata\.task_description\)/);
    assert.match(open, /els\.context\.value = \(metadata && metadata\.dataset_context\)/);
    assert.match(open, /renderImportWarnings\(metadata && metadata\.warnings\)/);
    assert.match(location, /els\.projectLocationPath\.textContent = importProjectRoot/);
    assert.match(location, /This existing folder remains the exact project root\./);
    assert.match(location, /els\.projectLocationActions\.hidden = true/);
    assert.match(template, /Existing files remain in place and are not copied or relocated\./);
});

test('first-time import returns to Projects instead of opening Project Home', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const importStart = persist.indexOf("if (modalMode === 'import')", persist.indexOf("if (modalMode === 'edit')"));
    const importValidation = persist.indexOf("if (typeof data.projects_url", importStart);
    const createValidation = persist.indexOf("if (typeof data.projects_url", importValidation + 1);
    const importSuccess = persist.slice(importStart, createValidation);
    const prepared = sourceBlock('async function submitPreparedProject(body)', 'async function submitCurrentProject()');
    const submit = sourceBlock('async function submitCurrentProject()', 'async function submitForm(e)');
    assert.match(persist, /modalMode === 'import'[\s\S]*'\/api\/projects\/import'/);
    assert.match(importSuccess, /typeof data\.projects_url !== 'string'/);
    assert.match(importSuccess, /resetSubmitState\(\)[\s\S]*closeModal\(\{ force: true \}\)[\s\S]*window\.location\.replace\(data\.projects_url\)/);
    assert.doesNotMatch(importSuccess, /location\.(?:replace|href)[^\n]*data\.destination/);
    assert.match(prepared, /if \(modalMode === 'import'\) return await persistProject\(body\)/);
    assert.match(submit, /body\.append\('project_root', importProjectRoot\)/);
    assert.match(
        submit,
        /if \(modalMode === 'import'\) \{[\s\S]*body\.append\('project_root', importProjectRoot\);[\s\S]*body\.append\('preview_token', importPreviewToken\);[\s\S]*\}\s*else body\.append\('project_parent_dir', pendingProjectParent\);/
    );
});

test('valid-marker re-registration uses the same Projects-list success path', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const submit = sourceBlock('async function submitCurrentProject()', 'async function submitForm(e)');
    const importStart = persist.indexOf("if (modalMode === 'import')", persist.indexOf("if (modalMode === 'edit')"));
    const importValidation = persist.indexOf("if (typeof data.projects_url", importStart);
    const createValidation = persist.indexOf("if (typeof data.projects_url", importValidation + 1);
    const importSuccess = persist.slice(importStart, createValidation);
    assert.match(submit, /body\.append\('preview_token', importPreviewToken\)/);
    assert.match(importSuccess, /window\.location\.replace\(data\.projects_url\)/);
    assert.doesNotMatch(importSuccess, /data\.destination/);
});

test('imported projects expose registry-only Remove Project while managed projects keep Delete', () => {
    const menu = sourceBlock('function buildMenu(project)', '// ---- project rows');
    const open = sourceBlock('function openDeleteConfirmation(project)', 'function renderDeleteConfirmationCopy()');
    const copy = sourceBlock('function renderDeleteConfirmationCopy()', 'function closeDeleteConfirmation()');
    const confirm = sourceBlock('async function confirmDeleteProject()', 'function resetProjectModalScroll()');
    assert.match(menu, /project\.imported_in_place \? 'Remove Project' : 'Delete'/);
    assert.match(open, /pendingDeleteImported = project\.imported_in_place === true/);
    assert.match(copy, /if \(pendingDeleteImported\)/);
    assert.match(copy, /deleteImportedCopy\.hidden = false/);
    assert.match(copy, /deleteConfirm\.textContent = 'Remove Project'/);
    assert.match(copy, /else \{[\s\S]*deleteTitle\.textContent = 'Delete this project\?'/);
    assert.match(confirm, /const registryOnly = pendingDeleteImported \|\| pendingDeleteUnavailable/);
    assert.match(template, /id="delete-project-imported-copy" hidden/);
    assert.match(template, /removes only its DiscoveryAgent registration/);
    assert.match(template, /folder and every file inside it will remain unchanged/);
});
