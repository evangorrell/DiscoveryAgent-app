const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../da_selection_ui/static/projects.js'), 'utf8');
function block(start, end) {
    const index = source.indexOf(start);
    assert.ok(index >= 0 && source.indexOf(end, index) > index);
    return source.slice(index, source.indexOf(end, index));
}
const selection = [{ dataset_id: 'ds_saved', selected_paths: ['.'], excluded_paths: [] }];
const copy = value => JSON.parse(JSON.stringify(value));
const reply = data => ({ ok: true, json: async () => data });
function detail(id = 'project') {
    return { success: true, project: {
        display_name: id, task_description: 'Objective', dataset_context: 'Context',
        data_sources: { selection: copy(selection), error: null },
        project_location: { root: '/projects/' + id, parent: '/projects', available: true },
    } };
}
function harness(fetch) {
    const field = () => ({ value: '', getAttribute: () => '', classList: { remove() {} } });
    const c = {
        fetch, readJson: response => response.json(), copySelection: copy,
        editLoadToken: 0, libraryLoadToken: 0, persistedEditSnapshot: null,
        modalMode: 'edit', selectedDataSources: copy(selection), draftDataSources: copy(selection),
        librarySources: [], els: {}, error: null,
    };
    for (const name of ['submit', 'name', 'objective', 'context', 'reasoningModel', 'executionModel',
        'queryCount', 'deepResearch', 'webSearch', 'projectDataStatus', 'dataLibraryLoading', 'dataLibraryError']) c.els[name] = field();
    c.els.form = { reset() {}, dataset: { defaultProjectParent: '/projects' } };
    for (const name of ['renderSelectedDataHierarchy', 'renderSelectorSources', 'restoreSelectorView',
        'applyMode', 'clearInvalid', 'setAdvancedSettingsExpanded', 'renderProjectLocation', 'showModal']) c[name] = () => {};
    c.setFormError = message => { c.error = message; };
    c.capturePersistedEditSnapshot = () => { c.persistedEditSnapshot = { data_sources: copy(c.selectedDataSources) }; };
    vm.createContext(c);
    vm.runInContext([
        block('async function loadLibrarySources(options)', 'async function openDataLibrarySelector()'),
        block('function resetSubmitState()', 'function relocationFocusableElements()'),
        block('async function openEdit(project)', 'function openDeleteConfirmation'),
        block('async function submitCurrentProject()', '// ---- wiring'),
    ].join('\n'), c);
    return c;
}

test('empty or unavailable library results never prune saved or draft attachments', async () => {
    for (const sources of [[], [{ id: 'ds_saved', available: false }]]) {
        const c = harness(async () => reply({ success: true, sources }));
        assert.equal(await c.loadLibrarySources(), true);
        assert.deepEqual(c.selectedDataSources, selection);
        assert.deepEqual(c.draftDataSources, selection);
    }
});

test('failed library refresh retains selections and reports an error', async () => {
    const c = harness(async () => { throw new Error('Offline'); });
    assert.equal(await c.loadLibrarySources(), false);
    assert.deepEqual(c.selectedDataSources, selection);
    assert.deepEqual(c.draftDataSources, selection);
    assert.equal(c.els.dataLibraryError.hidden, false);
});

test('Edit enables Save only after successful detail and library hydration', async () => {
    let resolveDetail;
    const pending = new Promise(resolve => { resolveDetail = resolve; });
    const c = harness(async url => url.includes('selection_only') ? pending : reply({ success: true, sources: [] }));
    const opening = c.openEdit({ id: 'project' });
    assert.equal(c.els.submit.disabled, true);
    resolveDetail(reply(detail()));
    await opening;
    assert.equal(c.els.submit.disabled, false);
    assert.deepEqual(c.selectedDataSources, selection);
    assert.deepEqual(c.persistedEditSnapshot.data_sources, selection);
});

for (const failure of ['network', 'selection-error', 'missing-selection', 'library']) {
    test(`Edit ${failure} failure blocks Save and normal form/Enter submission`, async () => {
        let requests = 0;
        const c = harness(async url => {
            requests++;
            if (failure === 'network') throw new Error('Offline');
            if (!url.includes('selection_only')) throw new Error('Library unavailable');
            const data = detail();
            if (failure === 'selection-error') data.project.data_sources.error = { code: 'unavailable' };
            if (failure === 'missing-selection') delete data.project.data_sources;
            return reply(data);
        });
        await c.openEdit({ id: 'project' });
        assert.equal(c.els.submit.disabled, true);
        assert.equal(c.persistedEditSnapshot, null);
        assert.ok(c.error);
        const before = requests;
        let prevented = false;
        assert.equal(await c.submitForm({ preventDefault() { prevented = true; } }), false);
        assert.equal(prevented, true);
        assert.equal(requests, before, 'failed hydration cannot issue a save request');
    });
}

test('late detail response cannot overwrite a newly opened project', async () => {
    let resolveOld;
    const pending = new Promise(resolve => { resolveOld = resolve; });
    const c = harness(async url => url.includes('/old?') ? pending : reply(
        url.includes('selection_only') ? detail('new') : { success: true, sources: [] }
    ));
    const old = c.openEdit({ id: 'old' });
    await c.openEdit({ id: 'new' });
    resolveOld(reply(detail('old')));
    await old;
    assert.equal(c.els.name.value, 'new');
    assert.equal(c.els.submit.disabled, false);
});

test('late library response cannot replace the newer library result', async () => {
    let resolveOld;
    const pending = new Promise(resolve => { resolveOld = resolve; });
    let count = 0;
    const c = harness(async () => ++count === 1 ? pending : reply({ success: true, sources: [{ id: 'ds_new' }] }));
    const old = c.loadLibrarySources();
    await c.loadLibrarySources();
    resolveOld(reply({ success: true, sources: [] }));
    assert.equal(await old, false);
    assert.equal(c.librarySources[0].id, 'ds_new');
});
