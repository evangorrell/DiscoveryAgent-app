const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'da_selection_ui');
const source = fs.readFileSync(path.join(root, 'static', 'projects.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'templates', 'projects.html'), 'utf8');

function sourceBlock(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert.ok(start >= 0 && end > start, `missing source block: ${startMarker}`);
    return source.slice(start, end);
}

test('successful Create closes its workflow and replaces history with the server-generated Projects URL', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const createSuccess = persist.slice(persist.indexOf("if (typeof data.projects_url"));

    assert.match(createSuccess, /typeof data\.projects_url !== 'string'/);
    assert.match(createSuccess, /resetSubmitState\(\)[\s\S]*closeModal\(\{ force: true \}\)[\s\S]*window\.location\.replace\(data\.projects_url\)/);
    assert.doesNotMatch(createSuccess, /window\.location\.href\s*=\s*data\.destination/);
    assert.doesNotMatch(createSuccess, /['"]\/projects(?:['"]|\/)/);
    assert.match(template, /projects\.js'\) \}\}\?v=20261102-project-location-1/);
});

test('clicking a project card title still opens that project through its canonical home URL', () => {
    const row = sourceBlock('function renderRow(p)', 'function renderList(projects)');
    const titleLink = row.slice(
        row.indexOf('const homeUrl ='),
        row.indexOf('head.appendChild(buildMenu(p))'),
    );

    assert.match(titleLink, /const homeUrl = p\.home_url \|\| \('\/projects\/' \+ encodeURIComponent\(p\.url_slug \|\| p\.id\)\)/);
    assert.match(titleLink, /const title = el\('a', 'v2-project-title'/);
    assert.match(titleLink, /title\.href = homeUrl/);
    assert.match(titleLink, /head\.appendChild\(title\)/);
    assert.doesNotMatch(titleLink, /preventDefault|addEventListener/);
});

test('Create failure stays in the modal and duplicate submission protection remains intact', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const failure = persist.slice(
        persist.indexOf('if (!(resp.ok && data && data.success))'),
        persist.indexOf("if (modalMode === 'edit')"),
    );
    const busy = sourceBlock('function setSubmitBusy()', 'async function persistProject');

    assert.match(failure, /setFormError\(message\)/);
    assert.match(failure, /resetSubmitState\(\)/);
    assert.doesNotMatch(failure, /window\.location|closeModal\(\{ force: true \}\)/);
    assert.match(busy, /els\.submit\.disabled = true/);
    assert.match(busy, /els\.submit\.classList\.add\('is-busy'\)/);
    assert.match(persist, /if \(projectSubmissionRunning\) return false/);
    assert.ok(persist.indexOf('setSubmitBusy()') < persist.indexOf('await fetch(endpoint'));
});

test('Add Data persists and restores the complete create-project draft', () => {
    const draftSource = sourceBlock('function createProjectDraftState()', 'function applyMode()');
    const storage = new Map();
    const localStorage = {
        setItem(key, value) { storage.set(key, value); },
        getItem(key) { return storage.get(key) || null; },
        removeItem(key) { storage.delete(key); },
    };
    const control = (value) => ({ value, checked: true });
    const els = {
        dataLibraryOverlay: { hidden: false },
        dataLibraryError: { hidden: true, textContent: '' },
        name: control('Draft Project'),
        objective: control('Preserve this objective exactly.'),
        context: control('Preserve this dataset context exactly.'),
        queryCount: control('17'),
        deepResearch: { checked: false }, webSearch: { checked: true },
        reasoningModel: { value: 'reasoner', options: [{ value: 'reasoner' }] },
        executionModel: { value: 'executor', options: [{ value: 'executor' }] },
        advancedSettingsToggle: { getAttribute() { return 'true'; } },
    };
    const factory = new Function('window', 'els', [
        "const CREATE_DRAFT_STORAGE_KEY = 'discoveryagent.create-project-draft.v1';",
        'const CREATE_DRAFT_VERSION = 1;',
        "let modalMode = 'create';",
        "let pendingProjectParent = '/tmp/project-parent';",
        'let pendingCanonicalDefaultLocation = false;',
        "let selectedDataSources = [{ dataset_id: 'saved', selected_paths: ['.'], excluded_paths: [] }];",
        "let draftDataSources = [{ dataset_id: 'pending', selected_paths: ['measurements.csv'], excluded_paths: [] }];",
        'const normalizeEditableDataSelection = (value) => JSON.parse(JSON.stringify(value || []));',
        'const copySelection = normalizeEditableDataSelection;',
        'let advancedExpanded = false;',
        'function setAdvancedSettingsExpanded(value) { advancedExpanded = value; }',
        draftSource,
        'return { createProjectDraftState, saveCreateProjectDraft, readCreateProjectDraft, clearCreateProjectDraft, applyCreateProjectDraft, preserveCreateDraftBeforeAddingData, state: () => ({ pendingProjectParent, pendingCanonicalDefaultLocation, selectedDataSources, draftDataSources, advancedExpanded }) };',
    ].join('\n'));
    const draft = factory({ localStorage }, els);

    assert.equal(draft.saveCreateProjectDraft(), true);
    const persisted = JSON.parse(storage.values().next().value);
    assert.equal(persisted.project_name, 'Draft Project');
    assert.equal(persisted.research_objective, 'Preserve this objective exactly.');
    assert.equal(persisted.project_context, 'Preserve this dataset context exactly.');
    assert.equal(persisted.query_count, '17');
    assert.deepEqual(persisted.data_sources.map((item) => item.dataset_id), ['pending']);

    els.name.value = ''; els.objective.value = ''; els.context.value = ''; els.queryCount.value = '';
    els.deepResearch.checked = true; els.webSearch.checked = false;
    draft.applyCreateProjectDraft(draft.readCreateProjectDraft(), { restoreProjectLocation: true });
    assert.equal(els.name.value, 'Draft Project');
    assert.equal(els.objective.value, 'Preserve this objective exactly.');
    assert.equal(els.context.value, 'Preserve this dataset context exactly.');
    assert.equal(els.queryCount.value, '17');
    assert.equal(els.deepResearch.checked, false);
    assert.equal(els.webSearch.checked, true);
    assert.deepEqual(draft.state().selectedDataSources.map((item) => item.dataset_id), ['pending']);
    assert.equal(draft.state().advancedExpanded, true);
});

test('ordinary Create uses the configured default while explicit draft return preserves its location', () => {
    const draftSource = sourceBlock('function selectHasOption', 'function preserveCreateDraftBeforeAddingData');
    const controls = {
        name: { value: '' }, objective: { value: '' }, context: { value: '' }, queryCount: { value: '' },
        reasoningModel: { value: 'reasoner', options: [{ value: 'reasoner' }] },
        executionModel: { value: 'executor', options: [{ value: 'executor' }] },
        deepResearch: { checked: true }, webSearch: { checked: true },
    };
    const factory = new Function('els', [
        "let pendingProjectParent = '/Users/scientist/DiscoveryAgent/projects';",
        'let pendingCanonicalDefaultLocation = false;',
        'let selectedDataSources = []; let draftDataSources = []; let advancedExpanded = false;',
        'const normalizeEditableDataSelection = (value) => value || [];',
        'const copySelection = (value) => value.slice();',
        'function setAdvancedSettingsExpanded(value) { advancedExpanded = value; }',
        draftSource,
        'return { applyCreateProjectDraft, state: () => ({ pendingProjectParent, pendingCanonicalDefaultLocation }) };',
    ].join('\n'));
    const draft = factory(controls);
    const saved = {
        project_parent: '/Users/scientist/Library/Application Support/DiscoveryAgent/projects',
        canonical_default_location: true,
        data_sources: [],
    };

    draft.applyCreateProjectDraft(saved);
    assert.deepEqual(draft.state(), {
        pendingProjectParent: '/Users/scientist/DiscoveryAgent/projects',
        pendingCanonicalDefaultLocation: false,
    });
    draft.applyCreateProjectDraft(saved, { restoreProjectLocation: true });
    assert.deepEqual(draft.state(), {
        pendingProjectParent: '/Users/scientist/Library/Application Support/DiscoveryAgent/projects',
        pendingCanonicalDefaultLocation: true,
    });
});

test('project draft handoff has an explicit return route and refuses navigation if storage fails', () => {
    assert.match(template, /open_data_picker='1', project_draft='1'/);
    assert.match(source, /els\.dataLibraryAdd\.addEventListener\('click', preserveCreateDraftBeforeAddingData\)/);
    assert.match(source, /function preserveCreateDraftBeforeAddingData\(event\)[\s\S]*if \(saveCreateProjectDraft\(\)\) return;[\s\S]*event\.preventDefault\(\)/);
    assert.match(source, /pendingCreateDraftRestore = initialParams\.get\('resume_project_draft'\) === '1'/);
    assert.match(source, /if \(pendingCreateDraftRestore\)[\s\S]*openCreate\(\{ restoreDraftLocation: true \}\)[\s\S]*replaceState\(\{\}, '', '\/projects'\)/);
    assert.match(source, /clearCreateProjectDraft\(\)[\s\S]*window\.location\.replace\(data\.projects_url\)/);
});

test('Edit Save and explicit Continue retain their established destinations', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const editSuccess = persist.slice(
        persist.indexOf("if (modalMode === 'edit')"),
        persist.indexOf("if (modalMode === 'import')"),
    );
    const continueProject = sourceBlock('async function continueProject(project, invoker)', '// ---- modal (create + edit)');
    const wiring = sourceBlock('function wire()', "document.addEventListener('DOMContentLoaded'");

    assert.match(editSuccess, /closeModal\(\{ force: true \}\)[\s\S]*loadProjects\(\)/);
    assert.doesNotMatch(editSuccess, /projects_url|location\.replace/);
    assert.match(continueProject, /window\.location\.href = data\.destination/);
    assert.match(wiring, /els\.cancel\.addEventListener\('click',[\s\S]*closeModal/);
});

test('first-run welcome owns the single New Project action while populated pages keep the sticky tray', () => {
    assert.equal((template.match(/id="open-new-project"/g) || []).length, 1);
    assert.ok(template.includes("{% macro new_project_button(label='New Project') -%}"));
    assert.ok(template.includes('id="open-new-project"'));
    assert.ok(template.includes('{% if projects_empty %}'));
    assert.ok(template.includes('Welcome to DiscoveryAgent!'));
    assert.ok(template.includes(`v2-onboarding-cta">{{ project_actions('Create Your First Project'`));
    assert.ok(template.includes('id="projects-primary-action" hidden'));
    assert.ok(template.includes("project_actions('New Project'"));
    assert.ok(template.includes('aria-label="{{ label }}"'));
    assert.ok(template.includes("new_project_button(create_label)"));
    assert.ok(source.includes('if (helper && els.primaryAction) pageActionLayout = helper.create'));
    assert.ok(source.includes('if (empty !== initialProjectsEmpty)'));
    assert.ok(source.includes('window.location.reload()'));
    assert.equal(source.split("els.openBtn.addEventListener('click', openCreate)").length - 1, 1);
});
