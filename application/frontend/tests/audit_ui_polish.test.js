const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appRoot = path.join(__dirname, '..', 'da_selection_ui');
const auditSource = fs.readFileSync(path.join(appRoot, 'static', 'project_audit_agent.js'), 'utf8');
const workspaceSource = fs.readFileSync(path.join(appRoot, 'static', 'round_workspace.js'), 'utf8');
const template = fs.readFileSync(path.join(appRoot, 'templates', 'round_workspace.html'), 'utf8');
const workspaceCss = fs.readFileSync(path.join(appRoot, 'static', 'round_workspace.css'), 'utf8');
const auditCss = fs.readFileSync(path.join(appRoot, 'static', 'project_audit_agent.css'), 'utf8');

function cssRule(selector) {
    const start = workspaceCss.indexOf(`${selector}{`);
    assert.notEqual(start, -1, `missing CSS rule for ${selector}`);
    const end = workspaceCss.indexOf('}', start);
    assert.notEqual(end, -1, `unterminated CSS rule for ${selector}`);
    return workspaceCss.slice(start, end + 1);
}

function historyDrawerHarness() {
    const start = auditSource.indexOf('    function setHistoryDrawerOpen(open)');
    const end = auditSource.indexOf('    function renderMarkdown', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const attributes = new Map();
    const historyDrawerEl = { hidden: true };
    function makeSlot(id) {
        return {
            id,
            children: [],
            appendChild(child) {
                if (child.parentElement) {
                    child.parentElement.children = child.parentElement.children.filter(item => item !== child);
                }
                this.children.push(child);
                child.parentElement = this;
            },
        };
    }
    const historyClosedToggleSlotEl = makeSlot('closed');
    const historyOpenToggleSlotEl = makeSlot('open');
    const document = { activeElement: null };
    const historyToggleBtn = {
        title: '',
        parentElement: historyClosedToggleSlotEl,
        classList: { toggle() {} },
        focus() { document.activeElement = this; },
        setAttribute(name, value) { attributes.set(name, value); },
    };
    historyClosedToggleSlotEl.children.push(historyToggleBtn);
    let renderCount = 0;
    let revealCount = 0;
    const functionSource = auditSource.slice(start, end);
    const setOpen = new Function(
        'historyDrawerEl', 'historyToggleBtn', 'historyClosedToggleSlotEl',
        'historyOpenToggleSlotEl', 'document', 'renderChatList', 'revealActiveChatRow',
        `${functionSource}; return setHistoryDrawerOpen;`,
    )(
        historyDrawerEl,
        historyToggleBtn,
        historyClosedToggleSlotEl,
        historyOpenToggleSlotEl,
        document,
        () => { renderCount += 1; },
        () => { revealCount += 1; },
    );
    return {
        attributes,
        functionSource,
        historyClosedToggleSlotEl,
        historyDrawerEl,
        historyOpenToggleSlotEl,
        historyToggleBtn,
        clickToggle() { document.activeElement = historyToggleBtn; setOpen(historyDrawerEl.hidden); },
        focused() { return document.activeElement; },
        counts() { return { renderCount, revealCount }; },
    };
}

test('the single history icon opens and closes with stateful accessibility text', () => {
    const selectedConversation = { id: 'selected-chat' };
    const harness = historyDrawerHarness();

    harness.clickToggle();
    assert.equal(harness.historyDrawerEl.hidden, false);
    assert.equal(harness.attributes.get('aria-expanded'), 'true');
    assert.equal(harness.attributes.get('aria-label'), 'Close chat history');
    assert.equal(harness.historyToggleBtn.title, 'Close chat history');
    assert.equal(harness.historyToggleBtn.parentElement, harness.historyOpenToggleSlotEl);
    assert.equal(harness.focused(), harness.historyToggleBtn);
    assert.deepEqual(harness.historyClosedToggleSlotEl.children, []);
    assert.deepEqual(harness.counts(), { renderCount: 1, revealCount: 1 });

    harness.clickToggle();
    assert.equal(harness.historyDrawerEl.hidden, true);
    assert.equal(harness.attributes.get('aria-expanded'), 'false');
    assert.equal(harness.attributes.get('aria-label'), 'Open chat history');
    assert.equal(harness.historyToggleBtn.title, 'Open chat history');
    assert.equal(harness.historyToggleBtn.parentElement, harness.historyClosedToggleSlotEl);
    assert.equal(harness.focused(), harness.historyToggleBtn);
    assert.deepEqual(harness.historyOpenToggleSlotEl.children, []);
    assert.equal(selectedConversation.id, 'selected-chat');
    assert.doesNotMatch(harness.functionSource, /activeChatId\s*=|activateChat\(|switchChat\(/);

    assert.match(auditSource, /historyToggleBtn && historyToggleBtn\.addEventListener\('click'/);
    assert.doesNotMatch(auditSource, /historyCloseBtn|getElementById\(['"]audit-history-close['"]\)/);
    assert.doesNotMatch(template, /id="audit-history-close"/);
    // The configuration dialog has its own X; only Audit's drawer lacks one.
    assert.ok(template.indexOf('id="aa-sidebar"') >= 0);
    assert.doesNotMatch(template.slice(template.indexOf('id="aa-sidebar"'),
        template.indexOf('id="rw-project-configuration-overlay"')), /&times;/);
    assert.equal((template.match(/id="audit-history-toggle"/g) || []).length, 1);
});

test('Audit controls are internal and history remains an in-flow independently scrolling sibling', () => {
    const pageStart = template.indexOf('<section class="aa-page aa-embedded"');
    const layoutStart = template.indexOf('<div class="aa-layout">', pageStart);
    const sidebarStart = template.indexOf('<aside class="v2-workspace-sidebar aa-sidebar"', layoutStart);
    const sidebarEnd = template.indexOf('</aside>', sidebarStart);
    const workspaceStart = template.indexOf('<div class="aa-workspace">', sidebarEnd);
    const controlStart = template.indexOf('<div class="aa-control-row">', workspaceStart);
    const transcriptStart = template.indexOf('id="main-interface"', controlStart);
    const sidebarMarkup = template.slice(sidebarStart, sidebarEnd);

    assert.ok(pageStart >= 0 && layoutStart > pageStart);
    assert.match(template.slice(pageStart, layoutStart), /aria-label="Audit Agent"/);
    assert.doesNotMatch(template.slice(pageStart, layoutStart), /aa-embedded-header|aa-embedded-title|>\s*Audit Agent\s*</);
    assert.ok(layoutStart >= 0 && sidebarStart > layoutStart && workspaceStart > sidebarEnd);
    assert.ok(controlStart > workspaceStart && transcriptStart > controlStart);
    assert.match(sidebarMarkup, /aa-history-heading[\s\S]*Chat history[\s\S]*id="audit-history-open-toggle-slot"/);
    assert.match(template.slice(controlStart, transcriptStart), /id="audit-history-closed-toggle-slot"[\s\S]*id="audit-history-toggle"/);

    const layout = cssRule('.aa-embedded .aa-layout');
    const sidebar = cssRule('.aa-embedded .aa-sidebar');
    const hiddenSidebar = cssRule('.aa-embedded .aa-sidebar[hidden]');
    const openSidebar = cssRule('.aa-embedded .aa-sidebar:not([hidden])');
    const workspace = cssRule('.aa-embedded .aa-workspace');
    const controlRow = cssRule('.aa-control-row');
    const historyHeading = cssRule('.aa-history-heading');

    assert.match(layout, /display:flex/);
    assert.match(layout, /align-items:stretch/);
    assert.match(sidebar, /flex:0 0 clamp\(96px,34%,180px\)/);
    assert.match(workspace, /flex:1 1 auto/);
    assert.match(workspace, /min-width:0/);
    assert.match(workspace, /flex-direction:column/);
    assert.match(workspace, /overflow:hidden/);
    assert.match(hiddenSidebar, /display:none/);
    assert.match(openSidebar, /display:flex/);

    for (const rule of [layout, sidebar, workspace]) {
        assert.match(rule, /background:var\(--v2-surface\)/);
    }
    assert.match(controlRow, /flex:0 0 var\(--aa-internal-control-height\)/);
    assert.match(controlRow, /border:0/);
    assert.match(controlRow, /box-shadow:none/);
    assert.match(controlRow, /background:var\(--v2-surface\)/);
    assert.doesNotMatch(controlRow, /border-(?:top|bottom)|position:(?:sticky|absolute|fixed)/);
    assert.match(historyHeading, /grid-template-columns:minmax\(0,1fr\) 30px/);
    assert.match(historyHeading, /flex:0 0 auto/);
    assert.match(sidebar, /border-right:1px solid var\(--v2-border\)/);
    assert.match(sidebar, /border-radius:0/);
    assert.match(sidebar, /box-shadow:none/);
    assert.doesNotMatch(sidebar, /position:(?:absolute|fixed)|(?:^|[;{])inset:|z-index:|transform:|backdrop|margin(?:-left)?:-/);
    assert.match(auditCss, /\.aa-chat-list\s*\{[\s\S]*?overflow-y:\s*auto/);
    assert.match(auditCss, /\.aa-shell\s*\{[\s\S]*?overflow-y:\s*auto/);
    assert.match(workspaceCss, /\.aa-embedded \.aa-composer\{position:absolute;[^}]*bottom:0/);
});

test('Activity and Audit Agent tabs are vertically centered within the shared-height header', () => {
    const outerStart = template.indexOf('<header class="rw-panel-tabs">');
    const outerEnd = template.indexOf('</header>', outerStart);
    const outerHeader = template.slice(outerStart, outerEnd);
    const sharedHeader = cssRule('.rw-pane-header,.rw-panel-tabs');
    const panelBand = cssRule('.rw-panel-tabs');
    const panelButtons = cssRule('.rw-panel-tabs>button:not(.rw-panel-collapse)');
    const panelButtonAppearance = cssRule('.rw-panel-tabs button');

    assert.match(sharedHeader, /height:41px/);
    assert.match(sharedHeader, /min-height:41px/);
    assert.match(panelBand, /display:flex/);
    assert.match(panelBand, /align-items:center/);
    assert.match(panelButtons, /display:inline-flex/);
    assert.match(panelButtons, /align-items:center/);
    assert.match(panelButtons, /height:100%/);
    assert.match(panelButtons, /border-top:2px solid transparent/);
    assert.match(panelButtonAppearance, /padding:0 14px/);
    assert.doesNotMatch(panelButtons, /padding-(?:top|bottom)|margin-(?:top|bottom)|(?:^|[;{])(?:transform|top|bottom|position):/);
    assert.doesNotMatch(panelButtonAppearance, /outline:none/);
    assert.match(workspaceCss, /\.rw-file-tab-label\{box-sizing:border-box;height:100%;padding-top:var\(--rw-tab-padding-start\);padding-bottom:var\(--rw-tab-padding-end\);display:inline-flex;align-items:center\}/);
    assert.match(outerHeader, /data-panel-tab="activity"/);
    assert.match(outerHeader, /data-panel-tab="chat"/);
    assert.match(workspaceSource, /classList\.toggle\('is-active',x\.dataset\.panelTab===state\.panelTab\)/);
    assert.doesNotMatch(outerHeader, /audit-history-toggle|audit-new-chat-btn|chat-export-current-btn/);
});

test('collapsed Activity / Audit Agent pill is centered by its painted header row', () => {
    const restoreRow = cssRule('.rw-panel-restore-row');
    const restorePill = cssRule('.rw-panel-restore');
    const restoreMarkupStart = template.indexOf('<div class="rw-panel-restore-row">');
    const restoreMarkupEnd = template.indexOf('</div>', restoreMarkupStart);
    const restoreMarkup = template.slice(restoreMarkupStart, restoreMarkupEnd);

    assert.ok(restoreMarkupStart >= 0);
    assert.match(restoreMarkup, /<button class="rw-panel-restore" id="rw-panel-restore" type="button" hidden>Activity \/ Audit Agent<\/button>/);
    assert.match(restoreRow, /box-sizing:border-box/);
    assert.match(restoreRow, /position:absolute/);
    assert.match(restoreRow, /top:0/);
    assert.match(restoreRow, /right:0/);
    assert.match(restoreRow, /left:0/);
    assert.match(restoreRow, /display:flex/);
    assert.match(restoreRow, /align-items:center/);
    assert.match(restoreRow, /justify-content:flex-end/);
    assert.match(restoreRow, /height:41px/);
    assert.match(restoreRow, /padding:7px 10px/);
    assert.match(restoreRow, /pointer-events:none/);
    assert.match(restorePill, /position:static/);
    assert.match(restorePill, /margin:0/);
    assert.match(restorePill, /padding:7px 11px/);
    assert.match(restorePill, /pointer-events:auto/);
    assert.doesNotMatch(restorePill, /height:100%|transform:|(?:^|[;{])(?:top|bottom):|position:absolute/);
    assert.equal((41 - 27) / 2, 7);
    assert.match(template, /round_workspace\.css', v='20261001-configuration-polish-1'/);
    assert.match(workspaceSource, /els\.restore\.hidden=!state\.panelCollapsed/);
    assert.match(workspaceSource, /els\.restore\.addEventListener\('click',\(\)=>\{setPanelCollapsed\(false\);\}\)/);
    assert.match(workspaceSource, /notifyAuditLifecycle\(next\?'closed':'reopened'\)/);
});

test('Audit actions render New chat before Download with their icons in the requested order', () => {
    const controlRow = template.split('<div class="aa-control-row">', 2)[1].split('id="main-interface"', 1)[0];
    const actions = template.split('<div class="aa-embedded-actions">', 2)[1].split('</div>', 1)[0];
    const newChatAt = actions.indexOf('id="audit-new-chat-btn"');
    const downloadAt = actions.indexOf('id="chat-export-current-btn"');
    const newChatButton = actions.slice(newChatAt, actions.indexOf('</button>', newChatAt));
    const downloadButton = actions.slice(downloadAt, actions.indexOf('</button>', downloadAt));
    assert.ok(newChatAt >= 0 && newChatAt < downloadAt);
    assert.ok(newChatButton.indexOf('<svg') < newChatButton.indexOf('<span>New chat</span>'));
    assert.match(newChatButton, /class="aa-embedded-new-chat"/);
    assert.match(newChatButton, /aria-label="New chat"/);
    assert.match(newChatButton, /type="button"/);
    assert.ok(downloadButton.indexOf('<svg') < downloadButton.indexOf('<span>Download</span>'));
    assert.match(downloadButton, /class="aa-embedded-download"/);
    assert.doesNotMatch(actions, /Reset|aa-embedded-reset/);
    assert.ok(controlRow.indexOf('id="audit-history-closed-toggle-slot"') < controlRow.indexOf('class="aa-embedded-actions"'));
    assert.match(auditSource, /exportChat\(getActiveChat\(\)\);/);
    assert.ok(auditSource.includes('a.download = `audit_chat_export_${ts}.md`;'));
});

test('New chat keeps prior conversations and creates no transient identity or persistence write', () => {
    const start = auditSource.indexOf('    function startNewChat()');
    const end = auditSource.indexOf('    async function restoreAuditConversationOnServer', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const startNewChatSource = auditSource.slice(start, end);

    assert.match(startNewChatSource, /showBlankNewChat\(\{ discardDraft: true, resetScroll: true \}\)/);
    assert.doesNotMatch(startNewChatSource, /createEmptyChat|createChatId|persistAuditChatState|resetAuditConversationOnServer|restoreAuditConversationOnServer/);
    assert.match(auditSource, /function showBlankNewChat\(options\)/);
    assert.match(auditSource, /activeChatId = null/);
    assert.match(auditSource, /cleanupUnlistedDraftChats\(null\)/);
});

test('welcome and docked composers share one vertically centered textarea rule', () => {
    const input = cssRule('.aa-embedded .aa-input');
    const baseInputStart = fs.readFileSync(
        path.join(appRoot, 'static', 'project_audit_agent.css'), 'utf8',
    ).indexOf('.aa-input {');
    const baseInputCss = fs.readFileSync(
        path.join(appRoot, 'static', 'project_audit_agent.css'), 'utf8',
    );
    const baseInput = baseInputCss.slice(baseInputStart, baseInputCss.indexOf('}', baseInputStart) + 1);

    assert.match(input, /box-sizing:border-box/);
    assert.match(input, /min-height:32px/);
    assert.match(input, /max-height:150px/);
    assert.match(input, /margin-bottom:1px/);
    assert.match(input, /padding:6px 6px 6px 2px/);
    assert.match(input, /line-height:1\.45/);
    assert.match(baseInput, /overflow-y:\s*auto/);
    assert.doesNotMatch(workspaceCss, /\.aa-shell\.is-empty \.aa-input\s*\{/);
    assert.match(auditSource, /chatInputEl\.style\.height = 'auto'/);
    assert.match(auditSource, /if \(!chatInputEl\.value\)/);
    assert.match(auditSource, /chatInputEl\.style\.height = ''/);
    assert.match(auditSource, /Math\.min\(chatInputEl\.scrollHeight, 260\)/);
    assert.match(auditSource, /e\.key === 'Enter' && !e\.shiftKey/);
});

test('Markdown Download renders its scoped icon before text and remains artifact-type conditional', () => {
    const controlStart = workspaceSource.indexOf('  function downloadControl(file,current)');
    const controlEnd = workspaceSource.indexOf('  function artifactViewControls(file)', controlStart);
    const controlsEnd = workspaceSource.indexOf('  function renderPanes()', controlEnd);
    const downloadControl = workspaceSource.slice(controlStart, controlEnd);
    const artifactControls = workspaceSource.slice(controlEnd, controlsEnd);
    assert.ok(downloadControl.indexOf('<svg class="rw-report-download-icon"') < downloadControl.indexOf('<span>Download</span>'));
    assert.match(downloadControl, /markdownArtifactDownloadUrl\(file\)/);
    assert.match(artifactControls, /if\(!report&&!downloadable\)return null/);
    assert.match(artifactControls, /controls\.append\(downloadControl\(file,current\)\)/);
    assert.match(workspaceSource, /const viewControls=artifactViewControls\(file\);if\(viewControls\)breadcrumb\.append\(viewControls\)/);
});
