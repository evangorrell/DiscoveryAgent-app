// Browser-free presentation/client contract for project-level model selection.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(ROOT, 'da_selection_ui/templates/projects.html'), 'utf8');
const baseTemplate = fs.readFileSync(path.join(ROOT, 'da_selection_ui/templates/base.html'), 'utf8');
const projectsClient = fs.readFileSync(path.join(ROOT, 'da_selection_ui/static/projects.js'), 'utf8');
const roundStart = fs.readFileSync(path.join(ROOT, 'da_selection_ui/static/round_start.js'), 'utf8');
const style = fs.readFileSync(path.join(ROOT, 'da_selection_ui/static/style.css'), 'utf8');

function sourceBlock(start, end) {
    const startIndex = projectsClient.indexOf(start);
    assert.notEqual(startIndex, -1, `missing ${start}`);
    const endIndex = projectsClient.indexOf(end, startIndex);
    assert.notEqual(endIndex, -1, `missing ${end}`);
    return projectsClient.slice(startIndex, endIndex);
}

function advancedSettingsHarness(options = {}) {
    const attributes = new Map([['aria-expanded', 'false']]);
    const frameCallbacks = new Map();
    const scrollCalls = [];
    let nextFrame = 0;
    const targetRect = options.targetRect || { top: 560, bottom: 680, height: 120 };
    const containerRect = options.containerRect || { top: 100, bottom: 500, height: 400 };
    const target = { getBoundingClientRect: () => ({ ...targetRect }) };
    const toggle = {
        setAttribute(name, value) { attributes.set(name, value); },
        getAttribute(name) { return attributes.get(name); },
    };
    const panel = {
        hidden: true,
        firstElementChild: target,
        querySelector(selector) { return selector === '.v2-advanced-query-field' ? target : null; },
    };
    const modalBody = {
        scrollTop: options.scrollTop || 0,
        scrollHeight: options.scrollHeight || 1000,
        clientHeight: options.clientHeight || containerRect.height,
        getBoundingClientRect: () => ({ ...containerRect }),
        scrollTo(argument) { scrollCalls.push(argument); this.scrollTop = argument.top; },
    };
    const mockWindow = {
        requestAnimationFrame(callback) { const id = ++nextFrame; frameCallbacks.set(id, callback); return id; },
        cancelAnimationFrame(id) { frameCallbacks.delete(id); },
        setTimeout(callback) { const id = ++nextFrame; frameCallbacks.set(id, callback); return id; },
        clearTimeout(id) { frameCallbacks.delete(id); },
        matchMedia() { return { matches: Boolean(options.reducedMotion) }; },
        scrollTo() { throw new Error('the background page must not scroll'); },
    };
    const context = {
        els: {
            overlay: { hidden: Boolean(options.overlayHidden) },
            modalBody,
            advancedSettingsToggle: toggle,
            advancedSettingsPanel: panel,
        },
        window: mockWindow,
        Math,
        Boolean,
    };
    const implementation = sourceBlock(
        '// ADVANCED_SETTINGS_SCROLL_BEGIN',
        '// ADVANCED_SETTINGS_SCROLL_END'
    );
    vm.runInNewContext(
        implementation + '\nthis.api = { setAdvancedSettingsExpanded, cancelAdvancedSettingsScroll };',
        context
    );
    return {
        api: context.api,
        toggle,
        panel,
        modalBody,
        scrollCalls,
        pendingFrames: () => frameCallbacks.size,
        runFrame() {
            const entry = frameCallbacks.entries().next().value;
            if (!entry) return false;
            frameCallbacks.delete(entry[0]);
            entry[1]();
            return true;
        },
        flushLayout() { this.runFrame(); this.runFrame(); },
    };
}

