const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..', 'da_selection_ui');
const projectsSource = fs.readFileSync(path.join(root, 'static', 'projects.js'), 'utf8');
const librarySource = fs.readFileSync(path.join(root, 'static', 'data_library.js'), 'utf8');
const treeSource = fs.readFileSync(path.join(root, 'static', 'data_tree.js'), 'utf8');
const pickerSource = fs.readFileSync(path.join(root, 'static', 'data_picker.js'), 'utf8');
const styleSource = fs.readFileSync(path.join(root, 'static', 'style.css'), 'utf8');
const projectsTemplate = fs.readFileSync(path.join(root, 'templates', 'projects.html'), 'utf8');
const dataTemplate = fs.readFileSync(path.join(root, 'templates', 'data_library.html'), 'utf8');
const baseTemplate = fs.readFileSync(path.join(root, 'templates', 'base.html'), 'utf8');

test('global sidebar links Projects then Data with route-owned active states', () => {
    assert.match(projectsTemplate, /<h1 class="v2-title v2-app-page-title">Projects<\/h1>/);
    assert.doesNotMatch(projectsTemplate, /id="data-library-box"|id="data-library-list"/);
    assert.ok(baseTemplate.indexOf('>Projects</a>') < baseTemplate.indexOf('>Data</a>'));
    assert.match(baseTemplate, /class="v2-workspace-sidebar-list v2-global-nav" aria-label="DiscoveryAgent sections"/);
    assert.match(baseTemplate, /href="\{\{ url_for\('projects'\) \}\}"/);
    assert.match(baseTemplate, /href="\{\{ url_for\('discoveryagent_data'\) \}\}"/);
    assert.match(baseTemplate, /global_nav_active == 'projects'[\s\S]*aria-current="page"/);
    assert.match(baseTemplate, /global_nav_active == 'data'[\s\S]*aria-current="page"/);
    assert.match(styleSource, /\.v2-global-sidebar[\s\S]*\.v2-global-content/);
    assert.match(styleSource, /\.v2-workspace-sidebar-item:focus-visible/);
    assert.match(styleSource, /\.v2-global-shell\s*\{[^}]*display:\s*flex[^}]*min-width:\s*0/s);
    assert.match(styleSource, /\.v2-page-data \.v2-global-content\s*\{[^}]*min-width:\s*0/s);
    assert.match(styleSource, /@media \(max-width: 580px\)[\s\S]*\.v2-global-sidebar[^}]*width:\s*140px/);
    assert.equal((baseTemplate.match(/class="v2-workspace-sidebar-list v2-global-nav"/g) || []).length, 1);
});

