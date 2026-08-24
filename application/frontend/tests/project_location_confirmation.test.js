// Browser-free contract coverage for Edit Project relocation confirmation.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(ROOT, 'da_selection_ui/templates/projects.html'), 'utf8');
const client = fs.readFileSync(path.join(ROOT, 'da_selection_ui/static/projects.js'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'da_selection_ui/static/style.css'), 'utf8');

function sourceBlock(start, end) {
    const startIndex = client.indexOf(start);
    assert.notEqual(startIndex, -1, `missing ${start}`);
    const endIndex = client.indexOf(end, startIndex);
    assert.notEqual(endIndex, -1, `missing ${end}`);
    return client.slice(startIndex, endIndex);
}

test('relocation uses a compact V2 dialog and no browser-native prompt', () => {
    assert.doesNotMatch(client, /window\.(?:confirm|alert)\s*\(/);
    assert.match(template, /id="project-relocation-overlay"[^>]*hidden/);
    assert.match(template, /v2-modal-overlay v2-confirm-overlay v2-relocation-overlay/);
    assert.match(template, /v2-modal v2-modal-compact v2-confirm-modal v2-relocation-modal/);
    assert.match(template, /role="alertdialog" aria-modal="true"/);
    assert.match(template, /aria-labelledby="project-relocation-title"/);
    assert.match(template, /aria-describedby="project-relocation-description project-relocation-support"/);
    assert.match(template, />Move project\?</);
    assert.match(template, /DiscoveryAgent will move this project and all of its files to:/);
    assert.match(template, /Your DiscoveryAgent Data sources will remain in their original locations\./);
    assert.match(template, /id="project-relocation-cancel">Cancel</);
    assert.match(template, /id="project-relocation-confirm">Move Project</);
});

test('the canonical destination is rendered as wrapped code without narrow overflow', () => {
    assert.match(template, /v2-project-location-path v2-confirm-path/);
    assert.match(client, /els\.relocationDestination\.textContent = destination/);
    const pathRule = css.slice(css.indexOf('.v2-confirm-path {'), css.indexOf('}', css.indexOf('.v2-confirm-path {')));
    assert.match(pathRule, /overflow-wrap:\s*anywhere/);
    assert.match(pathRule, /word-break:\s*break-word/);
    assert.match(css, /\.v2-modal\.v2-relocation-modal \{ max-width: min\(600px, calc\(100vw - 48px\)\); \}/);
});

test('only a changed Edit destination opens confirmation before any request', () => {
    const submit = sourceBlock('async function submitPreparedProject(body)', 'async function submitCurrentProject()');
    assert.match(submit, /const relocating = modalMode === 'edit' && currentProjectRoot && destination !== currentProjectRoot/);
    assert.match(submit, /if \(relocating\)/);
    assert.match(submit, /if \(projectHasActiveRound\)[\s\S]*openRelocationConfirmation\(body, destination\)/);
    assert.ok(
        submit.indexOf('openRelocationConfirmation(body, destination)') < submit.lastIndexOf('await persistProject(body)'),
        'confirmation must precede the first save request for a relocation'
    );
    assert.match(submit, /await persistProject\(body\)/);
});

test('Cancel preserves Edit state and restores focus without sending or mutating', () => {
    const close = sourceBlock('function closeRelocationConfirmation(options)', 'function trapRelocationFocus');
    assert.doesNotMatch(close, /fetch\(|form\.reset\(|pendingProjectParent\s*=|selectedDataSources\s*=/);
    assert.match(close, /els\.relocationOverlay\.hidden = true/);
    assert.match(close, /els\.overlay\.removeAttribute\('inert'\)/);
    assert.match(close, /els\.submit\.focus\(\)/);
    assert.match(client, /relocationCancel\.addEventListener\('click', \(\) => closeRelocationConfirmation\(\)\)/);
});

test('Move Project submits one confirmed snapshot and blocks duplicate activation', () => {
    const confirm = sourceBlock('async function confirmProjectRelocation()', 'async function submitPreparedProject(body)');
    assert.match(confirm, /if \(relocationProcessing \|\| !pendingRelocationBody\) return/);
    assert.equal((confirm.match(/confirm_project_relocation/g) || []).length, 1);
    assert.equal((confirm.match(/persistProject\(/g) || []).length, 1);
    assert.match(confirm, /setRelocationBusy\(true\)/);
    const busy = sourceBlock('function setRelocationBusy(busy)', 'function openRelocationConfirmation');
    assert.match(busy, /relocationCancel\.disabled = relocationProcessing/);
    assert.match(busy, /relocationConfirm\.disabled = relocationProcessing/);
    assert.match(busy, /setAttribute\('aria-busy', 'true'\)/);
});

test('the dialog traps focus, starts on Cancel, and cannot dismiss while busy', () => {
    const open = sourceBlock('function openRelocationConfirmation(body, destination)', 'function closeRelocationConfirmation');
    assert.match(open, /els\.overlay\.setAttribute\('inert', ''\)/);
    assert.match(open, /els\.relocationCancel\.focus\(\)/);
    const close = sourceBlock('function closeRelocationConfirmation(options)', 'function trapRelocationFocus');
    assert.match(close, /if \(relocationProcessing && !config\.force\) return/);
    const trap = sourceBlock('function trapRelocationFocus(event)', 'function recoveryFocusableElements');
    assert.match(trap, /event\.shiftKey && document\.activeElement === first/);
    assert.match(trap, /document\.activeElement === last/);
    const wiring = sourceBlock('function wire()', "document.addEventListener('DOMContentLoaded'");
    assert.match(wiring, /e\.key === 'Escape'[\s\S]*closeRelocationConfirmation\(\)/);
    assert.match(wiring, /e\.key === 'Tab'[\s\S]*trapRelocationFocus\(e\)/);
    assert.match(wiring, /e\.target === els\.relocationOverlay\) closeRelocationConfirmation\(\)/);
});

test('failed relocation returns to Edit with its inline V2 error and rollback response', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    assert.match(persist, /setFormError\(message\)/);
    assert.match(persist, /closeRelocationConfirmation\(\{ force: true, restoreFocus: true \}\)/);
    assert.match(persist, /if \(config\.relocation\)[\s\S]*return false/);
    assert.match(template, /id="np-form-error" role="alert" hidden/);
});