test('Create/Edit has separate accessible Advanced Settings controls in the required order', () => {
    assert.equal((template.match(/id="np-model"/g) || []).length, 0);
    assert.equal((template.match(/id="np-reasoning-model"/g) || []).length, 1);
    assert.equal((template.match(/id="np-execution-model"/g) || []).length, 1);
    assert.equal((template.match(/id="np-advanced-settings-toggle"/g) || []).length, 1);
    assert.equal((template.match(/id="np-advanced-settings-panel"/g) || []).length, 1);
    assert.match(template, /<h3[^>]*>MODEL SELECTION<\/h3>/);
    assert.equal((template.match(/>MODEL SELECTION<\/h3>/g) || []).length, 1);
    assert.match(template, /for="np-reasoning-model">Reasoning Model<\/label>/);
    assert.match(template, /for="np-execution-model">Execution Model<\/label>/);
    assert.doesNotMatch(template, />Model<\/label>/);
    assert.match(template, /Choose the Anthropic models DiscoveryAgent will use for reasoning and execution in this project\./);
    const queryInput = template.match(/<input[^>]*id="np-query-count"[^>]*>/s)?.[0] || '';
    assert.match(queryInput, /type="number"[^>]*min="1" max="50" step="1"[^>]*required[^>]*aria-label="Query Count"/s);
    assert.match(template, /value="\{\{ default_query_count \}\}"/);
    assert.match(template, /data-default-query-count="\{\{ default_query_count \}\}"/);
    assert.match(template, /Choose the maximum number of research queries DiscoveryAgent can run per round\./);
    assert.doesNotMatch(template, /Automatic \(5 for Round 0, 12 after\)/);
    assert.doesNotMatch(queryInput, /placeholder=/);
    assert.doesNotMatch(template, /<option value="automatic"/);
    assert.match(template, /id="np-deep-research" checked/);
    assert.match(template, /id="np-web-search" checked/);
    assert.ok(template.indexOf('RESEARCH OBJECTIVE') < template.indexOf('PROJECT CONTEXT'));
    assert.ok(template.indexOf('PROJECT CONTEXT') < template.indexOf('id="np-data-field"'));
    assert.ok(template.indexOf('id="np-data-field"') < template.indexOf('ADVANCED SETTINGS'));
    assert.ok(template.indexOf('ADVANCED SETTINGS') < template.indexOf('id="np-project-footer"'));

    const primaryFields = template.slice(
        template.indexOf('RESEARCH OBJECTIVE'),
        template.indexOf('id="np-data-field"')
    );
    assert.doesNotMatch(primaryFields, /MODEL SELECTION|id="np-reasoning-model"/);
});

