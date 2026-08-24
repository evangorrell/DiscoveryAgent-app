const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'da_selection_ui');
const source = fs.readFileSync(path.join(root, 'static', 'projects.js'), 'utf8');
const treeSource = fs.readFileSync(path.join(root, 'static', 'data_tree.js'), 'utf8');
const pickerSource = fs.readFileSync(path.join(root, 'static', 'data_picker.js'), 'utf8');
const librarySource = fs.readFileSync(path.join(root, 'static', 'data_library.js'), 'utf8');
const dataTemplate = fs.readFileSync(path.join(root, 'templates', 'data_library.html'), 'utf8');
const baseTemplate = fs.readFileSync(path.join(root, 'templates', 'base.html'), 'utf8');
const template = fs.readFileSync(path.join(root, 'templates', 'projects.html'), 'utf8');
const style = fs.readFileSync(path.join(root, 'static', 'style.css'), 'utf8');

function block(start, end) {
    const from = source.indexOf(start);
    return source.slice(from, source.indexOf(end, from));
}

test('Create and Edit use one unified Data section with one Select Data action', () => {
    assert.equal((template.match(/id="np-data-field"/g) || []).length, 1);
    assert.equal((template.match(/id="np-select-data"/g) || []).length, 1);
    assert.equal((template.match(/>PROJECT DATA<\/h3>/g) || []).length, 1);
    assert.doesNotMatch(template, />DATA<\/span>/);
    assert.match(template, /Select the data this project will have access to\./);
    assert.match(template, /<button type="button" class="v2-data-action v2-project-data-action" id="np-select-data" hidden>[\s\S]*<span class="v2-data-action-label">Select Data<\/span>[\s\S]*<span class="v2-data-action-arrow" aria-hidden="true">&#8599;<\/span>/);
    assert.match(style, /body\.v2 \[hidden\] \{ display: none !important; \}/);
    assert.doesNotMatch(template.slice(template.indexOf('id="np-data-field"'), template.indexOf('id="np-advanced-settings"')), /id="np-add-data"|>Add Data/);
    assert.doesNotMatch(template, /np-edit-data|np-create-data|INPUT DATA|Open Data Library|Open Data Folder|Copy path/);
    assert.match(source, /encodeURIComponent\(project\.id\) \+ '\?selection_only=1'/);
    assert.doesNotMatch(source, /\/api\/projects\/.*\/files|open-data-folder|renderEditDataFolder|rescanDataFolder/);
});


test('empty variants are standalone dropzones while populated data keeps one solid hierarchy card', () => {
    const fieldStart = template.indexOf('id="np-data-field"');
    const headerStart = template.indexOf('id="np-select-data"', fieldStart);
    const emptySelectStart = template.indexOf('id="np-selected-data-empty-select"', fieldStart);
    const cardStart = template.indexOf('id="np-project-data-card"', fieldStart);
    const card = template.slice(template.lastIndexOf('<div', cardStart), template.indexOf('</div>', cardStart) + 6);
    const emptySelect = template.slice(template.lastIndexOf('<button', emptySelectStart), template.indexOf('</button>', emptySelectStart) + 9);

    assert.ok(fieldStart < headerStart && headerStart < emptySelectStart && emptySelectStart < cardStart);
    assert.match(card, /class="v2-project-data-card v2-data-tree-container v2-data-surface-project" id="np-project-data-card" hidden>[\s\S]*<ul class="v2-selected-data-tree/);
    assert.doesNotMatch(card, /v2-dropzone|Click to select data|Add data to DiscoveryAgent/);
    assert.match(emptySelect, /type="button" class="v2-dropzone v2-data-empty-state v2-data-surface-project"[\s\S]*aria-label="No data selected\. Click to select data\."/);
    assert.match(emptySelect, /No data selected[\s\S]*Click to select data/);
    assert.equal((emptySelect.match(/<button\b/g) || []).length, 1);
    assert.doesNotMatch(emptySelect, /<a\b/);
    assert.match(source, /els\.projectDataCard\.hidden = empty/);
    assert.match(source, /els\.selectData\.hidden = empty/);
    assert.ok(source.includes("els.selectedDataEmptySelect.hidden = !empty"));
    assert.doesNotMatch(source, /selectedDataEmptyAdd|np-selected-data-empty-add|No data available|Add data to DiscoveryAgent/);
    assert.match(source, /els\.selectedDataTree\.hidden = empty/);
});


test('Project and app-wide empty states reuse the centered Skill Package dropzone contract', () => {
    assert.match(style, /\.v2-dropzone\s*\{[^}]*border:\s*1\.5px dashed var\(--v2-border\)[^}]*border-radius:\s*12px[^}]*background:\s*var\(--v2-bg\)[^}]*transition:/s);
    assert.match(style, /\.v2-data-empty-state\s*\{[^}]*display:\s*grid[^}]*place-items:\s*center[^}]*gap:\s*6px[^}]*width:\s*100%[^}]*padding:\s*22px 18px/s);
    assert.match(style, /\.v2-data-empty-state > \.v2-dropzone-icon,[\s\S]*\.v2-data-empty-state > \.v2-dropzone-action\s*\{\s*margin:\s*0/);
    assert.match(style, /\.v2-dropzone-action\s*\{[^}]*color:\s*var\(--v2-accent\)[^}]*text-decoration:\s*underline/s);
    assert.match(style, /\.v2-data-empty-state:hover\s*\{[^}]*border-color:\s*var\(--v2-accent-ring\)/s);
    assert.match(style, /\.v2-data-empty-state:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--v2-accent\)[^}]*outline-offset:\s*2px/s);
    assert.match(style, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.v2-data-empty-state\s*\{\s*transition:\s*none/s);
    assert.match(template, /class="v2-dropzone v2-data-empty-state v2-data-surface-project"[\s\S]*id="np-selected-data-empty-select"/);
    assert.doesNotMatch(template, /np-selected-data-empty-add|No data available|Add data to DiscoveryAgent/);
    assert.doesNotMatch(style, /v2-project-data-empty|project-data-empty-guidance|project-data-guidance-action|project-data-selection-surface/);
    assert.doesNotMatch(template, />Drop files|>Drop folders|upload/i);
    assert.doesNotMatch(source, /\/api\/data-library\/choose/);
});