test('Projects and Data share centered page identity, bounded content, and one trailing primary action', () => {
    const projectsHeader = projectsTemplate.slice(
        projectsTemplate.indexOf('<header class="v2-app-page-header v2-projects-header"'),
        projectsTemplate.indexOf('</header>', projectsTemplate.indexOf('<header class="v2-app-page-header v2-projects-header"')) + 9,
    );
    const projectsContent = projectsTemplate.indexOf('id="projects-content"');
    const projectsAction = projectsTemplate.indexOf('id="projects-primary-action"');
    const dataHeader = dataTemplate.slice(
        dataTemplate.indexOf('<header class="v2-app-page-header v2-data-library-header"'),
        dataTemplate.indexOf('</header>', dataTemplate.indexOf('<header class="v2-app-page-header v2-data-library-header"')) + 9,
    );
    const dataContent = dataTemplate.indexOf('id="data-library-content"');
    const dataAction = dataTemplate.indexOf('id="data-library-primary-action"');

    assert.match(projectsHeader, /<h1 class="v2-title v2-app-page-title">Projects<\/h1>/);
    assert.match(projectsHeader, /<p class="v2-app-page-description v2-projects-description">Start new investigations or continue work across your existing research projects\.<\/p>/);
    assert.match(dataHeader, /<h1 class="v2-title v2-app-page-title" id="data-library-title">Data<\/h1>/);
    assert.match(dataHeader, /Add folders once, then choose which projects can access them\. Files stay in their original locations\./);
    assert.ok(projectsContent > projectsTemplate.indexOf('</header>') && projectsAction > projectsContent);
    assert.ok(dataContent > dataTemplate.indexOf('</header>') && dataAction > dataContent);
    assert.equal((projectsTemplate.match(/id="open-new-project"/g) || []).length, 1);
    assert.equal((dataTemplate.match(/id="data-library-add"/g) || []).length, 1);
    assert.match(projectsTemplate, /id="projects-root" data-page-scroll-root/);
    assert.match(dataTemplate, /id="data-library-root" data-page-scroll-root/);
    assert.doesNotMatch(projectsTemplate + dataTemplate, /data-overflow-action-root/);
    const projectsTray = projectsTemplate.slice(
        projectsAction,
        projectsTemplate.indexOf('</div>', projectsAction) + 6,
    );
    assert.ok(projectsTray.includes('{{ new_project_button() }}'));
    assert.ok(projectsTemplate.includes("{% macro new_project_button(label='New Project') -%}"));
    assert.match(projectsTemplate, /class="v2-btn v2-btn-primary v2-new-project-btn" id="open-new-project"/);
    assert.match(projectsTemplate, /<span class="v2-plus" aria-hidden="true">\+<\/span> \{\{ label \}\}/);
    assert.match(dataTemplate, /class="v2-app-page-action v2-sticky-action-tray" id="data-library-primary-action" hidden>[\s\S]*v2-btn v2-btn-primary[\s\S]*<span class="v2-plus" aria-hidden="true">\+<\/span> Add Data/);
    assert.doesNotMatch(projectsTemplate, /open-new-project-empty|v2-projects-title-row/);
    assert.doesNotMatch(dataTemplate, /v2-data-library-header-row|Add Data<\/span>[\s\S]*&#8599;/);

    assert.match(styleSource, /\.v2-app-page\s*\{[^}]*max-width:\s*780px[^}]*margin:\s*0 auto/s);
    assert.match(styleSource, /\.v2-app-page-header\s*\{[^}]*text-align:\s*center/s);
    assert.match(styleSource, /\.v2-app-page-title\s*\{[^}]*margin:\s*0[^}]*text-align:\s*center/s);
    assert.match(styleSource, /\.v2-app-page-description\s*\{[^}]*max-width:\s*100%[^}]*margin:\s*10px auto 0[^}]*text-align:\s*center/s);
    assert.match(styleSource, /\.v2-app-page-content\s*\{[^}]*width:\s*100%[^}]*text-align:\s*left/s);
    assert.match(styleSource, /\.v2-app-page-action\s*\{[^}]*position:\s*relative[^}]*justify-content:\s*center[^}]*width:\s*100%[^}]*margin:\s*0/s);
    assert.doesNotMatch(styleSource, /\.v2-projects \.v2-app-page-action:not\(\.is-sticky\)/);
    assert.doesNotMatch(styleSource.slice(styleSource.indexOf('.v2-app-page {'), styleSource.indexOf('/* ---- Project list ---- */')), /min-height:/);
    assert.match(projectsTemplate, /app_page_action\.js'\) \}\}\?v=20260822-sticky-action-tray-1/);
    assert.match(dataTemplate, /app_page_action\.js'\) \}\}\?v=20260822-sticky-action-tray-1/);
    assert.match(baseTemplate, /style\.css'\) \}\}\?v=20260823-active-round-skill-import-1/);
});

test('Data keeps the empty dropzone as its sole empty action and shows one solid action after populated content', () => {
    const headerStart = dataTemplate.indexOf('<header class="v2-app-page-header v2-data-library-header">');
    const contentStart = dataTemplate.indexOf('id="data-library-content"');
    const emptyStart = dataTemplate.indexOf('id="data-library-empty"');
    const boxStart = dataTemplate.indexOf('id="data-library-box"');
    const actionStart = dataTemplate.indexOf('id="data-library-primary-action"');
    const sectionEnd = dataTemplate.indexOf('</section>');

    assert.ok(headerStart < contentStart && contentStart < emptyStart && emptyStart < boxStart && boxStart < actionStart && actionStart < sectionEnd);
    assert.match(dataTemplate, /<button type="button" class="v2-dropzone v2-data-empty-state v2-data-library-empty"\s+id="data-library-empty"/);
    assert.match(dataTemplate, /<div class="v2-app-page-action v2-sticky-action-tray" id="data-library-primary-action" hidden>[\s\S]*<button type="button" class="v2-btn v2-btn-primary v2-data-library-add-btn" id="data-library-add">/);
    assert.equal((dataTemplate.match(/id="data-library-add"/g) || []).length, 1);
    assert.doesNotMatch(dataTemplate, /Browse on Mac|v2-data-library-action-row|v2-data-library-header-row/);
    assert.match(librarySource, /const empty = sources\.length === 0;[\s\S]*els\.primaryAction\.hidden = empty;[\s\S]*els\.empty\.hidden = !empty/);
    assert.match(librarySource, /function dataLibraryPrimaryControl\(\) \{[\s\S]*sources\.length > 0 && !els\.primaryAction\.hidden \? els\.add : els\.empty/);
    assert.match(librarySource, /els\.empty\.addEventListener\('click',[\s\S]*void add\(els\.empty\)/);
    assert.match(librarySource, /els\.add\.addEventListener\('click',[\s\S]*void add\(els\.add\)/);
    assert.match(styleSource, /body\.v2\s*\{[^}]*background:\s*var\(--v2-bg\)/s);
    const trayRule = styleSource.match(/\.v2-sticky-action-tray\s*\{([^}]*)\}/s);
    const stickyTrayRule = styleSource.match(/\[data-page-scroll-root\] > \.v2-sticky-action-tray\.is-sticky\s*\{([^}]*)\}/s);
    assert.ok(trayRule);
    assert.ok(stickyTrayRule);
    assert.match(trayRule[1], /padding:\s*10px 12px calc\(10px \+ env\(safe-area-inset-bottom\)\)/);
    assert.match(trayRule[1], /background:\s*var\(--v2-bg\)/);
    assert.match(stickyTrayRule[1], /position:\s*sticky/);
    assert.match(stickyTrayRule[1], /bottom:\s*0/);
    assert.doesNotMatch(trayRule[1] + stickyTrayRule[1], /border|box-shadow|outline|gradient|filter|transparent|var\(--v2-surface\)/);
    assert.doesNotMatch(styleSource, /\.v2-sticky-action-tray(?:::before|::after)/);
    assert.match(styleSource, /\.v2-btn-primary\s*\{[^}]*background:\s*var\(--v2-accent\)/s);
    assert.match(styleSource, /\.v2-app-page\.has-sticky-action\s*\{[^}]*scroll-padding-bottom:\s*var\(--v2-sticky-action-reserve, 0px\)/s);
    const stickyRootRule = styleSource.match(/\.v2-app-page\.has-sticky-action\s*\{([^}]*)\}/s);
    assert.ok(stickyRootRule);
    assert.doesNotMatch(stickyRootRule[1], /(?:^|[;\s])padding-bottom\s*:|(?:^|[;\s])min-height\s*:/);
});

test('bounded action trays leave only a compact page ending and preserve document scrolling', () => {
    assert.match(styleSource, /\.v2-page-projects \.v2-global-content,\s*\.v2-page-data \.v2-global-content\s*\{[^}]*padding:\s*56px 28px 24px/s);
    assert.match(styleSource, /\.v2-data-library \{ padding-bottom: 0; \}/);
    assert.match(styleSource, /@media \(max-width: 820px\)[\s\S]*\.v2-page-data \.v2-global-content \{ padding:\s*40px 18px 24px; \}/);
    assert.doesNotMatch(styleSource, /\.v2-page-(?:projects|data) \.v2-global-content[\s\S]{0,300}padding:[^;]*(?:90px|120px)/);
    assert.doesNotMatch(styleSource, /\.v2-app-page(?:\.has-sticky-action)?\s*\{[^}]*min-height:\s*100(?:d)?vh/s);
    assert.match(styleSource, /\.v2-page-data \.v2-data-library-box > \.v2-data-tree-header\s*\{[^}]*position:\s*sticky/s);
    assert.match(styleSource, /\.v2-page-data \.v2-data-library-content,[\s\S]*overflow:\s*visible/s);
    assert.equal((projectsTemplate.match(/id="open-new-project"/g) || []).length, 1);
    assert.equal((dataTemplate.match(/id="data-library-add"/g) || []).length, 1);
});


test('registry render immediately switches between the sole empty control and populated Add Data action', () => {
    const renderLogic = librarySource.slice(
        librarySource.indexOf('function dataLibraryPrimaryControl()'),
        librarySource.indexOf('async function load()'),
    );
    const renderState = Function('inputSources', [
        "let sources = inputSources;",
        "const appended = [];",
        "const els = { loading: { hidden: false }, error: { hidden: false }, " +
            "add: { hidden: false }, primaryAction: { hidden: true }, empty: { hidden: true }, box: { hidden: false }, " +
            "list: { hidden: false, replaceChildren() { appended.length = 0; }, " +
            "appendChild(value) { appended.push(value); } } };",
        "function renderSource(source) { return source.id; }",
        "const dataTree = { renderSources(list, entries) { list.replaceChildren(); entries.forEach((entry) => list.appendChild(renderSource(entry))); } };",
        "const pageActionLayout = null;",
        renderLogic,
        "render(); return { actionHidden: els.primaryAction.hidden, emptyHidden: els.empty.hidden, " +
            "boxHidden: els.box.hidden, listHidden: els.list.hidden, appended, " +
            "primary: dataLibraryPrimaryControl() === els.add ? 'add' : 'empty' };",
    ].join(String.fromCharCode(10)));

    assert.deepEqual(renderState([]), {
        actionHidden: true, emptyHidden: false, boxHidden: true, listHidden: true,
        appended: [], primary: 'empty',
    });
    assert.deepEqual(renderState([{ id: 'source-a' }]), {
        actionHidden: false, emptyHidden: true, boxHidden: false, listHidden: false,
        appended: ['source-a'], primary: 'add',
    });
});

test("shared tree owns one Name and Size schema in management, preview, and selection modes", () => {
    assert.equal((treeSource.match(/node\(\x27div\x27, \x27v2-data-tree-header\x27\)/g) || []).length, 1);
    assert.match(treeSource, /node\(\x27span\x27, \x27v2-data-tree-header-name\x27, \x27Name\x27\)/);
    assert.match(treeSource, /node\(\x27span\x27, \x27v2-data-tree-header-size\x27, \x27Size\x27\)/);
    assert.match(treeSource, /function renderSources\(list, entries, renderEntry\)/);
    assert.match(librarySource, /dataTree\.renderSources\(els\.list, sources\)/);
    assert.match(projectsSource, /projectDataTree\.renderSources\(els\.selectedDataTree, empty \? \[\] : selectedDataSources, renderSelectedPreviewSource\)/);
    assert.match(projectsSource, /selectorDataTree\.renderSources\(els\.dataLibrarySources, librarySources\)/);
    assert.match(treeSource, /const size = sizeCell\(child\.size_bytes\)/);
    assert.equal((treeSource.match(/sizeCell\(null\)/g) || []).length, 2);
    assert.match(treeSource, /labelItemSize\(item, child\.name, size\.label\)/);
    assert.match(styleSource, /\.v2-data-tree-header,\s*\.v2-data-source-row,\s*\.v2-data-tree-row\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:[^}]*var\(--v2-data-tree-size-column\)[^}]*var\(--v2-data-tree-action-column\)/s);
    assert.match(styleSource, /\.v2-data-tree-header-name \{ grid-column: 4; min-width: 0; text-align: left; \}/);
    assert.match(styleSource, /\.v2-data-tree-header-size \{ grid-column: 5; padding-right: 4px; text-align: right; \}/);
    assert.match(styleSource, /\.v2-data-source-remove \{ grid-column: 6;/);
    assert.match(styleSource, /@media \(max-width: 520px\)[\s\S]*\.v2-data-tree-header-size,[\s\S]*\.v2-data-tree-size \{ display: none; \}/);
    assert.ok(treeSource.includes('item.setAttribute("aria-label", name + ". Size " + sizeLabel + ".");'));
    const sharedRowsStart = styleSource.indexOf(".v2-data-tree-header,");
    const headerStart = styleSource.indexOf(".v2-data-tree-header {", sharedRowsStart);
    const headerStyle = styleSource.slice(headerStart, styleSource.indexOf(".v2-data-tree-header[hidden]", headerStart));
    assert.doesNotMatch(headerStyle, /position:\s*sticky/);
});

