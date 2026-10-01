// Browser-free contract and pure-state coverage for Edit Project unsaved changes.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

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

function stateNormalizer() {
    const helpers = sourceBlock(
        'function normalizeEditablePaths(values)',
        'function currentEditableProjectState()'
    );
    const context = {};
    vm.runInNewContext(
        `function isSameOrChild(path, parent) {
            return parent === '.' || path === parent || path.indexOf(parent + '/') === 0;
        }
        ${helpers}
        this.normalize = normalizeEditableProjectState;`,
        context
    );
    return (value) => JSON.parse(JSON.stringify(context.normalize(value)));
}

function persistedState() {
    return {
        project_name: 'Project',
        research_objective: 'Question',
        project_context: 'Context',
        data_sources: [{
            dataset_id: 'ds_b',
            selected_paths: ['cohort', 'cohort/table.csv'],
            excluded_paths: ['cohort/private'],
        }],
        query_count: '12',
        deep_research: true,
        web_search: true,
        reasoning_model: 'claude-reasoning',
        execution_model: 'claude-execution',
        project_location: '/tmp/projects/project',
    };
}

test('the V2 unsaved dialog has associated copy and only Discard and Save actions', () => {
    assert.doesNotMatch(client, /window\.(?:confirm|alert)\s*\(/);
    assert.match(template, /id="project-unsaved-overlay" hidden/);
    assert.match(template, /v2-modal-overlay v2-confirm-overlay v2-unsaved-changes-overlay/);
    assert.match(template, /role="alertdialog" aria-modal="true"/);
    assert.match(template, /aria-labelledby="project-unsaved-title"/);
    assert.match(template, /aria-describedby="project-unsaved-description"/);
    assert.match(template, />Save changes before closing\?</);
    assert.match(template, /You have unsaved changes to this project\./);
    assert.doesNotMatch(template, /Keep Editing|project-unsaved-keep-editing/);
    assert.doesNotMatch(client, /unsavedKeepEditing|project-unsaved-keep-editing/);
    assert.match(template, /class="v2-btn v2-btn-danger"\s+id="project-unsaved-discard">Discard Changes</s);
    assert.match(template, /class="v2-btn v2-btn-primary"\s+id="project-unsaved-save">Save Changes</s);
});

test('normalization covers every persisted editable field and detects each meaningful change', () => {
    const normalize = stateNormalizer();
    const baseline = persistedState();
    const expected = normalize(baseline);
    const mutations = [
        (state) => { state.project_name = 'Different'; },
        (state) => { state.research_objective = 'Different'; },
        (state) => { state.project_context = 'Different'; },
        (state) => { state.data_sources[0].excluded_paths = []; },
        (state) => { state.query_count = '17'; },
        (state) => { state.deep_research = false; },
        (state) => { state.web_search = false; },
        (state) => { state.reasoning_model = 'other-reasoning'; },
        (state) => { state.execution_model = 'other-execution'; },
        (state) => { state.project_location = '/tmp/elsewhere/project'; },
    ];
    for (const mutate of mutations) {
        const candidate = structuredClone(baseline);
        mutate(candidate);
        assert.notDeepEqual(normalize(candidate), expected);
    }
});

test('restored and semantically equivalent values clear dirty state', () => {
    const normalize = stateNormalizer();
    const baseline = persistedState();
    const equivalent = structuredClone(baseline);
    equivalent.project_name = '  Project  ';
    equivalent.research_objective = ' Question ';
    equivalent.project_context = '\nContext\n';
    equivalent.query_count = '012';
    equivalent.project_location += '/';
    equivalent.data_sources = [{
        dataset_id: 'ds_b',
        selected_paths: ['cohort/table.csv', 'cohort', 'cohort'],
        excluded_paths: ['cohort/private', 'cohort/private'],
    }];
    equivalent.expanded_folders = ['cohort'];
    equivalent.scroll_top = 300;
    assert.deepEqual(normalize(equivalent), normalize(baseline));

    const restored = structuredClone(baseline);
    restored.project_name = 'Temporary';
    restored.project_name = baseline.project_name;
    assert.deepEqual(normalize(restored), normalize(baseline));
});

test('snapshot capture happens only after Edit hydration and includes only persisted values', () => {
    const current = sourceBlock('function currentEditableProjectState()', 'function selectionItem');
    for (const field of [
        'project_name', 'research_objective', 'project_context', 'data_sources',
        'query_count', 'deep_research', 'web_search', 'reasoning_model',
        'execution_model', 'project_location',
    ]) {
        assert.match(current, new RegExp(field));
    }
    assert.doesNotMatch(current, /advancedSettings|aria-expanded|scroll|expandedDatasetIds|draftDataSources/);
    const edit = sourceBlock('async function openEdit(project)', 'function openDeleteConfirmation');
    const hydrate = edit.indexOf('await loadLibrarySources({ throwOnError: true })');
    assert.ok(hydrate >= 0 && hydrate < edit.indexOf('capturePersistedEditSnapshot()'));
    assert.match(edit, /editingProjectId === project\.id/);
    assert.match(client, /JSON\.stringify\(currentEditableProjectState\(\)\) !== JSON\.stringify\(persistedEditSnapshot\)/);
});

test('header X, footer Cancel, Escape, backdrop, and default programmatic closes use the guard', () => {
    const close = sourceBlock('function closeModal(options)', 'function discardUnsavedChanges');
    assert.match(close, /if \(!config\.force && editHasUnsavedChanges\(\)\)/);
    assert.match(close, /openUnsavedChangesConfirmation\(config\.invoker\)/);
    const wiring = sourceBlock('function wire()', "document.addEventListener('DOMContentLoaded'");
    assert.match(wiring, /els\.close\.addEventListener\('click',[\s\S]*closeModal\(\{ invoker: e\.currentTarget \}\)/);
    assert.match(wiring, /els\.cancel\.addEventListener\('click',[\s\S]*closeModal\(\{ invoker: e\.currentTarget \}\)/);
    assert.match(wiring, /e\.target === els\.overlay[\s\S]*closeModal\(\{ invoker: document\.activeElement \}\)/);
    assert.match(wiring, /if \(!els\.overlay\.hidden\) \{ closeModal\(\{ invoker: document\.activeElement \}\); return; \}/);
    assert.match(client, /closeModal\(\{ force: true \}\)/);
});

test('dismissal preserves the draft while Discard performs no write or relocation', () => {
    const closeConfirmation = sourceBlock(
        'function closeUnsavedChangesConfirmation(options)',
        'function trapUnsavedChangesFocus'
    );
    assert.doesNotMatch(closeConfirmation, /form\.reset|selectedDataSources\s*=|pendingProjectParent\s*=|fetch\(/);
    assert.match(closeConfirmation, /els\.overlay\.removeAttribute\('inert'\)/);
    assert.match(closeConfirmation, /focusTarget\.focus\(\)/);

    const discard = sourceBlock('function discardUnsavedChanges()', 'async function saveUnsavedChanges');
    assert.match(discard, /persistedEditSnapshot = null/);
    assert.match(discard, /closeUnsavedChangesConfirmation\(\{ force: true, restoreFocus: false \}\)/);
    assert.match(discard, /closeModal\(\{ force: true \}\)/);
    assert.doesNotMatch(discard, /fetch\(|persistProject|submitCurrentProject|relocat/);
});

test('Save Changes reuses normal validation/submission and preserves established failures', () => {
    const save = sourceBlock('async function saveUnsavedChanges()', 'function setFormError');
    assert.match(save, /if \(unsavedProcessing\) return/);
    assert.match(save, /setUnsavedChangesBusy\(true\)/);
    assert.match(save, /preserveBusy: true/);
    assert.match(save, /try \{[\s\S]*submitCurrentProject\(\)[\s\S]*finally \{[\s\S]*setUnsavedChangesBusy\(false\)/);
    assert.equal((save.match(/submitCurrentProject\(\)/g) || []).length, 1);

    const normalSubmit = sourceBlock('async function submitForm(e)', '// ---- wiring');
    assert.match(normalSubmit, /return await submitCurrentProject\(\)/);
    const submission = sourceBlock('async function submitCurrentProject()', 'async function submitForm(e)');
    assert.match(submission, /showProjectSettingsValidationError/);
    assert.match(submission, /missing\[0\]\.el\.focus\(\)/);
    assert.match(submission, /return await submitPreparedProject\(body\)/);

    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    assert.match(persist, /setFormError\(message\)/);
    assert.match(persist, /resetSubmitState\(\)/);
    assert.match(persist, /closeModal\(\{ force: true \}\)/);
});

test('the Edit Project form action reads Save while confirmation retains Save Changes', () => {
    const mode = sourceBlock('function applyMode()', '// ADVANCED_SETTINGS_SCROLL_BEGIN');
    assert.match(mode, /isEdit[\s\S]*\? 'Save'/);
    assert.doesNotMatch(mode, /\? 'Save Changes'/);
    assert.match(template, /id="project-unsaved-save">Save Changes</);
});

test('the confirmation owns one focus trap, safe Escape, busy state, and narrow wrapping', () => {
    const open = sourceBlock('function openUnsavedChangesConfirmation(invoker)', 'function closeUnsavedChangesConfirmation');
    assert.match(open, /els\.overlay\.setAttribute\('inert', ''\)/);
    assert.match(open, /els\.unsavedSave\.focus\(\)/);
    assert.doesNotMatch(open, /unsavedDiscard\.focus\(\)/);
    const busy = sourceBlock('function setUnsavedChangesBusy(busy)', 'function openUnsavedChangesConfirmation');
    assert.doesNotMatch(busy, /unsavedKeepEditing/);
    assert.match(busy, /unsavedDiscard\.disabled = unsavedProcessing/);
    assert.match(busy, /unsavedSave\.disabled = unsavedProcessing/);
    assert.match(busy, /setAttribute\('aria-busy', 'true'\)/);
    const trap = sourceBlock('function trapUnsavedChangesFocus(event)', 'function closeModal(options)');
    assert.match(trap, /event\.shiftKey && document\.activeElement === first/);
    assert.match(trap, /document\.activeElement === last/);
    const wiring = sourceBlock('function wire()', "document.addEventListener('DOMContentLoaded'");
    assert.match(wiring, /e\.key === 'Escape'[\s\S]*closeUnsavedChangesConfirmation\(\)/);
    assert.match(wiring, /e\.key === 'Tab'[\s\S]*trapUnsavedChangesFocus\(e\)/);
    assert.match(wiring, /e\.target === els\.unsavedOverlay[\s\S]*closeUnsavedChangesConfirmation\(\)/);
    assert.match(css, /\.v2-unsaved-changes-actions \{ flex-wrap: wrap; \}/);
    assert.match(css, /\.v2-modal\.v2-unsaved-changes-modal \{ max-width: min\(620px, calc\(100vw - 48px\)\); \}/);
});