test('Project Location and both populated data trees use their established shared surfaces', () => {
    const modelControl = style.slice(
        style.indexOf('.v2-input, .v2-textarea {'),
        style.indexOf('.v2-input {', style.indexOf('.v2-input, .v2-textarea {'))
    );
    const locationSurface = style.slice(
        style.indexOf('.v2-project-location-field > .v2-project-location-path {'),
        style.indexOf('}', style.indexOf('.v2-project-location-field > .v2-project-location-path {')) + 1
    );
    const projectModifier = style.slice(
        style.indexOf('.v2-project-data-card {'),
        style.indexOf('}', style.indexOf('.v2-project-data-card {')) + 1
    );
    assert.match(modelControl, /background: var\(--v2-bg\)/);
    assert.match(locationSurface, /background: var\(--v2-bg\)/);
    assert.match(locationSurface, /border-color: var\(--v2-border\)/);
    assert.match(locationSurface, /border-radius: 8px/);
    assert.match(locationSurface, /padding: 0\.85rem 1rem/);
    assert.match(locationSurface, /color: var\(--v2-ink\)/);
    assert.doesNotMatch(locationSurface, /opacity|cursor|:hover|:focus|disabled|#[0-9a-f]{3,8}/i);
    const projectSurface = style.slice(
        style.indexOf('.v2-data-surface-project {'),
        style.indexOf('}', style.indexOf('.v2-data-surface-project {')) + 1
    );
    assert.match(style, /\.v2-data-library-box\s*\{\s*background:\s*var\(--v2-surface\)/s);
    assert.match(projectSurface, /background:\s*var\(--v2-bg\)/);
    assert.doesNotMatch(projectSurface, /#[0-9a-f]{3,8}|color:|opacity:|filter:/i);
    assert.match(template, /class="v2-dropzone v2-data-empty-state v2-data-surface-project"[\s\S]*id="np-selected-data-empty-select"/);
    assert.match(template, /class="v2-project-data-card v2-data-tree-container v2-data-surface-project" id="np-project-data-card"/);
    assert.match(template, /class="v2-data-selector-browser v2-data-tree-container v2-data-surface-project" id="np-data-library-browser"/);
    assert.doesNotMatch(projectModifier, /background|cursor|transition|padding|min-height|height/);
    assert.doesNotMatch(style, /\.v2-project-data-card:hover|\.v2-project-data-card:focus-within/);
    assert.match(source, /!projectLocationAvailable[\s\S]*locateProject\.hidden/);
    assert.match(source, /const defaultLocationSelected = pendingProjectParent === els\.form\.dataset\.defaultProjectParent/);
    assert.match(source, /useDefaultLocation\.disabled = projectLocationRunning[\s\S]*defaultLocationSelected/);
    assert.equal((template.match(/id="np-project-data-card"/g) || []).length, 1, 'Create and Edit share this modal surface');
});

test('project surfaces keep canonical row-scoped hover, focus, contrast, and containment', () => {
    const surfaceRule = style.slice(
        style.indexOf('.v2-data-surface-project {'),
        style.indexOf('}', style.indexOf('.v2-data-surface-project {')) + 1
    );
    assert.match(surfaceRule, /background:\s*var\(--v2-bg\)/);
    assert.doesNotMatch(surfaceRule, /hover|focus|accent-soft|color:|opacity:|filter:|#[0-9a-f]{3,8}/i);
    assert.match(style, /\.v2-data-source-row:hover,[\s\S]*\.v2-data-tree-row:hover,[\s\S]*\.v2-data-source-row:focus-within,[\s\S]*\.v2-data-tree-row:focus-within,[\s\S]*background:\s*var\(--v2-accent-soft\)/s);
    assert.match(style, /\.v2-data-source:focus-visible > \.v2-data-source-row,[\s\S]*\.v2-data-tree-item:focus-visible > \.v2-data-tree-row/s);
    assert.match(style, /\.v2-data-tree-checkbox:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--v2-accent-ring\)/s);
    assert.match(style, /@media \(max-width: 820px\)[\s\S]*\.v2-data-tree-container \{ padding-inline: 8px; \}/);
});

test('Select Data and the empty dropzone remain the only selector entry points', () => {
    const open = block('async function openDataLibrarySelector()', 'function closeDataLibrarySelector(options)');
    const close = block('function closeDataLibrarySelector(options)', 'function saveSelectedData()');
    const dataField = template.slice(template.indexOf('id="np-data-field"'), template.indexOf('id="np-advanced-settings"'));

    assert.match(dataField, /<div class="v2-data-description-action-row v2-project-data-header-row">\s*<p class="v2-field-help v2-data-description-action-copy">Select the data this project will have access to\.<\/p>\s*<button type="button" class="v2-data-action v2-project-data-action" id="np-select-data" hidden>/);
    assert.doesNotMatch(dataField, /np-selected-data-empty-add|No data available|Add data to DiscoveryAgent|href="\{\{ url_for\('discoveryagent_data'\) \}\}"/);
    assert.doesNotMatch(dataField, /target=|onclick=|role="button"/);
    assert.match(open, /selectorLastFocused = document\.activeElement/);
    assert.match(open, /await loadLibrarySources\(\)[\s\S]*els\.dataLibraryOverlay\.hidden = false/);
    assert.doesNotMatch(open, /window\.location|\/api\/data-library\/choose/);
    assert.match(close, /config\.restoreToProjectData[\s\S]*projectDataSelectionControl\(\)/);
    assert.match(source, /els\.selectData\.addEventListener\('click', openDataLibrarySelector\)/);
    assert.match(source, /els\.selectedDataEmptySelect\.addEventListener\('click', openDataLibrarySelector\)/);
    assert.doesNotMatch(source, /els\.selectedDataEmptySelect\.addEventListener\('keydown'/);
    assert.doesNotMatch(source, /els\.projectDataCard\.addEventListener\('click'/);
    assert.doesNotMatch(source, /shouldOpenProjectDataSelectorFromTarget/);
    const projectDataBindings = source.slice(
        source.indexOf("els.selectData.addEventListener('click'"),
        source.indexOf("els.dataLibraryOverlay.addEventListener('mousedown'")
    );
    assert.doesNotMatch(projectDataBindings, /v2Native|\/api\/data-library\/choose/);
});

test('Create and Edit refresh registered sources before exposing Select Data', () => {
    const create = block('function openCreate()', 'async function openEdit(project)');
    const edit = block('async function openEdit(project)', 'function openDeleteConfirmation');
    assert.match(create, /librarySources = \[\]/);
    assert.match(create, /selectedDataSources = \[\];[\s\S]*renderSelectedDataHierarchy\(\)/);
    assert.match(create, /showModal\(\);\s*loadLibrarySources\(\)/);
    assert.match(edit, /librarySources = \[\]/);
    assert.match(edit, /selectedDataSources = \[\];[\s\S]*renderSelectedDataHierarchy\(\)/);
    assert.match(edit, /selectedDataSources = inputData\.selection \|\| \[\]/);
    assert.match(edit, /await loadLibrarySources\(\)/);
    assert.match(source, /const empty = !hasSelectedProjectData\(selectedDataSources\)/);
});

test('saving and clearing selections switch the visible state while Cancel preserves it', () => {
    const close = block('function closeDataLibrarySelector(options)', 'function saveSelectedData()');
    const save = block('function saveSelectedData()', 'function applyMode()');
    assert.match(close, /draftDataSources = copySelection\(selectedDataSources\)/);
    assert.match(save, /normalizeDraft\(\); selectedDataSources = copySelection\(draftDataSources\)/);
    assert.match(save, /renderSelectedDataHierarchy\(\)/);
    assert.match(save, /closeDataLibrarySelector\(\{ restoreToProjectData: true \}\)/);
    assert.match(close, /const preferredTarget = config\.restoreToProjectData[\s\S]*projectDataSelectionControl\(\)/);
    assert.match(source, /\.filter\(\(item\) => item\.selected_paths\.length\)/);
});

test('pending selection alone controls empty and populated action visibility', () => {
    const rendering = block('function hasSelectedProjectData(selection)', 'async function fetchSelectorTreePage(source, relative, cursor)');
    const renderState = Function('selection', 'sources', `
        let selectedDataSources = selection;
        let librarySources = sources;
        const appended = [];
        const els = {
            selectData: { hidden: false },
            projectDataCard: { hidden: false },
            selectedDataEmptySelect: { hidden: false },
            selectedDataTree: {
                hidden: false,
                replaceChildren() { appended.length = 0; },
                appendChild(value) { appended.push(value); },
            },
        };
        function renderSelectedPreviewSource(item) { return { datasetId: item.dataset_id }; }
        const projectDataTree = { renderSources(list, entries, renderEntry) { list.replaceChildren(); entries.forEach((entry) => list.appendChild(renderEntry(entry))); } };
        ${rendering}
        renderSelectedDataHierarchy();
        return {
            actionHidden: els.selectData.hidden,
            cardHidden: els.projectDataCard.hidden,
            emptySelectHidden: els.selectedDataEmptySelect.hidden,
            treeHidden: els.selectedDataTree.hidden,
            appended,
            focusTarget: projectDataSelectionControl() === els.selectData ? 'action' : 'dropzone',
        };
    `);

    const empty = renderState([], [{ id: 'ds_a' }]);
    assert.deepEqual(empty, {
        actionHidden: true, cardHidden: true, emptySelectHidden: false,
        treeHidden: true, appended: [], focusTarget: 'dropzone',
    });
    assert.equal(renderState([{ dataset_id: 'ds_a', selected_paths: [] }], [{ id: 'ds_a' }]).actionHidden, true);

    const populated = renderState([
        { dataset_id: 'ds_a', selected_paths: ['folder/file.csv'], excluded_paths: [] },
    ], [{ id: 'ds_a' }]);
    assert.equal(populated.actionHidden, false);
    assert.equal(populated.cardHidden, false);
    assert.equal(populated.emptySelectHidden, true);
    assert.equal(populated.treeHidden, false);
    assert.deepEqual(populated.appended, [{ datasetId: 'ds_a' }]);
    assert.equal(populated.focusTarget, 'action');

    const noSources = renderState([], []);
    assert.equal(noSources.actionHidden, true);
    assert.equal(noSources.emptySelectHidden, false);
    assert.equal(noSources.focusTarget, 'dropzone');
});

test('Select data stays lazy and hierarchical while Add Data navigates to the shared Data-page picker', () => {
    assert.match(template, /class="v2-data-selector-sources v2-data-source-list" id="np-data-library-sources" role="tree"/);
    assert.match(template, /id="np-data-library-cancel">Cancel/);
    assert.match(template, /id="np-data-library-save">Save/);
    assert.match(template, /<a class="v2-data-action" id="np-data-library-add-data"\s+href="\{\{ url_for\('discoveryagent_data', open_data_picker='1'\) \}\}">[\s\S]*<span class="v2-data-action-label">Add Data<\/span>[\s\S]*&#8599;/);
    assert.doesNotMatch(template.slice(template.indexOf('id="np-data-library-overlay"')), /Browse on Mac/);
    assert.match(source, /\/tree\?path=/);
    assert.match(source, /const selectorDataTree = window\.DiscoveryAgentDataTree\.createRenderer/);
    assert.match(treeSource, /checkbox\.indeterminate = Boolean\(state && state\.indeterminate\)/);
    assert.doesNotMatch(source, /DiscoveryAgentDataPicker|addDataLibrarySources|\/api\/data-library\/choose/);
    assert.match(librarySource, /window\.DiscoveryAgentDataPicker/);
    assert.match(librarySource, /AUTO_PICKER_QUERY_PARAMETER = 'open_data_picker'/);
    assert.equal((pickerSource.match(/fetch\('\/api\/data-library\/choose'/g) || []).length, 1);
});

test('Select data configures the canonical tree in selection mode', () => {
    const config = source.slice(source.indexOf('const selectorDataTree ='), source.indexOf('function renderSelectorSources'));
    const checkbox = treeSource.slice(treeSource.indexOf('function createSelectionCheckbox'), treeSource.indexOf('function appendError'));
    const childRenderer = treeSource.slice(treeSource.indexOf('function renderChild'), treeSource.indexOf('async function loadChildren'));
    const sourceRenderer = treeSource.slice(treeSource.indexOf('function renderSource'), treeSource.indexOf('return { renderSource'));
    assert.match(config, /selection:\s*\{/);
    assert.match(config, /getState:\s*\(source, path\) => pathState\(source\.id, path\)/);
    assert.match(config, /onChange:\s*\(source, path, checked\) => toggleDraftPath\(source\.id, path, checked\)/);
    assert.match(config, /isDisabled:[\s\S]*value\.kind === 'symlink'/);
    assert.doesNotMatch(config, /onRemove|removeClass/);
    assert.match(checkbox, /node\('label', 'v2-data-tree-checkbox-slot'\)/);
    assert.match(checkbox, /node\('input', 'v2-data-tree-checkbox'\)/);
    assert.match(checkbox, /slot\.addEventListener\('click', \(event\) => event\.stopPropagation\(\)\)/);
    assert.match(checkbox, /slot\.appendChild\(checkbox\)[\s\S]*return slot/);
    assert.match(checkbox, /checkbox\.dataset\.datasetId = source\.id/);
    assert.match(checkbox, /checkbox\.addEventListener\('click', \(event\) => event\.stopPropagation\(\)\)/);
    assert.match(checkbox, /checkbox\.addEventListener\('change',[\s\S]*selection\.onChange/);
    assert.match(childRenderer, /if \(selection\) row\.appendChild\(createSelectionCheckbox\(entry, child\.path/);
    assert.match(sourceRenderer, /if \(selection\) row\.appendChild\(createSelectionCheckbox\(entry, '\.', source\.display_name/);
    assert.match(style, /\.v2-data-tree-checkbox:focus-visible\s*\{[^}]*outline:/s);
    assert.match(style, /\.v2-data-tree-selection-mode \{ --v2-data-tree-checkbox-column: 24px; \}/);
    assert.match(style, /\.v2-data-tree-checkbox-slot\s*\{[^}]*align-self:\s*stretch[^}]*justify-content:\s*flex-start[^}]*min-height:\s*24px/s);
    assert.match(style, /\.v2-data-tree-checkbox\s*\{[^}]*width:\s*14px[^}]*height:\s*14px/s);
    assert.match(style, /--v2-data-tree-caret-checkbox-gap:\s*7px/);
    assert.match(style, /--v2-data-tree-checkbox-icon-gap:\s*10px/);
    assert.match(style, /--v2-data-tree-icon-name-gap:\s*8px/);
    assert.match(childRenderer, /row\.appendChild\(node\('span', 'v2-data-tree-chevron-spacer'\)\)/);
    const disclosureWire = treeSource.slice(treeSource.indexOf('function wireDirectory'), treeSource.indexOf('function renderChild'));
    assert.doesNotMatch(disclosureWire, /selection\.onChange|checkbox\.checked|checkbox\.click/);
});

test('nested project selection preserves checked and indeterminate states', () => {
    const stateFunctions = source.slice(source.indexOf('function selectionItem'), source.indexOf('function normalizeDraft'));
    const makeState = Function('draftDataSources', stateFunctions + '; return pathState;');

    const excludedBranch = makeState([{
        dataset_id: 'ds_a', selected_paths: ['.'], excluded_paths: ['private'],
    }]);
    assert.deepEqual(excludedBranch('ds_a', '.'), { checked: false, indeterminate: true });
    assert.deepEqual(excludedBranch('ds_a', 'public/file.csv'), { checked: true, indeterminate: false });
    assert.deepEqual(excludedBranch('ds_a', 'private'), { checked: false, indeterminate: false });

    const selectedFile = makeState([{
        dataset_id: 'ds_a', selected_paths: ['folder/file.csv'], excluded_paths: [],
    }]);
    assert.deepEqual(selectedFile('ds_a', '.'), { checked: false, indeterminate: true });
    assert.deepEqual(selectedFile('ds_a', 'folder'), { checked: false, indeterminate: true });
    assert.deepEqual(selectedFile('ds_a', 'folder/file.csv'), { checked: true, indeterminate: false });
});

test('Select data has a sentence-case centered header without a close control', () => {
    const selector = template.slice(template.indexOf('id="np-data-library-overlay"'));
    assert.match(selector, /class="v2-modal-head v2-data-selector-head"/);
    assert.match(selector, /id="np-data-library-title">Select data/);
    assert.doesNotMatch(selector, /id="np-data-library-title">Select Data|id="np-data-library-close"|aria-label="Close Select data"/);
    assert.doesNotMatch(source, /dataLibraryClose|np-data-library-close/);
    assert.match(style, /\.v2-data-selector-head \.v2-modal-head-main\s*\{[^}]*width:\s*100%[^}]*text-align:\s*center/s);
    assert.match(style, /\.v2-data-selector-head \.v2-field-help\s*\{[^}]*text-align:\s*center/s);
});

test('empty and populated selector states share one contained browser component', () => {
    const browserId = template.indexOf('id="np-data-library-browser"');
    const browserStart = template.lastIndexOf('<div', browserId);
    const browserEnd = template.indexOf('id="np-data-library-error"', browserStart);
    const browserMarkup = template.slice(browserStart, browserEnd);
    assert.ok(browserStart >= 0);
    assert.match(browserMarkup, /id="np-data-library-empty"/);
    assert.match(browserMarkup, /No data sources are registered\./);
    assert.equal((browserMarkup.match(/<a\b/g) || []).length, 0);
    assert.doesNotMatch(browserMarkup, /Projects page|<button|Browse on Mac/);
    assert.match(browserMarkup, /v2-data-tree-container v2-data-surface-project/);
    assert.doesNotMatch(browserMarkup, /v2-data-library-box/);
    assert.match(browserMarkup, /v2-data-source-list" id="np-data-library-sources" role="tree"/);
    assert.match(style, /\.v2-data-selector-browser\s*\{[^}]*min-height:\s*210px[^}]*max-height:[^;}]+[^}]*overflow-y:\s*auto/s);
    assert.match(style, /\.v2-data-tree-container\s*\{[^}]*border:\s*1px solid var\(--v2-border\)[^}]*border-radius:\s*10px/s);
    assert.match(source, /els\.dataLibraryEmpty\.hidden = librarySources\.length > 0/);
    assert.match(source, /selectorDataTree\.renderSources\(els\.dataLibrarySources, librarySources\)/);
    assert.doesNotMatch(source, /dataLibraryBrowser\.(?:replaceChildren|innerHTML)/);
});

test('modal Add Data is a focus-trapped canonical navigation link outside the source box', () => {
    const selectorStart = template.indexOf('id="np-data-library-overlay"');
    const actionStart = template.indexOf('id="np-data-library-add-data"', selectorStart);
    const browserStart = template.indexOf('id="np-data-library-browser"', selectorStart);
    const selectorMarkup = template.slice(selectorStart, browserStart);
    const trap = block('function trapDataSelectorFocus(event)', 'function clearInvalid(el)');
    assert.ok(selectorStart >= 0 && actionStart > selectorStart && actionStart < browserStart);
    assert.match(selectorMarkup, /<a class="v2-data-action" id="np-data-library-add-data"\s+href="\{\{ url_for\('discoveryagent_data', open_data_picker='1'\) \}\}">/);
    assert.doesNotMatch(selectorMarkup, /<button[^>]+np-data-library-add-data|target=|onclick=|role="button"|Browse on Mac/);
    assert.match(trap, /\[href\]/);
    assert.match(style, /\.v2-data-action\s*\{[^}]*color:\s*var\(--v2-accent\)/s);
    assert.match(style, /\.v2-data-action:hover \.v2-data-action-label/);
    assert.match(style, /\.v2-data-action:focus-visible\s*\{[^}]*outline:/s);
    assert.doesNotMatch(source, /dataLibraryAddData|addDataLibrarySources|DiscoveryAgentDataPicker/);
    assert.match(baseTemplate, /data_picker\.js/);
});

test('modal navigation leaves project drafts unpersisted and delegates picker work to Data', () => {
    const selectorMarkup = template.slice(template.indexOf('id="np-data-library-overlay"'));
    const loader = block('async function loadLibrarySources(options)', 'async function openDataLibrarySelector()');
    assert.match(selectorMarkup, /href="\{\{ url_for\('discoveryagent_data', open_data_picker='1'\) \}\}"/);
    assert.doesNotMatch(selectorMarkup, /form=|download=|target=/);
    assert.doesNotMatch(source, /addDataLibrarySources|registrationFailureMessage/);
    assert.match(loader, /if \(!config\.preserveProjectSelection\)[\s\S]*selectedDataSources = selectedDataSources\.filter/);
    assert.match(source, /expandedDatasetIds[\s\S]*restoreSelectorView\(config\.viewState\)/);
    assert.doesNotMatch(librarySource, /selectedDataSources|draftDataSources|submitForm|persistProject/);
});

test('the selector tree scrolls inside the fixed header and divided footer', () => {
    assert.match(style, /\.v2-data-selector-modal > \.v2-data-selector-body\s*\{[^}]*overflow:\s*hidden/s);
    assert.match(style, /\.v2-data-selector-browser\s*\{[^}]*overflow-x:\s*hidden[^}]*overflow-y:\s*auto/s);
    assert.match(style, /\.v2-data-selector-footer\s*\{[^}]*flex:\s*0 0 auto[^}]*border-top:\s*1px solid var\(--v2-border-soft\)/s);
    assert.match(style, /@media \(max-width: 820px\)[\s\S]*\.v2-data-selector-browser\s*\{[^}]*max-height:/);
    assert.match(style, /\.v2-data-selector-sources\s*\{[^}]*min-width:\s*0/s);
});

test('selector Cancel discards its draft while Save updates only pending form state', () => {
    const open = block('async function openDataLibrarySelector()', 'function closeDataLibrarySelector(options)');
    const close = block('function closeDataLibrarySelector(options)', 'function saveSelectedData()');
    const save = block('function saveSelectedData()', 'function applyMode()');
    assert.match(open, /draftDataSources = copySelection\(selectedDataSources\)/);
    assert.match(close, /draftDataSources = copySelection\(selectedDataSources\)/);
    assert.match(save, /selectedDataSources = copySelection\(draftDataSources\)/);
    assert.match(save, /renderSelectedDataHierarchy\(\)/);
    assert.doesNotMatch(open + close + save, /fetch\([^)]*\/api\/projects|submitForm/);
    assert.match(source, /body\.append\('data_sources', JSON\.stringify\(selectedDataSources\)\)/);
});

test('selector keyboard handling traps focus and prevents checkbox Enter submission', () => {
    assert.match(source, /function trapDataSelectorFocus\(event\)/);
    assert.match(source, /event\.shiftKey && document\.activeElement === first/);
    assert.match(source, /!event\.shiftKey && document\.activeElement === last/);
    assert.match(source, /e\.key === 'Escape'/);
    assert.match(source, /if \(e\.key === 'Escape'\) \{\s*e\.preventDefault\(\); closeDataLibrarySelector\(\)/);
    assert.match(source, /e\.key === 'Tab'/);
    assert.match(source, /e\.key === 'Enter' && e\.target\.matches\('input\[type="checkbox"\]'\)/);
    assert.match(source, /const preferredVisible = preferredTarget && !preferredTarget\.hidden/);
    assert.match(source, /const focusTarget = preferredVisible \? preferredTarget : projectDataSelectionControl\(\)/);
    assert.match(source, /focusTarget\.focus/);
});

test('saved selections adapt the shared lazy tree with read-only project filtering', () => {
    const preview = block('function selectedPreviewPathVisible(selection, path)', 'function announceProjectDataChange(message)');
    const rootRender = block('function renderSelectedPreviewSource(selection)', 'function hasSelectedProjectData(selection)');
    const initialRender = block('function renderSelectedDataHierarchy()', 'async function fetchSelectorTreePage(source, relative, cursor)');
    const childRenderer = treeSource.slice(treeSource.indexOf('function renderChild'), treeSource.indexOf('async function loadChildren'));
    const projectOptions = source.slice(source.indexOf('const projectDataTree ='), source.indexOf('function announceProjectDataChange'));
    assert.match(preview, /\/api\/data-library\/sources\/.*\/tree\?path=/s);
    assert.match(preview, /window\.DiscoveryAgentDataTree\.createRenderer/);
    assert.match(preview, /includeChild:[\s\S]*selectedPreviewPathVisible/);
    assert.match(preview, /sourceClass:\s*'v2-selected-data-source'/);
    assert.match(rootRender, /projectDataTree\.renderSource\(\{ source, selection \}\)/);
    assert.doesNotMatch(initialRender, /fetch\(|fetchSelectedPreviewPage/);
    assert.doesNotMatch(projectOptions, /selection:\s*\{/);
    assert.match(childRenderer, /if \(selection\) row\.appendChild\(createSelectionCheckbox/);
    assert.doesNotMatch(projectOptions + childRenderer, /Relink|source_path|absolute/i);
    assert.doesNotMatch(source, /Entire source|Selected folder/);
    assert.match(template, /id="np-selected-data-tree" role="tree"/);
    assert.match(template, /v2-selected-data-tree v2-data-source-list/);
    assert.match(style, /\.v2-data-tree-children\s*\{[^}]*padding:\s*0/);
    assert.match(treeSource, /--v2-data-tree-indent/);
    assert.match(style, /\.v2-data-tree-name\s*\{[^}]*text-overflow:\s*ellipsis/);
    assert.doesNotMatch(source, /FileReader|readAsText|readAsArrayBuffer/);
});
test('both surfaces share one mouse and keyboard directory disclosure path', () => {
    const wire = treeSource.slice(treeSource.indexOf('function wireDirectory'), treeSource.indexOf('function renderChild'));
    const childRenderer = treeSource.slice(treeSource.indexOf('function renderChild'), treeSource.indexOf('async function loadChildren'));
    const sourceRenderer = treeSource.slice(treeSource.indexOf('function renderSource'), treeSource.indexOf('return { renderSource'));
    const projectOptions = source.slice(source.indexOf('const projectDataTree ='), source.indexOf('function announceProjectDataChange'));

    assert.match(source, /window\.DiscoveryAgentDataTree\.createRenderer/);
    assert.match(projectOptions, /getSource:\s*\(entry\) => entry\.source/);
    assert.match(childRenderer, /wireDirectory\(item, row, disclosure, children, child\.name/);
    assert.match(sourceRenderer, /wireDirectory\(item, row, disclosure, tree, source\.display_name/);
    assert.match(wire, /item\.tabIndex = 0/);
    assert.equal((wire.match(/row\.addEventListener\('click'/g) || []).length, 1);
    assert.doesNotMatch(wire, /button\.addEventListener\('click'/);
    assert.match(wire, /event\.target !== item \|\| !\['Enter', ' ', 'Spacebar'\]\.includes\(event\.key\)/);
    assert.match(wire, /event\.preventDefault\(\)[\s\S]*return toggle\(\)/);
    assert.match(wire, /setDisclosure\(button, item, opening, name\)/);
    assert.match(wire, /opening && !children\.dataset\.loaded/);
    assert.match(wire, /children\.dataset\.loaded = 'true'/);
    assert.match(childRenderer, /item\.append\(row, children\)/);
    assert.match(sourceRenderer, /item\.append\(row, tree\)/);
    assert.match(style, /\.v2-data-source:focus-visible > \.v2-data-source-row,[\s\S]*\.v2-data-tree-item:focus-visible > \.v2-data-tree-row/s);
});
test('shared file rows and populated Project Data background are inert', () => {
    const childRenderer = treeSource.slice(treeSource.indexOf('function renderChild'), treeSource.indexOf('async function loadChildren'));
    const fileBranch = childRenderer.slice(childRenderer.indexOf('} else {'));
    const wiring = block('function wire()', 'wire();');

    assert.match(fileBranch, /v2-data-tree-row v2-data-tree-file/);
    assert.doesNotMatch(fileBranch, /addEventListener|wireDirectory|openDataLibrarySelector|onRemove/);
    assert.ok(!source.includes("els.projectDataCard.addEventListener"));
    assert.ok(!source.includes('shouldOpenProjectDataSelectorFromTarget'));
    assert.ok(wiring.includes("els.selectData.addEventListener('click', openDataLibrarySelector)"));
    assert.ok(wiring.includes("els.selectedDataEmptySelect.addEventListener('click', openDataLibrarySelector)"));
});
test('effective preview filtering preserves ancestors, merges paths, and applies exclusions', () => {
    const start = source.indexOf('function selectedPreviewPathVisible(selection, path)');
    const end = source.indexOf('async function fetchSelectedPreviewPage', start);
    const functionSource = source.slice(start, end);
    const isSameOrChild = (pathValue, parent) => parent === '.' || pathValue === parent || pathValue.indexOf(parent + '/') === 0;
    const visible = Function('isSameOrChild', functionSource + '; return selectedPreviewPathVisible;')(isSameOrChild);

    const entire = { selected_paths: ['.'], excluded_paths: ['RawData/private', 'Metadata/hidden'] };
    assert.equal(visible(entire, 'RawData'), true);
    assert.equal(visible(entire, 'RawData/public.csv'), true);
    assert.equal(visible(entire, 'RawData/private'), false);
    assert.equal(visible(entire, 'RawData/private/secret.csv'), false);

    const folder = { selected_paths: ['ProcessedData/tables'], excluded_paths: ['ProcessedData/tables/draft'] };
    assert.equal(visible(folder, 'ProcessedData'), true);
    assert.equal(visible(folder, 'ProcessedData/tables'), true);
    assert.equal(visible(folder, 'ProcessedData/tables/final.csv'), true);
    assert.equal(visible(folder, 'ProcessedData/figures'), false);
    assert.equal(visible(folder, 'ProcessedData/tables/draft/result.csv'), false);

    const files = {
        selected_paths: ['RawData/a/file.csv', 'RawData/a/notes.txt', 'Metadata/readme.md'],
        excluded_paths: [],
    };
    assert.equal(visible(files, 'RawData'), true);
    assert.equal(visible(files, 'RawData/a'), true);
    assert.equal(visible(files, 'RawData/b'), false);
    assert.equal(visible(files, 'Metadata'), true);
    assert.equal(visible(files, 'Metadata/unselected.txt'), false);
});
test('shared pagination stays bounded and Project Data filters symlinks', () => {
    const projectOptions = source.slice(source.indexOf('const projectDataTree ='), source.indexOf('function announceProjectDataChange'));
    assert.match(source, /cursor=' \+ encodeURIComponent\(cursor \|\| 0\)/);
    assert.match(treeSource, /if \(data\.next_cursor != null\)/);
    assert.match(treeSource, /'Load more'/);
    assert.match(treeSource, /loadChildren\(entry, parent, relative, data\.next_cursor, true, depth\)/);
    assert.match(projectOptions, /includeChild:\s*\(entry, child\) => child\.kind !== 'symlink'/);
    assert.match(projectOptions, /selectedPreviewPathVisible\(entry\.selection, child\.path\)/);
});
test('the unified Data card contains no path, availability, relink, or global removal presentation', () => {
    const dataCard = template.slice(template.indexOf('id="np-data-field"'), template.indexOf('class="v2-advanced-settings"'));
    assert.doesNotMatch(dataCard, /<code|absolute|Copy path|Available|Unavailable|Relink|Finder|IDE/);
    assert.doesNotMatch(source, /v2-data-source-status|source\.available|openRemove/);
    assert.match(style, /@media \(max-width: 820px\)[\s\S]*\.v2-data-tree-container \{ padding-inline: 8px; \}/);
});


test('Project Data description and Select Data reuse the responsive shared data header row', () => {
    const fieldStart = template.indexOf('<div class="v2-field" id="np-data-field">');
    const headerStart = template.indexOf('<div class="v2-data-description-action-row v2-project-data-header-row">', fieldStart);
    const headerEnd = template.indexOf('</div>', headerStart) + '</div>'.length;
    const emptyStart = template.indexOf('id="np-selected-data-empty-select"', fieldStart);
    const cardStart = template.indexOf('id="np-project-data-card"', fieldStart);
    const header = template.slice(headerStart, headerEnd);

    assert.ok(fieldStart < headerStart && headerStart < emptyStart && emptyStart < cardStart);
    assert.match(header, /class="v2-field-help v2-data-description-action-copy">Select the data this project will have access to\./);
    assert.match(header, /<button type="button"[^>]*id="np-select-data"/);
    assert.equal((template.match(/class="v2-data-description-action-row v2-project-data-header-row"/g) || []).length, 1);
    assert.match(style, /\.v2-data-description-action-row\s*\{[^}]*gap:\s*4px 16px[^}]*min-width:\s*0/s);
    assert.match(style, /@media \(max-width: 580px\)[\s\S]*\.v2-data-description-action-row\s*\{[^}]*align-items:\s*center[^}]*\}[\s\S]*\.v2-data-description-action-copy\s*\{[^}]*flex-basis:\s*100%/s);
    assert.doesNotMatch(style, /\.v2-data-description-action-row\s*\{[^}]*overflow-x|\.v2-data-description-action-row\s*\{[^}]*white-space:\s*nowrap/s);
});

test('shared renderer gives each selected top-level source one project-only accessible X', () => {
    const projectOptions = source.slice(source.indexOf('const projectDataTree ='), source.indexOf('function announceProjectDataChange'));
    const sourceRenderer = treeSource.slice(treeSource.indexOf('function renderSource'), treeSource.indexOf('return { renderSource'));
    const childRenderer = treeSource.slice(treeSource.indexOf('function renderChild'), treeSource.indexOf('async function loadChildren'));
    assert.match(projectOptions, /removeClass:\s*'v2-project-data-source-remove'/);
    assert.match(projectOptions, /removeLabel:\s*\(source\) => 'Remove ' \+ source\.display_name \+ ' from this project'/);
    assert.match(sourceRenderer, /if \(options\.onRemove\)/);
    assert.match(sourceRenderer, /v2-icon-button v2-data-source-remove/);
    assert.match(sourceRenderer, /options\.removeClass/);
    assert.match(sourceRenderer, /aria-hidden="true" focusable="false"/);
    assert.doesNotMatch(childRenderer, /onRemove|v2-data-source-remove|removeLabel/);
});
test('project X removes the complete matching pending source and preserves all others', () => {
    const removal = block('function removeSelectedPreviewSource(datasetId, friendlyName)', 'function renderSelectedPreviewSource(selection)');
    const execute = Function(`
        let selectedDataSources = [
            { dataset_id: 'ds_a', selected_paths: ['.', 'nested/file.csv'], excluded_paths: ['private'] },
            { dataset_id: 'ds_b', selected_paths: ['keep.csv'], excluded_paths: [] },
        ];
        let draftDataSources = JSON.parse(JSON.stringify(selectedDataSources));
        let rendered = 0;
        let announced = '';
        let focusIndex = null;
        function renderSelectedDataHierarchy() { rendered += 1; }
        function announceProjectDataChange(message) { announced = message; }
        function focusAfterProjectSourceRemoval(index) { focusIndex = index; }
        ${removal}
        removeSelectedPreviewSource('ds_a', 'Atlas A');
        return { selectedDataSources, draftDataSources, rendered, announced, focusIndex };
    `);
    const result = execute.call({});
    assert.deepEqual(result.selectedDataSources, [
        { dataset_id: 'ds_b', selected_paths: ['keep.csv'], excluded_paths: [] },
    ]);
    assert.deepEqual(result.draftDataSources, result.selectedDataSources);
    assert.equal(result.rendered, 1);
    assert.equal(result.announced, 'Atlas A removed from this project. Save changes to apply.');
    assert.equal(result.focusIndex, 0);
});

test('project X is isolated from disclosure, selection, navigation, and registry removal', () => {
    const sharedSource = treeSource.slice(treeSource.indexOf('function renderSource'), treeSource.indexOf('return { renderSource'));
    const projectOptions = source.slice(source.indexOf('const projectDataTree ='), source.indexOf('function announceProjectDataChange'));
    const removal = block('function removeSelectedPreviewSource(datasetId, friendlyName)', 'function renderSelectedPreviewSource(selection)');
    assert.match(sharedSource, /remove\.addEventListener\('click',[\s\S]*event\.stopPropagation\(\)[\s\S]*options\.onRemove\(entry, remove\)/s);
    assert.match(projectOptions, /onRemove:\s*\(entry\) => removeSelectedPreviewSource\(entry\.source\.id, entry\.source\.display_name\)/);
    assert.doesNotMatch(projectOptions + removal, /openDataLibrarySelector|window\.location|href|fetch\(|api\/data-library\/sources\/.*DELETE/);
    assert.doesNotMatch(projectOptions, /openRemove|detach_projects|DiscoveryAgent Data/);
});
test('removal focus and status behavior is predictable, including the final source', () => {
    const focus = block('function announceProjectDataChange(message)', 'function removeSelectedPreviewSource(datasetId, friendlyName)');
    assert.match(focus, /role="status"|projectDataStatus/);
    assert.match(focus, /removeButtons\[removedIndex\] \|\| removeButtons\[removedIndex - 1\] \|\| projectDataSelectionControl\(\)/);
    assert.match(focus, /target\.focus\(\{ preventScroll: true \}\)/);
    assert.match(template, /id="np-project-data-status" role="status"[\s\S]*aria-live="polite" aria-atomic="true"/);
    assert.match(style, /\.v2-project-data-status\s*\{[^}]*position:\s*absolute[^}]*clip-path:\s*inset\(50%\)/s);
    assert.equal((source.match(/els\.projectDataStatus\.textContent = ''/g) || []).length, 3);
});


test('removing the final source restores the invariant empty Project Data surface', () => {
    const render = block('function renderSelectedDataHierarchy()', 'async function fetchSelectorTreePage(source, relative, cursor)');
    assert.match(render, /const empty = !hasSelectedProjectData\(selectedDataSources\)/);
    assert.match(render, /els\.selectData\.hidden = empty/);
    assert.match(render, /els\.projectDataCard\.hidden = empty/);
    assert.ok(render.includes("els.selectedDataEmptySelect.hidden = !empty"));
    assert.doesNotMatch(render, /hasSources|selectedDataEmptyAdd|No data available|Add data to DiscoveryAgent/);
    assert.match(render, /els\.selectedDataTree\.hidden = empty/);
    assert.match(source, /removeButtons\[removedIndex\] \|\| removeButtons\[removedIndex - 1\] \|\| projectDataSelectionControl\(\)/);
});

test('project source names and X use the same compact truncation and alignment primitive', () => {
    assert.match(style, /\.v2-data-source-name\s*\{[^}]*grid-column:\s*4[^}]*min-width:\s*0[^}]*overflow:\s*hidden[^}]*text-overflow:\s*ellipsis[^}]*white-space:\s*nowrap/s);
    assert.match(style, /\.v2-icon-button\s*\{[^}]*display:\s*inline-flex[^}]*align-items:\s*center[^}]*justify-content:\s*center[^}]*flex:\s*0 0 auto[^}]*width:\s*28px[^}]*height:\s*28px/s);
    assert.doesNotMatch(style, /\.v2-selected-data-source \.v2-data-source-name|\.v2-project-data-source-remove\s*\{/);
});

test('Select data keeps checkboxes, no X, and the same trailing geometry as management', () => {
    const selectorOptions = source.slice(source.indexOf('const selectorDataTree ='), source.indexOf('function renderSelectorSources'));
    const managementOptions = source.slice(source.indexOf('const projectDataTree ='), source.indexOf('function announceProjectDataChange'));
    const sourceRenderer = treeSource.slice(treeSource.indexOf('function renderSource'), treeSource.indexOf('function ensureHeader'));
    assert.match(selectorOptions, /selection:\s*\{/);
    assert.doesNotMatch(selectorOptions, /onRemove|removeClass|removeLabel/);
    assert.match(managementOptions, /removeClass:\s*'v2-project-data-source-remove'[\s\S]*onRemove:/);
    assert.match(treeSource, /if \(selection\) row\.appendChild\(createSelectionCheckbox/);
    assert.match(sourceRenderer, /if \(options\.onRemove\)[\s\S]*v2-data-source-remove[\s\S]*options\.onRemove\(entry, remove\)/);
    assert.match(style, /\.v2-data-tree-selection-mode,\s*\.v2-data-tree-action-mode \{ --v2-data-tree-action-column: 36px; \}/);
    assert.doesNotMatch(sourceRenderer, /action-slot|action-spacer|empty-action/);
    assert.match(treeSource, /return \(rounded\.endsWith\("\.0"\) \? rounded\.slice\(0, -2\) : rounded\) \+ " " \+ units\[unit\]/);
});