test("Data alone gives its shared Name and Size header a tree-bounded sticky surface", () => {
    assert.match(styleSource, /\.v2-page-data \.v2-data-library-content,\s*\.v2-page-data \.v2-data-library-box,\s*\.v2-page-data \.v2-data-source-list\s*\{[^}]*max-height:\s*none[^}]*overflow:\s*visible/s);
    assert.match(styleSource, /\.v2-page-data \.v2-data-library-box > \.v2-data-tree-header\s*\{[^}]*position:\s*sticky[^}]*top:\s*var\(--v2-appbar-offset\)[^}]*z-index:\s*7[^}]*background:\s*var\(--v2-surface\)/s);
    assert.match(styleSource, /--v2-appbar-offset:\s*61px/);
    assert.match(styleSource, /\.v2-global-shell\s*\{[^}]*100dvh/s);
    assert.match(styleSource, /\.v2-data-selector-browser\s*\{[^}]*overflow-y:\s*auto/s);
    assert.doesNotMatch(styleSource, /\.v2-page-data[\s\S]{0,300}overflow-y:\s*auto/);
    assert.doesNotMatch(projectsTemplate, /v2-page-data/);
    assert.doesNotMatch(styleSource, /\.v2-projects-header\s*\{[^}]*position:\s*sticky/s);
    assert.doesNotMatch(styleSource, /\.v2-project-row\s*\{[^}]*position:\s*sticky/s);
});