test('Advanced Settings is a collapsed accessible disclosure with decorative inset chevron', () => {
    assert.match(template, /<button type="button" class="v2-advanced-settings-toggle"[^>]*\n\s*aria-expanded="false" aria-controls="np-advanced-settings-panel">/);
    assert.match(template, /class="v2-advanced-settings-chevron" aria-hidden="true"/);
    assert.match(template, /id="np-advanced-settings-panel" hidden/);
    assert.match(projectsClient, /advancedSettingsToggle\.setAttribute\('aria-expanded', String\(isExpanded\)\)/);
    assert.match(projectsClient, /advancedSettingsPanel\.hidden = !isExpanded/);
    assert.match(projectsClient, /advancedSettingsToggle\.addEventListener\('click'/);
    assert.match(projectsClient, /advancedSettingsToggle\.addEventListener\('keydown'/);
    assert.match(projectsClient, /\['Enter', ' ', 'Spacebar'\]\.includes\(event\.key\)/);
    assert.match(projectsClient, /event\.preventDefault\(\)/);
});

test('Create and Edit reset disclosure state without leaking pending settings', () => {
    const createBlock = sourceBlock('function openCreate()', 'async function openEdit');
    const editBlock = sourceBlock('async function openEdit', 'function openDeleteConfirmation');
    assert.match(createBlock, /setAdvancedSettingsExpanded\(false\)/);
    assert.match(editBlock, /setAdvancedSettingsExpanded\(false\)/);
    assert.match(createBlock, /els\.reasoningModel\.value = els\.reasoningModel\.getAttribute\('data-default-model'\)/);
    assert.match(editBlock, /els\.reasoningModel\.value = data\.project\.reasoning_model/);
    assert.match(editBlock, /els\.executionModel\.value = data\.project\.execution_model/);

    const disclosureBlock = sourceBlock('function setAdvancedSettingsExpanded', 'function showModelValidationError');
    const disclosureWire = sourceBlock("els.advancedSettingsToggle.addEventListener('click'", "els.selectData.addEventListener('click'");
    assert.doesNotMatch(disclosureBlock, /Model\.value|form\.reset/);
    assert.doesNotMatch(disclosureWire, /Model\.value|form\.reset/);
});

test('Create and Edit share a layout-aware modal-body scroll after expansion', () => {
    const harness = advancedSettingsHarness();
    harness.api.setAdvancedSettingsExpanded(true);

    assert.equal(harness.toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(harness.panel.hidden, false);
    assert.equal(harness.scrollCalls.length, 0, 'layout must complete before scrolling');
    harness.flushLayout();

    assert.equal(harness.scrollCalls.length, 1);
    assert.equal(harness.scrollCalls[0].top, 198, 'only the minimum scroll needed is requested');
    assert.equal(harness.scrollCalls[0].behavior, 'smooth');

    const createBlock = sourceBlock('function openCreate()', 'async function openEdit');
    const editBlock = sourceBlock('async function openEdit', 'function openDeleteConfirmation');
    assert.match(createBlock, /setAdvancedSettingsExpanded\(false\)/);
    assert.match(editBlock, /setAdvancedSettingsExpanded\(false\)/);
    assert.doesNotMatch(
        sourceBlock('// ADVANCED_SETTINGS_SCROLL_BEGIN', '// ADVANCED_SETTINGS_SCROLL_END'),
        /window\.scrollTo|\.focus\(/
    );
});

test('visible content, collapse, and stale scheduled work do not scroll', () => {
    const visible = advancedSettingsHarness({
        targetRect: { top: 180, bottom: 300, height: 120 },
    });
    visible.api.setAdvancedSettingsExpanded(true);
    visible.flushLayout();
    assert.deepEqual(visible.scrollCalls, []);

    const rapid = advancedSettingsHarness();
    rapid.api.setAdvancedSettingsExpanded(true);
    assert.equal(rapid.pendingFrames(), 1);
    rapid.api.setAdvancedSettingsExpanded(false);
    assert.equal(rapid.pendingFrames(), 0);
    assert.equal(rapid.toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(rapid.panel.hidden, true);
    rapid.flushLayout();
    assert.deepEqual(rapid.scrollCalls, []);
});

test('reduced motion is immediate and narrow-height content aligns to its start', () => {
    const harness = advancedSettingsHarness({
        reducedMotion: true,
        targetRect: { top: 430, bottom: 850, height: 420 },
        containerRect: { top: 100, bottom: 400, height: 300 },
        clientHeight: 300,
    });
    harness.api.setAdvancedSettingsExpanded(true);
    harness.flushLayout();

    assert.equal(harness.scrollCalls.length, 1);
    assert.equal(harness.scrollCalls[0].top, 312);
    assert.equal(harness.scrollCalls[0].behavior, 'auto');
});

test('closing before layout cancels safely and keeps disclosure focus ownership', () => {
    const harness = advancedSettingsHarness();
    harness.api.setAdvancedSettingsExpanded(true);
    harness.api.cancelAdvancedSettingsScroll();
    harness.flushLayout();

    assert.deepEqual(harness.scrollCalls, []);
    assert.equal(harness.pendingFrames(), 0);
    const closeBlock = sourceBlock('function closeModal(options)', 'function setFormError');
    assert.match(closeBlock, /cancelAdvancedSettingsScroll\(\)/);
    assert.doesNotMatch(
        sourceBlock('// ADVANCED_SETTINGS_SCROLL_BEGIN', '// ADVANCED_SETTINGS_SCROLL_END'),
        /\.focus\(/
    );
});

test('all pending project settings are defaulted, hydrated, and submitted once', () => {
    assert.match(projectsClient, /reasoningModel: \$\('np-reasoning-model'\)/);
    assert.match(projectsClient, /executionModel: \$\('np-execution-model'\)/);
    assert.match(projectsClient, /els\.reasoningModel\.value = data\.project\.reasoning_model/);
    assert.match(projectsClient, /queryCount\.getAttribute\('data-default-query-count'\) \|\| '12'/);
    assert.match(projectsClient, /if \(!queryCount \|\| !\/\^\\d\+\$\//);
    assert.equal((projectsClient.match(/body\.append\('reasoning_model', reasoningModel\)/g) || []).length, 1);
    assert.equal((projectsClient.match(/body\.append\('execution_model', executionModel\)/g) || []).length, 1);
    assert.equal((projectsClient.match(/body\.append\('query_count', queryCount\)/g) || []).length, 1);
    assert.match(projectsClient, /!\/\^\\d\+\$\/\.test\(queryCount\)/);
    assert.match(projectsClient, /Number\(queryCount\) < 1/);
    assert.match(projectsClient, /Number\(queryCount\) > 50/);
    assert.match(projectsClient, /queryCount\.addEventListener\('invalid'/);
    assert.equal((projectsClient.match(/body\.append\('deep_research'/g) || []).length, 1);
    assert.equal((projectsClient.match(/body\.append\('web_search'/g) || []).length, 1);
});

test('Project Location stays pending until outer Save and uses the native chooser contract', () => {
    assert.match(template, />PROJECT LOCATION<\/h3>/);
    assert.match(template, /Choose where DiscoveryAgent stores this project&rsquo;s rounds, code, reports, memory, and generated files\./);
    assert.match(template, /id="np-project-location-path"/);
    assert.match(template, /class="v2-btn v2-btn-secondary v2-compact-secondary" id="np-choose-location">Choose Location<\/button>/);
    assert.match(template, /class="v2-btn v2-btn-secondary v2-compact-secondary" id="np-use-default-location">Use Default<\/button>/);
    assert.match(projectsClient, /fetch\('\/api\/project-location\/choose'/);
    assert.match(projectsClient, /if \(!data\.cancelled\) \{[\s\S]*pendingProjectParent = data\.parent[\s\S]*pendingCanonicalDefaultLocation = false/);
    assert.match(projectsClient, /body\.append\('project_parent_dir', pendingProjectParent\)/);
    assert.match(projectsClient, /body\.append\('project_id', els\.form\.dataset\.pendingProjectId\)/);
    assert.doesNotMatch(projectsClient, /window\.(?:confirm|alert)\s*\(/);
    assert.match(projectsClient, /openRelocationConfirmation\(body, destination\)/);
    assert.match(projectsClient, /body\.append\('confirm_project_relocation', 'true'\)/);
    const chooserBlock = sourceBlock('async function chooseProjectLocation()', 'function resetSubmitState');
    assert.doesNotMatch(chooserBlock, /body\.append|form\.submit|\/api\/projects/);
});

test('Project Location actions share the compact Back to Projects treatment', () => {
    assert.match(baseTemplate, /class="v2-appbar-back v2-compact-secondary"[^>]*>[^<]*Back to Projects<\/a>/);
    const compactCss = style.slice(
        style.indexOf('.v2-compact-secondary {'),
        style.indexOf('/* ---- Modal ---- */', style.indexOf('.v2-compact-secondary {'))
    );
    assert.match(compactCss, /padding: 7px 14px/);
    assert.match(compactCss, /border: 1px solid var\(--v2-border\)/);
    assert.match(compactCss, /border-radius: 8px/);
    assert.match(compactCss, /background: var\(--v2-surface\)/);
    assert.match(compactCss, /font: 400 0\.86rem\/1\.6 var\(--v2-sans\)/);
    assert.match(compactCss, /\.v2-compact-secondary:hover/);
    assert.match(compactCss, /\.v2-compact-secondary:focus-visible/);
    assert.match(compactCss, /\.v2-btn\.v2-compact-secondary:disabled/);
    assert.match(compactCss, /@media \(max-width: 680px\)[\s\S]*padding: 6px 10px/);
    assert.match(style, /\.v2-project-location-actions \{[^}]*display: flex;[^}]*flex-wrap: wrap;[^}]*gap: 8px/s);
    assert.match(style, /\.v2-btn:disabled[^}]*pointer-events: none/);
});

test('model validation expands Advanced Settings and focuses the invalid field', () => {
    const validationBlock = sourceBlock('function showModelValidationError', 'function resetSubmitState');
    assert.match(validationBlock, /setAdvancedSettingsExpanded\(true\)/);
    assert.match(validationBlock, /els\.reasoningModel\.classList\.add\('v2-input-invalid'\)/);
    assert.match(validationBlock, /setFormError\(/);
    assert.match(validationBlock, /els\.reasoningModel\.focus\(\{ preventScroll: true \}\)/);
    assert.match(projectsClient, /data\.code === 'unsupported_project_model'/);
    assert.match(projectsClient, /data\.project\.project_models_valid === false/);
});


test('Query Count validation expands Advanced Settings, scrolls, and focuses the field', () => {
    const validationBlock = sourceBlock(
        'function showProjectSettingsValidationError',
        'function projectDestination'
    );
    assert.match(validationBlock, /setAdvancedSettingsExpanded\(true\)/);
    assert.match(validationBlock, /\/query count\/i\.test[^?]*\? els\.queryCount/);
    assert.match(validationBlock, /target\.classList\.add\('v2-input-invalid'\)/);
    assert.match(validationBlock, /target\.focus\(\{ preventScroll: true \}\)/);
});

test('Advanced Settings and model select keep comfortable chevron insets without overflow', () => {
    const disclosureCss = style.slice(
        style.indexOf('.v2-advanced-settings {'),
        style.indexOf('.v2-data-tree,', style.indexOf('.v2-advanced-settings {'))
    );
    const selectCss = style.slice(
        style.indexOf('.v2-select {'),
        style.indexOf('.v2-checkbox-row', style.indexOf('.v2-select {'))
    );
    const advancedControlCss = style.slice(
        style.indexOf('.v2-advanced-control {'),
        style.indexOf('.v2-select {', style.indexOf('.v2-advanced-control {'))
    );
    assert.match(disclosureCss, /width: 100%/);
    assert.match(disclosureCss, /min-width: 0/);
    assert.match(disclosureCss, /padding: 12px 18px/);
    assert.match(disclosureCss, /justify-content: space-between/);
    assert.match(disclosureCss, /focus-visible/);
    assert.doesNotMatch(disclosureCss, /margin-right:\s*-/);
    assert.match(selectCss, /background-position: right 18px center/);
    assert.match(selectCss, /padding-right: 48px/);
    assert.match(advancedControlCss, /width: 100%/);
    assert.match(advancedControlCss, /min-width: 0/);
    assert.match(advancedControlCss, /:hover/);
    assert.match(advancedControlCss, /:disabled/);
    assert.equal((template.match(/class="v2-input v2-advanced-control/g) || []).length, 3);
    assert.match(style, /\.v2-advanced-model-field > \.v2-field-help\s*\{[^}]*margin-bottom:\s*10px/s);
    assert.match(baseTemplate, /style\.css'\) \}\}\?v=20260908-round-launch-settings-1/);
    assert.match(template, /projects\.js'\) \}\}\?v=20260908-data-files-project-draft-1/);
});

test('Round Start offers validated per-round model and research overrides', () => {
    const launchTemplate = fs.readFileSync(path.join(ROOT, 'da_selection_ui/templates/_round_launch_dialog.html'), 'utf8');
    assert.match(launchTemplate, /id="round-launch-reasoning-model"/);
    assert.match(launchTemplate, /id="round-launch-execution-model"/);
    assert.match(launchTemplate, /id="round-launch-queries"/);
    assert.match(launchTemplate, /id="round-launch-deep-research"/);
    assert.match(launchTemplate, /id="round-launch-web-search"/);
    assert.match(roundStart, /\/api\/rounds\/options\?project_id=/);
    assert.match(roundStart, /reasoning_model:\s*els\.reasoningModel\.value/);
    assert.match(roundStart, /execution_model:\s*els\.executionModel\.value/);
});

test('Research Tools align each native checkbox with its title row', () => {
    const toolsMarkup = template.slice(
        template.indexOf('>RESEARCH TOOLS</h3>'),
        template.indexOf('v2-advanced-model-field'),
    );
    const checkboxCss = style.slice(
        style.indexOf('.v2-checkbox-row {'),
        style.indexOf('/* ---- Modal footer ---- */'),
    );

    assert.equal((toolsMarkup.match(/<label class="v2-checkbox-row"/g) || []).length, 2);
    assert.match(toolsMarkup, /<label class="v2-checkbox-row" for="np-deep-research">\s*<input type="checkbox" id="np-deep-research" checked>\s*<span class="v2-checkbox-label">Deep Research<\/span>\s*<span class="v2-checkbox-hint">Allow DiscoveryAgent to use its configured deep-research provider\.<\/span>\s*<\/label>/s);
    assert.match(toolsMarkup, /<label class="v2-checkbox-row" for="np-web-search">\s*<input type="checkbox" id="np-web-search" checked>\s*<span class="v2-checkbox-label">Web Search<\/span>\s*<span class="v2-checkbox-hint">Allow DiscoveryAgent to search the web during research\.<\/span>\s*<\/label>/s);
    assert.match(checkboxCss, /\.v2-checkbox-row\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*auto minmax\(0, 1fr\)[^}]*grid-template-rows:\s*auto auto[^}]*column-gap:\s*10px[^}]*row-gap:\s*2px[^}]*align-items:\s*start/s);
    assert.match(checkboxCss, /\.v2-checkbox-row input\s*\{[^}]*grid-column:\s*1[^}]*grid-row:\s*1[^}]*align-self:\s*center[^}]*margin:\s*0/s);
    assert.match(checkboxCss, /\.v2-checkbox-label\s*\{[^}]*grid-column:\s*2[^}]*grid-row:\s*1[^}]*min-width:\s*0/s);
    assert.match(checkboxCss, /\.v2-checkbox-hint\s*\{[^}]*grid-column:\s*2[^}]*grid-row:\s*2[^}]*min-width:\s*0[^}]*margin:\s*0/s);
    assert.doesNotMatch(checkboxCss, /transform:|position:\s*(?:absolute|relative)|margin-top:/);
    assert.match(projectsClient, /body\.append\('deep_research', String\(els\.deepResearch\.checked\)\)/);
    assert.match(projectsClient, /body\.append\('web_search', String\(els\.webSearch\.checked\)\)/);
});
