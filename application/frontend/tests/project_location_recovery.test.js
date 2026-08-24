'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.join(__dirname, '..', 'da_selection_ui');
const source = fs.readFileSync(path.join(root, 'static', 'projects.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'templates', 'projects.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static', 'style.css'), 'utf8');

function block(start, end) {
    const first = source.indexOf(start);
    assert.notEqual(first, -1, `missing ${start}`);
    const last = source.indexOf(end, first + start.length);
    assert.notEqual(last, -1, `missing ${end}`);
    return source.slice(first, last);
}

test('Continue remains a real keyboard-accessible link and revalidates before navigation', () => {
    const row = block('function renderRow(p)', 'function renderList(projects)');
    assert.match(row, /const cont = el\('a', 'v2-project-continue'/);
    assert.match(row, /cont\.href = homeUrl/);
    assert.match(row, /cont\.addEventListener\('click'/);
    assert.match(row, /continueProject\(p, cont\)/);
    assert.doesNotMatch(row, /disabled/);
    assert.match(row, /<span class="v2-project-continue-label">Continue<\/span> <span class="v2-arrow" aria-hidden="true">/);
    assert.match(css, /\.v2-project-continue:hover \.v2-project-continue-label \{ text-decoration: underline; \}/);
    assert.match(css, /\.v2-project-continue:hover \.v2-arrow \{ text-decoration: none; \}/);
    assert.doesNotMatch(css, /\.v2-project-continue:hover \{ text-decoration: underline; \}/);
    const continuation = block('async function continueProject(project, invoker)', '// ---- modal (create + edit)');
    assert.match(continuation, /\/continue'/);
    assert.match(continuation, /project_location_unavailable/);
    assert.match(continuation, /openProjectRecovery/);
    assert.ok(continuation.indexOf('fetch(') < continuation.indexOf('window.location.href'));
});

test('recovery uses the established V2 modal with exact copy and wrapped saved path', () => {
    assert.match(template, /id="project-recovery-overlay" hidden/);
    assert.match(template, /v2-modal-overlay v2-confirm-overlay v2-project-recovery-overlay/);
    assert.match(template, /role="alertdialog" aria-modal="true"/);
    assert.match(template, />Project location unavailable</);
    assert.match(template, /DiscoveryAgent could not find this project at its saved location\. Locate the project folder to continue\./);
    assert.match(template, /id="project-recovery-path"/);
    assert.match(template, /id="project-recovery-cancel">Cancel</);
    assert.match(template, /id="project-recovery-locate">Locate Project</);
    assert.doesNotMatch(source, /window\.(?:alert|confirm)\s*\(/);
});

test('recovery traps focus, starts on Cancel, restores invoking focus, and blocks dismissal while busy', () => {
    const opened = block('function openProjectRecovery(project, options)', 'function closeProjectRecovery(options)');
    assert.match(opened, /setAttribute\('inert', ''\)/);
    assert.match(opened, /recoveryCancel\.focus\(\)/);
    const closed = block('function closeProjectRecovery(options)', 'async function locateMissingProject()');
    assert.match(closed, /if \(recoveryProcessing && !config\.force\) return/);
    assert.match(closed, /recoveryLastFocused\.focus\(\)/);
    const trap = block('function trapRecoveryFocus(event)', 'function setRecoveryBusy(busy)');
    assert.match(trap, /document\.activeElement === first/);
    assert.match(trap, /document\.activeElement === last/);
    const wiring = block('function wire()', "document.addEventListener('DOMContentLoaded'");
    assert.match(wiring, /recoveryOverlay\.addEventListener\('keydown'/);
    assert.match(wiring, /e\.key === 'Escape'[\s\S]*closeProjectRecovery\(\)/);
    assert.match(wiring, /e\.key === 'Tab'[\s\S]*trapRecoveryFocus\(e\)/);
});

test('Locate Project uses one server picker path, stays open on cancel/error, and resumes the original action', () => {
    const request = block('async function requestProjectLocationRecovery(projectId)', 'function applyRecoveredEditLocation');
    assert.match(request, /\/api\/projects\/['"] \+ encodeURIComponent\(projectId\) \+ ['"]\/locate/);
    assert.match(request, /method: 'POST'/);
    const locate = block('async function locateMissingProject()', 'function openCreate()');
    assert.match(locate, /requestProjectLocationRecovery\(requestState\.project\.id\)/);
    assert.match(locate, /if \(data\.cancelled\)/);
    assert.match(locate, /recoveryError\.textContent/);
    assert.match(locate, /window\.location\.href = data\.destination/);
    assert.match(locate, /requestState\.action === 'edit-save'/);
    assert.match(locate, /submitPreparedProject\(body\)/);
    assert.doesNotMatch(locate, /form\.reset|selectedDataSources\s*=\s*\[\]/);
});

test('Edit inline Locate opens the shared native picker directly without opening the recovery dialog', () => {
    const direct = block('async function locateEditProjectInline()', 'function openCreate()');
    assert.match(direct, /recoveryProcessing \|\| modalMode !== 'edit'/);
    assert.match(direct, /requestProjectLocationRecovery\(projectId\)/);
    assert.doesNotMatch(direct, /openProjectRecovery|recoveryOverlay/);
    assert.match(direct, /if \(data\.cancelled\) return/);
    assert.match(direct, /applyRecoveredEditLocation\(projectId, data\)/);
    assert.match(direct, /setFormError\(error && error\.message/);
    assert.match(template, /class="v2-modal-form-error" id="np-form-error" role="alert"/);
    assert.match(css, /\.v2-modal-form-error {[\s\S]*color: var\(--v2-danger, #b3261e\)/);
    assert.match(direct, /projectLocationAvailable \? els\.chooseLocation : invoker/);
    const busy = block('function setInlineLocationRecoveryBusy(busy)', 'async function locateEditProjectInline()');
    assert.match(busy, /recoveryProcessing = Boolean\(busy\)/);
    assert.match(busy, /aria-busy/);
    assert.match(busy, /Locating…/);
    const wiring = block('function wire()', "document.addEventListener('DOMContentLoaded'");
    assert.match(wiring, /locateProject\.addEventListener\('click', locateEditProjectInline\)/);
    assert.doesNotMatch(wiring, /locateProject\.addEventListener\('click',[\s\S]*openProjectRecovery/);
});

test('direct recovery preserves the Edit draft and clears only the unavailable location state', () => {
    const apply = block('function applyRecoveredEditLocation(projectId, data)', 'async function locateMissingProject()');
    assert.match(apply, /currentProjectRoot = data\.root/);
    assert.match(apply, /currentProjectParent = data\.parent/);
    assert.match(apply, /projectLocationAvailable = true/);
    assert.match(apply, /if \(pendingUsedSavedParent\) pendingProjectParent = data\.parent/);
    assert.match(apply, /renderProjectLocation\(\)/);
    assert.doesNotMatch(apply, /form\.reset|name\.value|objective\.value|context\.value|selectedDataSources\s*=/);
});

test('Edit exposes registry-backed location state and requires Locate before save', () => {
    assert.match(template, /class="v2-btn v2-btn-secondary v2-compact-secondary" id="np-locate-project" hidden>Locate Project</);
    assert.match(template, /id="np-project-location-status" role="status" aria-live="polite" aria-atomic="true"/);
    const location = block('function renderProjectLocation()', 'async function chooseProjectLocation()');
    assert.match(location, /!projectLocationAvailable/);
    assert.match(location, /saved project location is unavailable/);
    assert.match(location, /classList\.toggle\('is-error', unavailable\)/);
    assert.match(location, /setAttribute\('role', unavailable \? 'alert' : 'status'\)/);
    assert.match(location, /locateProject\.hidden = modalMode !== 'edit' \|\| projectLocationAvailable/);
    assert.match(css, /\.v2-project-location-status\.is-error \{ color: var\(--v2-danger, #b3261e\); \}/);
    const opened = block('async function openEdit(project)', 'function openDeleteConfirmation(project)');
    assert.match(opened, /project\.location_available !== false/);
    assert.match(opened, /project_location\.available !== false/);
    const prepared = block('async function submitPreparedProject(body)', 'async function submitCurrentProject()');
    assert.match(prepared, /!projectLocationAvailable/);
    assert.match(prepared, /action: 'edit-save'/);
    assert.ok(prepared.indexOf('openProjectRecovery') < prepared.indexOf('persistProject'));
});

test('unavailable deletion has distinct accurate registry-only confirmation and server revalidation', () => {
    assert.match(source, /Remove unavailable project\?/);
    assert.match(template, /it will not delete files that may exist at another location\./);
    assert.match(template, /id="delete-project-confirm">Delete</);
    const copy = block('function renderDeleteConfirmationCopy()', 'function closeDeleteConfirmation()');
    assert.match(copy, /pendingDeleteUnavailable/);
    assert.match(copy, /Remove Project/);
    const deletion = block('async function confirmDeleteProject()', 'function resetProjectModalScroll()');
    assert.match(deletion, /confirm_unavailable=true/);
    assert.match(deletion, /project_unavailable_delete_confirmation_required/);
    assert.match(deletion, /renderDeleteConfirmationCopy\(\)/);
});

test('recovery modal has a bounded narrow layout and path/error overflow protection', () => {
    assert.match(css, /\.v2-project-recovery-overlay \{ z-index: 1400; \}/);
    assert.match(css, /\.v2-modal\.v2-project-recovery-modal \{ max-width: min\(600px, calc\(100vw - 48px\)\); \}/);
    assert.match(css, /\.v2-confirm-path[\s\S]*overflow-wrap:\s*anywhere/);
    assert.match(css, /\.v2-project-recovery-error[\s\S]*overflow-wrap:\s*anywhere/);
});

test('the recovery dialog remains reachable for non-inline Continue and save-retry surfaces', () => {
    const continuation = block('async function continueProject(project, invoker)', '// ---- modal (create + edit)');
    assert.match(continuation, /openProjectRecovery/);
    const prepared = block('async function submitPreparedProject(body)', 'async function submitCurrentProject()');
    assert.match(prepared, /openProjectRecovery/);
    assert.match(template, /id="project-recovery-overlay" hidden/);
});