test("shared rows keep selection controls spaced and active surfaces continuous through Size", () => {
    assert.match(styleSource, /--v2-data-tree-disclosure-column:\s*24px/);
    assert.match(styleSource, /\.v2-data-tree-selection-mode \{ --v2-data-tree-checkbox-column: 24px; \}/);
    assert.match(styleSource, /--v2-data-tree-glyph-column:\s*22px/);
    assert.match(styleSource, /--v2-data-tree-caret-checkbox-gap:\s*7px/);
    assert.match(styleSource, /--v2-data-tree-checkbox-icon-gap:\s*10px/);
    assert.match(styleSource, /--v2-data-tree-icon-name-gap:\s*8px/);
    assert.match(styleSource, /--v2-data-tree-size-column:\s*84px/);
    assert.match(styleSource, /grid-template-columns:[\s\S]*calc\(var\(--v2-data-tree-disclosure-column\) [+] var\(--v2-data-tree-indent, 0px\)\)[\s\S]*var\(--v2-data-tree-checkbox-column\)[\s\S]*var\(--v2-data-tree-glyph-column\)[\s\S]*minmax\(0, 1fr\)[\s\S]*var\(--v2-data-tree-size-column\)[\s\S]*var\(--v2-data-tree-action-column\)/);
    assert.match(styleSource, /\.v2-data-tree-checkbox-slot\s*\{[^}]*box-sizing:\s*border-box[^}]*display:\s*flex[^}]*grid-column:\s*2[^}]*align-self:\s*stretch[^}]*align-items:\s*center[^}]*justify-content:\s*flex-start[^}]*width:\s*100%[^}]*min-height:\s*24px[^}]*padding-right:\s*var\(--v2-data-tree-checkbox-icon-gap\)/s);
    assert.match(styleSource, /\.v2-data-tree-checkbox\s*\{[^}]*flex:\s*0 0 14px[^}]*width:\s*14px[^}]*height:\s*14px[^}]*margin:\s*0/s);
    assert.match(styleSource, /\.v2-data-source-disclosure,[\s\S]*\.v2-data-tree-disclosure \{[^}]*width:\s*var\(--v2-data-tree-disclosure-column\)[^}]*padding:\s*0 var\(--v2-data-tree-caret-checkbox-gap\)/s);
    assert.match(styleSource, /\.v2-data-source-row > \.v2-data-tree-glyph-folder,[\s\S]*\.v2-data-tree-row > \.v2-data-tree-glyph-file \{[^}]*justify-self:\s*stretch[^}]*padding-right:\s*var\(--v2-data-tree-icon-name-gap\)/s);
    assert.match(styleSource, /\.v2-data-tree-chevron-spacer\s*\{[^}]*grid-column:\s*1[^}]*justify-self:\s*end[^}]*width:\s*var\(--v2-data-tree-disclosure-column\)[^}]*height:\s*28px/s);
    assert.match(styleSource, /\.v2-data-tree-header,[\s\S]*\.v2-data-tree-row\s*\{[^}]*align-items:\s*center/s);
    assert.match(styleSource, /\.v2-data-tree-header-name \{ grid-column: 4; min-width: 0; text-align: left; \}/);
    assert.match(treeSource, /viewBox="0 0 12 12" width="10" height="10"/);
    assert.match(treeSource, /viewBox="0 0 16 16" width="14" height="14"/);
    assert.match(styleSource, /\.v2-data-source-row:hover,[\s\S]*\.v2-data-tree-row:hover,[\s\S]*\.v2-data-source-row:focus-within,[\s\S]*\.v2-data-tree-row:focus-within,[\s\S]*background:\s*var\(--v2-accent-soft\)/s);
    assert.match(styleSource, /\.v2-data-tree-size \{[^}]*grid-column:\s*5[^}]*padding-right:\s*4px[^}]*text-align:\s*right/s);
    assert.match(styleSource, /\.v2-data-source-remove \{ grid-column: 6;/);
    assert.match(styleSource, /\.v2-data-tree-header,[\s\S]*\.v2-data-tree-row \{[^}]*max-width:\s*100%[^}]*column-gap:\s*0/s);
    assert.match(treeSource, /row\.style\.setProperty\('--v2-data-tree-indent',[\s\S]*const size = sizeCell/);
    assert.match(treeSource, /const slot = node\('label', 'v2-data-tree-checkbox-slot'\)/);
    assert.match(treeSource, /checkbox\.addEventListener\('click', \(event\) => event\.stopPropagation\(\)\)/);
    assert.match(treeSource, /slot\.addEventListener\('click', \(event\) => event\.stopPropagation\(\)\)/);
    assert.match(treeSource, /if \(sizeBytes == null\) return "--"/);
});

test("shared file-size formatter is deterministic from bytes through gigabytes", () => {
    const context = { window: {} };
    vm.runInNewContext(treeSource, context);
    const format = context.window.DiscoveryAgentDataTree.formatSize;
    assert.equal(format(null), "--");
    assert.equal(format(842), "842 bytes");
    assert.equal(format(1), "1 byte");
    assert.equal(format(2458), "2.4 KB");
    assert.equal(format(Math.round(18.7 * 1024 * 1024)), "18.7 MB");
    assert.equal(format(Math.round(1.1 * 1024 * 1024 * 1024)), "1.1 GB");
});

test('main library delegates compact lazy rendering and only top-level removal to the shared tree', () => {
    assert.match(dataTemplate, /id="data-library-list" role="tree"/);
    assert.match(librarySource, /\/tree\?path=/);
    assert.match(librarySource, /window\.DiscoveryAgentDataTree\.createRenderer/);
    assert.match(librarySource, /fetchChildren:\s*fetchTreeChildren/);
    assert.match(librarySource, /onRemove:\s*\(source, button\) => openRemove\(source, button\)/);
    const managementOptions = librarySource.slice(librarySource.indexOf('const dataTree ='), librarySource.indexOf('function renderSource'));
    assert.doesNotMatch(managementOptions, /selection:\s*\{/);
    assert.match(treeSource, /data\.next_cursor/);
    assert.match(treeSource, /'Load more'/);
    assert.match(treeSource, /glyph\('folder'\)/);
    assert.match(treeSource, /glyph\('file'\)/);
    assert.match(treeSource, /setAttribute\('aria-expanded'/);
    const childRenderer = treeSource.slice(treeSource.indexOf('function renderChild'), treeSource.indexOf('async function loadChildren'));
    assert.doesNotMatch(childRenderer, /onRemove|v2-data-source-remove/);
    assert.doesNotMatch(librarySource, /Relink|source\.available|source\.availability|v2-data-source-status|v2-data-source-path/);
    assert.doesNotMatch(librarySource, /FileReader|readAsText|readAsArrayBuffer/);
});
test('shared renderer owns canonical chevrons, icons, rows, and independent child groups', () => {
    const childRenderer = treeSource.slice(treeSource.indexOf('function renderChild'), treeSource.indexOf('async function loadChildren'));
    const sourceRenderer = treeSource.slice(treeSource.indexOf('function renderSource'), treeSource.indexOf('return { renderSource'));
    assert.match(childRenderer, /const row = node\('div', 'v2-data-tree-row v2-data-tree-folder-row'\)/);
    assert.match(childRenderer, /const disclosure = node\('button', 'v2-data-tree-disclosure'\)/);
    assert.match(childRenderer, /disclosure\.append\(glyph\('chevron'\)\)/);
    assert.match(childRenderer, /row\.appendChild\(disclosure\)/);
    assert.match(childRenderer, /if \(selection\) row\.appendChild\(createSelectionCheckbox/);
    assert.match(childRenderer, /row\.append\(glyph\('folder'\), node\('span', 'v2-data-tree-name', child\.name\), size\.value\)/);
    assert.match(childRenderer, /item\.append\(row, children\)/);
    assert.match(childRenderer, /children\.setAttribute\('role', 'group'\)/);
    assert.match(sourceRenderer, /disclosure\.append\(glyph\('chevron'\)\)/);
    assert.match(sourceRenderer, /row\.appendChild\(disclosure\)/);
    assert.match(sourceRenderer, /if \(selection\) row\.appendChild\(createSelectionCheckbox/);
    assert.match(sourceRenderer, /row\.append\(glyph\('folder'\), node\('span', 'v2-data-source-name', source\.display_name\), sourceSize\.value\)/);
    assert.match(styleSource, /\.v2-data-source-name\s*\{[^}]*grid-column:\s*4[^}]*min-width:\s*0/s);
});
test('shared folder rows toggle once from row, caret, name, or keyboard while X is isolated', () => {
    const wire = treeSource.slice(treeSource.indexOf('function wireDirectory'), treeSource.indexOf('function renderChild'));
    const sourceRenderer = treeSource.slice(treeSource.indexOf('function renderSource'), treeSource.indexOf('return { renderSource'));
    assert.equal((wire.match(/row\.addEventListener\('click'/g) || []).length, 1);
    assert.doesNotMatch(wire, /button\.addEventListener\('click'/);
    assert.match(wire, /item\.tabIndex = 0/);
    assert.match(wire, /event\.target !== item \|\| !\['Enter', ' ', 'Spacebar'\]\.includes\(event\.key\)/);
    assert.match(wire, /event\.preventDefault\(\)[\s\S]*return toggle\(\)/);
    assert.match(wire, /setDisclosure\(button, item, opening, name\)/);
    assert.match(sourceRenderer, /remove\.addEventListener\('click',[\s\S]*event\.stopPropagation\(\)[\s\S]*options\.onRemove\(entry, remove\)/s);
    assert.match(styleSource, /\.v2-data-source-row,\s*\.v2-data-tree-folder-row\s*\{\s*cursor:\s*pointer;\s*\}/s);
    assert.match(styleSource, /\.v2-data-source:focus-visible > \.v2-data-source-row,[\s\S]*\.v2-data-tree-item:focus-visible > \.v2-data-tree-row/s);
    assert.match(dataTemplate, /data_tree\.js'\) \}\}\?v=20260822-data-tree-row-geometry-1/);
    assert.match(projectsTemplate, /data_tree\.js'\) \}\}\?v=20260822-data-tree-row-geometry-1/);
});
test('dedicated Data page uses the compact shared Skill dropzone empty state', () => {
    assert.match(dataTemplate, /<h1[^>]*id="data-library-title">Data<\/h1>/);
    assert.match(dataTemplate, /Add folders once, then choose which projects can access them\. Files stay in their original locations\./);
    assert.match(dataTemplate, /aria-label="No data added\. Click to add data\."/);
    assert.match(dataTemplate, /class="v2-dropzone-text">No data added<\/span>/);
    assert.match(dataTemplate, /class="v2-dropzone-action">Click to add data<\/span>/);
    assert.doesNotMatch(dataTemplate, /Choose a user-supplied input folder|DiscoveryAgent stores only its location/);
    assert.doesNotMatch(dataTemplate, />Drop files|>Drop folders|Relink|Available|Unavailable/i);
    assert.match(styleSource, /\.v2-dropzone\s*\{[^}]*border:\s*1\.5px dashed var\(--v2-border\)[^}]*border-radius:\s*12px[^}]*background:\s*var\(--v2-bg\)/s);
    assert.match(styleSource, /\.v2-data-empty-state\s*\{[^}]*display:\s*grid[^}]*gap:\s*6px[^}]*padding:\s*22px 18px/s);
    assert.match(styleSource, /\.v2-data-empty-state > \.v2-dropzone-icon,[\s\S]*margin:\s*0/);
    assert.doesNotMatch(styleSource, /\.v2-data-library-box\.is-empty|v2-data-library-empty-icon|v2-data-library-empty-copy/);
});

test('empty library box has button semantics and keyboard picker activation', () => {
    const emptyStart = dataTemplate.indexOf('<button type="button" class="v2-dropzone v2-data-empty-state');
    const emptyMarkup = dataTemplate.slice(emptyStart, dataTemplate.indexOf('</button>', emptyStart) + 9);
    assert.match(emptyMarkup, /id="data-library-empty" aria-label="No data added\. Click to add data\."/);
    assert.equal((emptyMarkup.match(/<button\b/g) || []).length, 1);
    assert.doesNotMatch(emptyMarkup, /<a\b|tabindex=|role="button"/);
    assert.match(librarySource, /els\.empty\.addEventListener\('click', \(event\) => \{ event\.preventDefault\(\); void add\(els\.empty\); \}\)/);
    assert.doesNotMatch(librarySource, /event\.key !== 'Enter' && event\.key !== ' '|setAttribute\('role', 'button'\)/);
    assert.match(styleSource, /\.v2-dropzone-action\s*\{[^}]*color:\s*var\(--v2-accent\)[^}]*text-decoration:\s*underline/s);
});

test('empty box and external Add Data action share one batch picker flow', () => {
    const addFlow = librarySource.slice(
        librarySource.indexOf('async function add(trigger)'),
        librarySource.indexOf('function openRemove'),
    );
    assert.match(librarySource, /els\.add\.addEventListener\('click', \(event\) => \{ event\.preventDefault\(\); void add\(els\.add\); \}\)/);
    assert.match(librarySource, /els\.empty\.addEventListener\('click', \(event\) => \{ event\.preventDefault\(\); void add\(els\.empty\); \}\)/);
    assert.match(addFlow, /window\.DiscoveryAgentDataPicker/);
    assert.match(addFlow, /picker\.choose\(\{ controls: \[els\.add, els\.empty\] \}\)/);
    assert.doesNotMatch(addFlow, /fetch\('/);
    assert.equal((pickerSource.match(/fetch\('\/api\/data-library\/choose'/g) || []).length, 1);
    assert.doesNotMatch(pickerSource, /project-location|chooseProjectLocation|native_project_location/);
    assert.equal((addFlow.match(/await load\(\)/g) || []).length, 1);
    assert.match(addFlow, /const added = Array\.isArray\(data\.added\)[\s\S]*const skipped = Array\.isArray\(data\.skipped\)[\s\S]*if \(added\.length \|\| skipped\.length\) await load\(\)/);
    assert.doesNotMatch(addFlow, /No folders selected/);
    assert.match(addFlow, /restoreTarget === els\.empty && els\.empty\.hidden[\s\S]*dataLibraryPrimaryControl\(\)[\s\S]*!preferredTarget\.hidden[\s\S]*dataLibraryPrimaryControl\(\)/);
    assert.match(dataTemplate, /id="data-library-feedback" role="status"[\s\S]*aria-live="polite"/);
    assert.match(librarySource, /already registered/);
    assert.match(librarySource, /not be added/);
    assert.doesNotMatch(addFlow, /forEach[\s\S]*\/api\/data-library\/choose/);
});

test('shared picker owns single-flight busy state and always clears both entry controls', () => {
    const addFlow = librarySource.slice(
        librarySource.indexOf('async function add(trigger)'),
        librarySource.indexOf('function openRemove'),
    );

    assert.match(addFlow, /if \(picker\.isPending\(\)\) return;/);
    assert.match(addFlow, /picker\.choose\(\{ controls: \[els\.add, els\.empty\] \}\)/);
    assert.match(addFlow, /catch \(error\) \{ showFeedback\(error\.message, true\); \}[\s\S]*finally \{/);
    assert.match(addFlow, /if \(data\.cancelled\) return/);
    assert.match(pickerSource, /if \(pickerPending\) return \{ ignored: true \}/);
    assert.match(pickerSource, /control\.disabled = Boolean\(busy\)/);
    assert.match(pickerSource, /setAttribute\('aria-busy', 'true'\)/);
    assert.match(pickerSource, /removeAttribute\('aria-busy'\)/);
    assert.match(pickerSource, /finally \{[\s\S]*pickerPending = false;[\s\S]*setControlsBusy\(config\.controls, false\)/);
    assert.doesNotMatch(addFlow, /document\.body[^\n]*(?:cursor|wait)|style\.cursor|classList[^\n]*wait/);
    assert.doesNotMatch(styleSource, /\.v2-data-(?:action|empty-state):disabled\s*\{[^}]*cursor:\s*wait/s);
    assert.match(styleSource, /\.v2-data-action:disabled\s*\{[^}]*cursor:\s*default/s);
    assert.match(styleSource, /\.v2-data-empty-state:disabled\s*\{[^}]*cursor:\s*default/s);
    assert.doesNotMatch(addFlow, /data-action-label|textContent\s*=\s*['"]Add Data/);
});

function dataLibraryAddHarness(result, options) {
    const config = Object.assign({ detachBeforeResult: false }, options || {});
    const addSource = librarySource.slice(
        librarySource.indexOf('async function add(trigger)'),
        librarySource.indexOf('function openRemove'),
    );
    const focusEvents = [];
    const feedback = [];
    let loadCount = 0;
    let resolvePicker;
    const pickerResult = new Promise((resolve) => { resolvePicker = resolve; });
    const empty = {
        hidden: false,
        focus() { focusEvents.push('empty'); },
    };
    const addControl = {
        hidden: true,
        focus() { focusEvents.push('add'); },
    };
    const els = { add: addControl, empty: empty };
    const documentMock = {
        activeElement: empty,
        contains(target) { return !config.detachBeforeResult && (target === empty || target === addControl); },
    };
    const picker = {
        isPending() { return false; },
        choose() { return pickerResult; },
    };
    const windowMock = { DiscoveryAgentDataPicker: picker };
    const primaryControl = () => empty.hidden ? addControl : empty;
    const add = new Function(
        'window', 'document', 'els', 'showFeedback', 'batchFeedback',
        'load', 'dataLibraryPrimaryControl', 'setTimeout',
        addSource + '\nreturn add;',
    )(
        windowMock,
        documentMock,
        els,
        (message, isError) => feedback.push({ message, isError }),
        () => ({ message: 'Added folders.', isError: false }),
        async () => {
            loadCount += 1;
            empty.hidden = true;
            addControl.hidden = false;
        },
        primaryControl,
        (callback) => callback(),
    );
    const pending = add(empty);
    resolvePicker(result);
    return pending.then(() => ({ focusEvents, feedback, loadCount, empty, addControl }));
}

test('native picker cancellation leaves the empty Data state unchanged and restores its control focus', async () => {
    const state = await dataLibraryAddHarness({
        success: true, cancelled: true, added: [], skipped: [], rejected: [],
    });

    assert.equal(state.loadCount, 0);
    assert.equal(state.empty.hidden, false);
    assert.equal(state.addControl.hidden, true);
    assert.deepEqual(state.focusEvents, ['empty']);
    assert.deepEqual(state.feedback, [{ message: '', isError: false }]);
});

test('successful picker selection refreshes once and restores focus to the populated Add Data control', async () => {
    const state = await dataLibraryAddHarness({
        success: true,
        cancelled: false,
        added: [{ id: 'one' }, { id: 'two' }],
        skipped: [],
        rejected: [],
    });

    assert.equal(state.loadCount, 1);
    assert.equal(state.empty.hidden, true);
    assert.equal(state.addControl.hidden, false);
    assert.deepEqual(state.focusEvents, ['add']);
    assert.deepEqual(state.feedback.at(-1), { message: 'Added folders.', isError: false });
});

test('picker completion after Data navigation does not focus a detached stale control', async () => {
    const state = await dataLibraryAddHarness(
        { success: true, cancelled: true, added: [], skipped: [], rejected: [] },
        { detachBeforeResult: true },
    );

    assert.deepEqual(state.focusEvents, []);
    assert.equal(state.empty.hidden, false);
});

test('one-shot modal handoff removes its signal and waits for the rendered Data page before one picker launch', async () => {
    const consumeStart = librarySource.indexOf('function consumeAutoPickerSignal()');
    const paintStart = librarySource.indexOf('function afterPagePaint()');
    const launchStart = librarySource.indexOf('async function launchAutoPickerAfterRender(initialLoad)');
    const renderStart = librarySource.indexOf('function render()', launchStart);
    const lifecycleFactory = Function('window', 'load', 'add', 'dataLibraryPrimaryControl', [
        "const AUTO_PICKER_QUERY_PARAMETER = 'open_data_picker';",
        'let autoPickerLaunchStarted = false;',
        librarySource.slice(consumeStart, paintStart),
        librarySource.slice(paintStart, launchStart),
        librarySource.slice(launchStart, renderStart),
        'return { consumeAutoPickerSignal, launchAutoPickerAfterRender };',
    ].join('\n'));

    const events = [];
    const historyCalls = [];
    let resolveInitialLoad;
    const initialLoad = new Promise((resolve) => { resolveInitialLoad = resolve; });
    const windowMock = {
        URL,
        location: { href: 'http://127.0.0.1:8000/data?open_data_picker=1&keep=yes#library' },
        history: {
            state: { retained: true },
            replaceState(state, title, next) {
                historyCalls.push({ state, title, next });
                windowMock.location.href = new URL(next, windowMock.location.href).href;
            },
        },
        requestAnimationFrame(callback) { events.push('paint'); callback(); },
        setTimeout(callback) { callback(); },
    };
    const lifecycle = lifecycleFactory(
        windowMock,
        () => initialLoad,
        async () => { events.push('picker'); },
        () => ({ id: 'data-library-control' }),
    );

    assert.equal(lifecycle.consumeAutoPickerSignal(), true);
    assert.deepEqual(historyCalls.map((call) => call.next), ['/data?keep=yes#library']);
    assert.doesNotMatch(windowMock.location.href, /open_data_picker/);
    assert.equal(lifecycle.consumeAutoPickerSignal(), false);

    const firstLaunch = lifecycle.launchAutoPickerAfterRender(initialLoad);
    const duplicateLaunch = lifecycle.launchAutoPickerAfterRender(initialLoad);
    assert.deepEqual(events, []);
    resolveInitialLoad();
    await Promise.all([firstLaunch, duplicateLaunch]);
    assert.deepEqual(events, ['paint', 'paint', 'picker']);

    const restoredLifecycle = lifecycleFactory(windowMock, async () => {}, async () => { events.push('reopened'); }, () => ({}));
    assert.equal(restoredLifecycle.consumeAutoPickerSignal(), false);
    assert.ok(!events.includes('reopened'));

    const wiring = librarySource.slice(librarySource.indexOf("document.addEventListener('DOMContentLoaded'"));
    assert.ok(wiring.indexOf('consumeAutoPickerSignal()') < wiring.indexOf('const initialLoad = load()'));
    assert.ok(wiring.indexOf('const initialLoad = load()') < wiring.indexOf('launchAutoPickerAfterRender(initialLoad)'));
    assert.match(dataTemplate, /data_library\.js'\) \}\}\?v=20260822-unsaved-size-1/);
});

test('populated Project and DiscoveryAgent Data share one renderer and compact geometry', () => {
    const projectCard = projectsTemplate.slice(
        projectsTemplate.lastIndexOf('<div', projectsTemplate.indexOf('id="np-project-data-card"')),
        projectsTemplate.indexOf('</div>', projectsTemplate.indexOf('id="np-project-data-card"')) + 6,
    );
    const projectRoot = projectsSource.slice(
        projectsSource.indexOf('function renderSelectedPreviewSource'),
        projectsSource.indexOf('function hasSelectedProjectData'),
    );
    const libraryRoot = librarySource.slice(
        librarySource.indexOf('function renderSource'),
        librarySource.indexOf('function dataLibraryPrimaryControl'),
    );
    const projectModifier = styleSource.slice(
        styleSource.indexOf('.v2-project-data-card {'),
        styleSource.indexOf('}', styleSource.indexOf('.v2-project-data-card {')) + 1,
    );

    assert.match(dataTemplate, /class="v2-data-library-box v2-data-tree-container" id="data-library-box"/);
    assert.match(projectCard, /class="v2-project-data-card v2-data-tree-container v2-data-surface-project" id="np-project-data-card"/);
    assert.ok(dataTemplate.indexOf("filename='data_tree.js'") < dataTemplate.indexOf("filename='data_library.js'"));
    assert.ok(projectsTemplate.indexOf("filename='data_tree.js'") < projectsTemplate.indexOf("filename='projects.js'"));
    assert.match(librarySource, /window\.DiscoveryAgentDataTree\.createRenderer/);
    assert.match(projectsSource, /window\.DiscoveryAgentDataTree\.createRenderer/);
    assert.match(libraryRoot, /return dataTree\.renderSource\(source\)/);
    assert.match(projectRoot, /projectDataTree\.renderSource\(\{ source, selection \}\)/);
    assert.match(treeSource, /node\('div', 'v2-data-source-row'\)/);
    assert.match(treeSource, /node\('div', 'v2-data-tree-row v2-data-tree-folder-row'\)/);
    assert.match(styleSource, /\.v2-data-tree-container\s*\{[^}]*padding:\s*10px 12px 12px[^}]*border:\s*1px solid var\(--v2-border\)[^}]*border-radius:\s*10px/s);
    assert.doesNotMatch(projectModifier, /display:\s*grid|align-items|min-height|height:|padding:|transition:/);
    assert.equal((styleSource.match(/\.v2-data-source-row\s*\{/g) || []).length, 1);
    assert.match(styleSource, /\.v2-data-source-row\s*\{[^}]*height:\s*36px[^}]*padding:\s*0[^}]*font:\s*400 1rem\/1\.6 var\(--v2-sans\)/s);
    assert.match(styleSource, /\.v2-data-source-disclosure,[\s\S]*width:\s*var\(--v2-data-tree-disclosure-column\)[^}]*height:\s*28px[^}]*padding:\s*0 var\(--v2-data-tree-caret-checkbox-gap\)/s);
    assert.match(styleSource, /\.v2-data-tree-row\s*\{[^}]*min-height:\s*30px[^}]*padding:\s*3px 5px[^}]*font:\s*400 1rem\/1\.6 var\(--v2-sans\)/s);
    assert.match(styleSource, /\.v2-data-library-box\s*\{\s*background:\s*var\(--v2-surface\)/s);
    assert.match(styleSource, /\.v2-data-surface-project\s*\{\s*background:\s*var\(--v2-bg\)/s);
    assert.match(projectsTemplate, /v2-data-selector-browser v2-data-tree-container v2-data-surface-project/);
    assert.doesNotMatch(dataTemplate, /v2-data-surface-project/);
    assert.doesNotMatch(styleSource, /\.v2-selected-data-source \.v2-data-source-name|\.v2-project-data-source-remove\s*\{/);
});
test('compact nested rows truncate long names and remain contained', () => {
    assert.match(styleSource, /\.v2-data-tree-row\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0/);
    assert.match(styleSource, /\.v2-data-tree-name\s*\{[^}]*overflow:\s*hidden[^}]*text-overflow:\s*ellipsis[^}]*white-space:\s*nowrap/);
    assert.match(styleSource, /\.v2-data-source\s*\{[^}]*border-bottom:\s*1px solid var\(--v2-border-soft\)/s);
    assert.match(styleSource, /\.v2-data-source:last-child\s*\{[^}]*border-bottom:\s*0/s);
    assert.match(styleSource, /\.v2-data-tree,\n\.v2-data-tree-children \{[^}]*padding:\s*0/s);
    assert.match(treeSource, /--v2-data-tree-indent/);
});

test('project selector persists hierarchical IDs and drops reconciled missing IDs', () => {
    assert.match(projectsTemplate, /id="np-data-field"/);
    assert.match(projectsTemplate, /Select the data this project will have access to\./);
    assert.match(projectsTemplate, /id="np-select-data"/);
    assert.match(projectsSource, /body\.append\('data_sources', JSON\.stringify\(selectedDataSources\)\)/);
    assert.match(projectsSource, /checkbox\.indeterminate = state\.indeterminate/);
    assert.match(projectsSource, /setAttribute\('aria-checked', state\.indeterminate \? 'mixed'/);
    assert.match(projectsSource, /selected_paths/);
    assert.match(projectsSource, /excluded_paths/);
    assert.match(projectsSource, /const validIds = new Set\(librarySources\.map/);
    assert.match(projectsSource, /selectedDataSources = selectedDataSources\.filter/);
    assert.doesNotMatch(projectsSource, /v2-data-source-status|source\.available|Relink|\/api\/data-library\/choose/);
    assert.doesNotMatch(projectsSource, /window\.location[^\n]*\/data|location\.href[^\n]*\/data/);
});

test('Create and Edit share one Data card without exposing the physical project folder', () => {
    assert.equal((projectsTemplate.match(/id="np-data-field"/g) || []).length, 1);
    assert.match(projectsTemplate, /No data selected/);
    assert.match(projectsTemplate, /<button type="button" class="v2-data-action v2-project-data-action" id="np-select-data" hidden>[\s\S]*Select Data[\s\S]*&#8599;/);
    assert.doesNotMatch(projectsTemplate, /Click anywhere to select data|np-selected-data-guidance/);
    assert.match(projectsTemplate, /class="v2-dropzone-action">Click to select data<\/span>/);
    assert.match(projectsTemplate, /<a class="v2-data-action" id="np-data-library-add-data"\s+href="\{\{ url_for\('discoveryagent_data', open_data_picker='1'\) \}\}">[\s\S]*Add Data[\s\S]*&#8599;/);
    assert.doesNotMatch(projectsTemplate, />DATA<\/span>|Click to browse DiscoveryAgent Data/);
    assert.doesNotMatch(projectsTemplate, /np-edit-data|np-create-data|INPUT DATA|Open Data Library|Open Data Folder|Copy path/);
    assert.doesNotMatch(projectsSource, /data_folder|renderEditDataFolder|rescanDataFolder|openDataFolder|copyEditDataPath/);
    assert.doesNotMatch(projectsSource, /body\.append\('data_folder'|\/api\/data-folder\/inspect/);
});

test('selection reserves the management action track without rendering a dummy control', () => {
    const sourceRenderer = treeSource.slice(treeSource.indexOf('function renderSource'), treeSource.indexOf('function ensureHeader'));
    const childRenderer = treeSource.slice(treeSource.indexOf('function renderChild'), treeSource.indexOf('async function loadChildren'));
    assert.match(styleSource, /\.v2-data-tree-selection-mode,\s*\.v2-data-tree-action-mode \{ --v2-data-tree-action-column: 36px; \}/);
    assert.match(styleSource, /grid-template-columns:[\s\S]*minmax\(0, 1fr\)[\s\S]*var\(--v2-data-tree-size-column\)[\s\S]*var\(--v2-data-tree-action-column\)/);
    assert.match(styleSource, /\.v2-data-tree-header-size \{ grid-column: 5; padding-right: 4px; text-align: right; \}/);
    assert.match(styleSource, /\.v2-data-tree-size \{[^}]*grid-column:\s*5[^}]*padding-right:\s*4px[^}]*text-align:\s*right/s);
    assert.match(styleSource, /\.v2-data-tree-header,[\s\S]*\.v2-data-tree-row \{[^}]*width:\s*100%[^}]*max-width:\s*100%/s);
    assert.match(styleSource, /\.v2-data-source-row:hover,[\s\S]*\.v2-data-tree-item:focus-visible > \.v2-data-tree-row \{\s*background:\s*var\(--v2-accent-soft\)/s);
    assert.match(styleSource, /calc\(var\(--v2-data-tree-disclosure-column\) \+ var\(--v2-data-tree-indent, 0px\)\)[\s\S]*var\(--v2-data-tree-size-column\)[\s\S]*var\(--v2-data-tree-action-column\)/);
    assert.match(styleSource, /@media \(max-width: 520px\)\s*\{[\s\S]*--v2-data-tree-size-column:\s*0px[\s\S]*\.v2-data-tree-size \{ display: none; \}/);
    assert.match(treeSource, /if \(sizeBytes == null\) return "--"/);
    assert.match(sourceRenderer, /if \(options\.onRemove\)[\s\S]*node\('button', classes\)[\s\S]*row\.appendChild\(remove\)/);
    assert.doesNotMatch(sourceRenderer + childRenderer, /action-slot|action-spacer|empty-action/);
});
