const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'da_selection_ui');
const scrollbar = require(path.join(root, 'static', 'round_workspace_tab_scrollbar.js'));
const scrollbarSource = fs.readFileSync(path.join(root, 'static', 'round_workspace_tab_scrollbar.js'), 'utf8');
const workspaceSource = fs.readFileSync(path.join(root, 'static', 'round_workspace.js'), 'utf8');
const workspaceCss = fs.readFileSync(path.join(root, 'static', 'round_workspace.css'), 'utf8');
const template = fs.readFileSync(path.join(root, 'templates', 'round_workspace.html'), 'utf8');

function workspaceBetween(startMarker, endMarker) {
    const start = workspaceSource.indexOf(startMarker);
    const end = workspaceSource.indexOf(endMarker, start);
    assert.notEqual(start, -1, 'missing ' + startMarker);
    assert.notEqual(end, -1, 'missing ' + endMarker);
    return workspaceSource.slice(start, end);
}
const installSource = workspaceBetween('  function installTabScrollbars()', '  function renderPanes()');

function mockClassList() {
    const names = new Set();
    return {
        add(value) { names.add(value); },
        remove(value) { names.delete(value); },
        contains(value) { return names.has(value); },
    };
}

class MockElement {
    constructor({ clientWidth = 0, scrollWidth = 0, left = 0 } = {}) {
        this.attributes = new Map();
        this.children = [];
        this.classList = mockClassList();
        this.clientWidth = clientWidth;
        this.scrollWidth = scrollWidth;
        this.scrollLeft = 0;
        this.hidden = false;
        this.isConnected = true;
        this.listeners = new Map();
        this.style = {};
        this.left = left;
        this.focused = false;
        this.parentElement = null;
        this.capturedPointer = null;
    }

    addEventListener(type, listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type).add(listener);
    }

    removeEventListener(type, listener) {
        if (this.listeners.has(type)) this.listeners.get(type).delete(listener);
    }

    emit(type, values = {}) {
        const event = {
            type,
            target: this,
            button: 0,
            pointerId: 1,
            clientX: 0,
            key: '',
            preventDefault() { this.defaultPrevented = true; },
            stopPropagation() { this.propagationStopped = true; },
            ...values,
        };
        for (const listener of this.listeners.get(type) || []) listener(event);
        return event;
    }

    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name); }
    removeAttribute(name) { this.attributes.delete(name); }
    getBoundingClientRect() { return { left: this.left, width: this.clientWidth }; }
    focus() { this.focused = true; }
    setPointerCapture(pointerId) { this.capturedPointer = pointerId; }
    hasPointerCapture(pointerId) { return this.capturedPointer === pointerId; }
    releasePointerCapture(pointerId) {
        if (this.capturedPointer === pointerId) this.capturedPointer = null;
    }
}

function controllerHarness({ clientWidth = 200, scrollWidth = 500, fontSet = null } = {}) {
    const animationFrames = [];
    const resizeObservers = [];
    const mutationObservers = [];
    const originals = {
        requestAnimationFrame: global.requestAnimationFrame,
        ResizeObserver: global.ResizeObserver,
        MutationObserver: global.MutationObserver,
        document: global.document,
    };
    global.requestAnimationFrame = (callback) => {
        animationFrames.push(callback);
        return animationFrames.length;
    };
    global.ResizeObserver = class {
        constructor(callback) {
            this.callback = callback;
            this.observed = [];
            this.disconnected = false;
            resizeObservers.push(this);
        }
        observe(element) { this.observed.push(element); }
        disconnect() { this.disconnected = true; this.observed = []; }
    };
    global.MutationObserver = class {
        constructor(callback) {
            this.callback = callback;
            this.disconnected = false;
            mutationObservers.push(this);
        }
        observe() {}
        disconnect() { this.disconnected = true; }
    };
    if (fontSet) global.document = { fonts: fontSet };

    const host = new MockElement();
    const scroller = new MockElement({ clientWidth, scrollWidth });
    const track = new MockElement({ clientWidth, left: 10 });
    const thumb = new MockElement();
    track.parentElement = host;
    const controller = scrollbar.create(scroller, track, thumb);
    function flush() {
        let safety = 20;
        while (animationFrames.length && safety > 0) {
            const callback = animationFrames.shift();
            callback();
            safety -= 1;
        }
        assert.ok(safety > 0, 'animation-frame updates settle without feedback loops');
    }
    function restore() {
        controller.destroy();
        global.requestAnimationFrame = originals.requestAnimationFrame;
        global.ResizeObserver = originals.ResizeObserver;
        global.MutationObserver = originals.MutationObserver;
        global.document = originals.document;
    }
    return {
        animationFrames,
        controller,
        flush,
        host,
        mutationObserver: mutationObservers[0],
        resizeObserver: resizeObservers[0],
        restore,
        scroller,
        thumb,
        track,
    };
}

test('geometry hides at fit/tolerance and maps thumb size and position to the native scroller', () => {
    assert.equal(scrollbar.geometry(200, 200, 0, 200).overflow, false);
    assert.equal(scrollbar.geometry(200.75, 200, 0, 200).overflow, false);
    assert.equal(scrollbar.geometry(201, 200, 0, 200).overflow, false);
    assert.equal(scrollbar.geometry(201.01, 200, 0, 200).overflow, true);
    const metrics = scrollbar.geometry(500, 200, 150, 200);
    assert.equal(metrics.overflow, true);
    assert.equal(metrics.maximum, 300);
    assert.equal(metrics.current, 150);
    assert.equal(metrics.thumbWidth, 80);
    assert.equal(metrics.thumbOffset, 60);
});

test('track clicks and every required keyboard command map to horizontal scroll positions', () => {
    const metrics = scrollbar.geometry(500, 200, 0, 200);
    assert.equal(scrollbar.scrollForTrackPoint(metrics, 100), 150);
    assert.equal(scrollbar.scrollForKey('ArrowRight', 10, 300, 200), 50);
    assert.equal(scrollbar.scrollForKey('ArrowLeft', 10, 300, 200), 0);
    assert.equal(scrollbar.scrollForKey('PageDown', 10, 300, 200), 170);
    assert.equal(scrollbar.scrollForKey('PageUp', 170, 300, 200), 10);
    assert.equal(scrollbar.scrollForKey('Home', 170, 300, 200), 0);
    assert.equal(scrollbar.scrollForKey('End', 10, 300, 200), 300);
    assert.equal(scrollbar.scrollForKey('Enter', 10, 300, 200), null);
});

test('one or several fitting tabs reserve no row and stay out of focus and accessibility', () => {
    const harness = controllerHarness({ clientWidth: 240, scrollWidth: 120 });
    try {
        assert.equal(harness.track.hidden, true, 'hidden before the first reliable measurement');
        assert.equal(harness.track.tabIndex, -1);
        assert.equal(harness.track.getAttribute('aria-hidden'), 'true');
        assert.equal(harness.host.classList.contains('has-tab-overflow'), false);
        harness.flush();
        assert.equal(harness.track.hidden, true, 'one short tab remains collapsed');

        harness.scroller.scrollWidth = 240;
        harness.mutationObserver.callback();
        harness.flush();
        assert.equal(harness.track.hidden, true, 'several exact-fit tabs remain collapsed');

        harness.scroller.scrollWidth = 240.75;
        harness.resizeObserver.callback();
        harness.flush();
        assert.equal(harness.track.hidden, true, 'subpixel rounding remains collapsed');
        assert.equal(harness.track.tabIndex, -1);
        assert.equal(harness.track.getAttribute('aria-hidden'), 'true');
        assert.equal(harness.host.classList.contains('has-tab-overflow'), false);
    } finally {
        harness.restore();
    }
});

test('controller mirrors native scrolling, supports track, thumb, keyboard, and active-tab reveal', () => {
    const harness = controllerHarness();
    try {
        harness.flush();
        assert.equal(harness.track.hidden, false);
        assert.equal(harness.track.tabIndex, 0);
        assert.equal(harness.track.getAttribute('aria-hidden'), undefined);
        assert.equal(harness.host.classList.contains('has-tab-overflow'), true);
        assert.equal(harness.track.getAttribute('aria-valuemin'), '0');
        assert.equal(harness.track.getAttribute('aria-valuemax'), '300');
        assert.equal(harness.track.getAttribute('aria-valuenow'), '0');
        assert.equal(harness.thumb.style.width, '80px');

        harness.scroller.scrollLeft = 150;
        harness.scroller.emit('scroll');
        harness.flush();
        assert.equal(harness.track.getAttribute('aria-valuenow'), '150');
        assert.equal(harness.thumb.style.transform, 'translateX(60px)');

        const trackClick = harness.track.emit('pointerdown', {
            target: harness.track,
            clientX: 160,
        });
        harness.flush();
        assert.equal(Math.round(harness.scroller.scrollLeft), 275);
        assert.equal(trackClick.defaultPrevented, true);
        assert.equal(harness.track.focused, true);

        harness.scroller.scrollLeft = 0;
        harness.resizeObserver.callback();
        harness.flush();
        harness.thumb.emit('pointerdown', { target: harness.thumb, clientX: 20, pointerId: 7 });
        harness.thumb.emit('pointermove', { target: harness.thumb, clientX: 80, pointerId: 7 });
        harness.flush();
        assert.equal(Math.round(harness.scroller.scrollLeft), 150);
        assert.equal(harness.track.classList.contains('is-dragging'), true);
        harness.thumb.emit('pointerup', { target: harness.thumb, pointerId: 7 });
        assert.equal(harness.track.classList.contains('is-dragging'), false);

        const keyEvent = harness.track.emit('keydown', { key: 'End' });
        harness.flush();
        assert.equal(harness.scroller.scrollLeft, 300);
        assert.equal(keyEvent.defaultPrevented, true);

        const active = {
            isConnected: true,
            scrollIntoView(options) {
                assert.deepEqual(options, { block: 'nearest', inline: 'nearest' });
                harness.scroller.scrollLeft = 90;
                harness.scroller.emit('scroll');
            },
        };
        harness.controller.scrollActive(active);
        harness.flush();
        assert.equal(harness.track.getAttribute('aria-valuenow'), '90');
    } finally {
        harness.restore();
    }
});

test('overflow transitions on tab and panel geometry fully collapse and safely reappear', () => {
    const harness = controllerHarness({ clientWidth: 260, scrollWidth: 250 });
    const active = {
        isConnected: true,
        revealCount: 0,
        scrollIntoView() {
            this.revealCount += 1;
            harness.scroller.scrollLeft = 70;
        },
    };
    try {
        harness.controller.scrollActive(active);
        harness.flush();
        assert.equal(harness.track.hidden, true);
        assert.equal(harness.scroller.scrollLeft, 0);

        harness.scroller.scrollWidth = 420;
        harness.mutationObserver.callback();
        harness.flush();
        assert.equal(harness.track.hidden, false, 'adding a tab can create overflow');
        assert.equal(harness.host.classList.contains('has-tab-overflow'), true);
        assert.equal(harness.scroller.scrollLeft, 70, 'the active tab is revealed');
        assert.ok(Number(harness.track.getAttribute('aria-valuemax')) > 0);

        harness.scroller.scrollLeft = 999;
        harness.scroller.scrollWidth = 250;
        harness.resizeObserver.callback();
        harness.flush();
        assert.equal(harness.track.hidden, true, 'closing tabs fully collapses the row');
        assert.equal(harness.scroller.scrollLeft, 0, 'stale scrolling resets when all tabs fit');
        assert.equal(harness.track.tabIndex, -1);
        assert.equal(harness.track.getAttribute('aria-hidden'), 'true');

        harness.scroller.clientWidth = 180;
        harness.track.clientWidth = 180;
        harness.resizeObserver.callback();
        harness.flush();
        assert.equal(harness.track.hidden, false, 'narrowing or opening the panel creates overflow');
        assert.equal(harness.scroller.scrollLeft, 70);

        harness.scroller.clientWidth = 300;
        harness.track.clientWidth = 300;
        harness.resizeObserver.callback();
        harness.flush();
        assert.equal(harness.track.hidden, true, 'widening or closing the panel removes overflow');
        assert.ok(active.revealCount >= 2);
    } finally {
        harness.restore();
    }
});

test('tab mutations and workspace or panel resizes recalculate visibility without a feedback loop', () => {
    const harness = controllerHarness();
    try {
        harness.flush();
        harness.scroller.scrollWidth = 200;
        harness.resizeObserver.callback();
        harness.flush();
        assert.equal(harness.track.hidden, true);
        assert.equal(harness.track.getAttribute('aria-valuemax'), '0');
        assert.equal(harness.track.getAttribute('aria-valuenow'), '0');

        harness.scroller.scrollWidth = 760;
        harness.scroller.clientWidth = 180;
        harness.track.clientWidth = 180;
        harness.mutationObserver.callback();
        harness.flush();
        assert.equal(harness.track.hidden, false);
        assert.equal(harness.track.getAttribute('aria-valuemax'), '580');

        harness.resizeObserver.callback();
        harness.resizeObserver.callback();
        harness.scroller.emit('scroll');
        assert.equal(harness.animationFrames.length, 1, 'rapid updates coalesce into one frame');
        harness.flush();
    } finally {
        harness.restore();
    }
});

test('font completion recalculates geometry and all observers and callbacks clean up', () => {
    let readyCallback = null;
    const fontListeners = new Map();
    const fontSet = {
        ready: {
            then(callback) {
                readyCallback = callback;
                return { catch() {} };
            },
        },
        addEventListener(type, callback) { fontListeners.set(type, callback); },
        removeEventListener(type, callback) {
            if (fontListeners.get(type) === callback) fontListeners.delete(type);
        },
    };
    const harness = controllerHarness({ clientWidth: 200, scrollWidth: 180, fontSet });
    harness.flush();
    harness.scroller.scrollWidth = 360;
    readyCallback();
    harness.flush();
    assert.equal(harness.track.hidden, false, 'font readiness can expose new overflow');
    harness.scroller.scrollWidth = 180;
    fontListeners.get('loadingdone')();
    harness.flush();
    assert.equal(harness.track.hidden, true, 'later font loading can remove overflow');

    harness.controller.destroy();
    assert.equal(harness.resizeObserver.disconnected, true);
    assert.equal(harness.mutationObserver.disconnected, true);
    assert.equal(fontListeners.has('loadingdone'), false);
    assert.equal(harness.scroller.listeners.get('scroll').size, 0);
    assert.equal(harness.track.hidden, true);
    assert.equal(harness.track.tabIndex, -1);
    harness.restore();
});

test('workspace integration keeps the native tab strip authoritative and adds no second scroller', () => {
    assert.match(workspaceSource, /const TabScrollbar = window\.RoundWorkspaceTabScrollbar/);
    assert.doesNotMatch(workspaceSource, /throw new Error\('Artifact tab scrollbar is unavailable'\)/);
    assert.match(installSource, /if\(!els\.panes\|\|!TabScrollbar\|\|typeof TabScrollbar\.create!=='function'\)return/);
    assert.match(installSource, /catch\(error\).*reportTabScrollbarFailure\(error\)/s);
    assert.match(workspaceSource, /try\{if\(controller&&typeof controller\.destroy==='function'\)controller\.destroy\(\);\}/);
    assert.match(workspaceSource, /TabScrollbar\.create\(tabs,track,thumb\)/);
    assert.match(workspaceSource, /controller\.scrollActive\(tabs\.querySelector\('\.rw-file-tab\.is-active'\)\)/);
    assert.match(workspaceSource, /clearTabScrollbarControllers\(\)/);
    assert.match(workspaceSource, /installTabScrollbars\(\);\s*persistViewer\(\)/);
    assert.match(scrollbarSource, /new ResizeObserver\(schedule\)/);
    assert.match(scrollbarSource, /resizeObserver\.observe\(scroller\)/);
    assert.match(scrollbarSource, /new MutationObserver\(function \(\)/);
    assert.match(scrollbarSource, /mutationObserver\.observe\(scroller/);
    assert.match(workspaceCss, /\.rw-file-tabs\{[^}]*overflow-x:auto[^}]*scrollbar-width:none/);
    assert.match(workspaceCss, /\.rw-file-tabs::\-webkit-scrollbar\{display:none\}/);
    assert.ok(workspaceCss.includes('.rw-pane-header{position:relative;padding-bottom:0}'));
    assert.ok(workspaceCss.includes('.rw-pane-header.has-tab-overflow{padding-bottom:4px}'));
    assert.ok(workspaceCss.includes('.rw-tab-scrollbar{position:absolute;z-index:5;right:0;bottom:0;left:0;box-sizing:border-box;height:4px;padding:1px 0 0;overflow:hidden;background:var(--v2-border-soft);cursor:pointer;touch-action:none}'));
    assert.ok(!workspaceCss.includes('.rw-tab-scrollbar{position:absolute;z-index:5;top:0'));
    assert.ok(workspaceCss.includes('.rw-tab-scrollbar-thumb{display:block;height:3px;min-width:24px;border-radius:999px;background:var(--v2-muted)'));
    assert.match(workspaceCss, /\.rw-tab-scrollbar\[hidden\]\{display:none!important;height:0;padding:0;border:0;background:transparent;pointer-events:none\}/);
    assert.doesNotMatch(workspaceCss, /\.rw-pane-header\{position:relative;padding-bottom:4px\}/);
    assert.match(workspaceCss, /\.rw-tab-scrollbar:active \.rw-tab-scrollbar-thumb/);
    assert.match(workspaceCss, /\.rw-tab-scrollbar\.is-dragging[^}]*cursor:grabbing/);
    assert.doesNotMatch(workspaceCss, /rotate\([^)]*90deg[^)]*\)[^}]*rw-tab-scrollbar/);
});

test('scrollbar accessibility and cache-safe asset ordering are explicit', () => {
    for (const token of [
        "track.setAttribute('role','scrollbar')",
        "track.tabIndex=-1",
        "track.setAttribute('aria-hidden','true')",
        "track.setAttribute('aria-orientation','horizontal')",
        "track.setAttribute('aria-controls',tabs.id)",
        "track.setAttribute('aria-valuemin','0')",
        "track.setAttribute('aria-valuemax','0')",
        "track.setAttribute('aria-valuenow','0')",
        "track.setAttribute('aria-label','Scroll open artifact tabs')",
    ]) assert.ok(workspaceSource.includes(token), token);
    const moduleIndex = template.indexOf("filename='round_workspace_tab_scrollbar.js'");
    const workspaceIndex = template.indexOf("filename='round_workspace.js'");
    assert.ok(moduleIndex > 0 && workspaceIndex > moduleIndex);
    assert.match(template, /round_workspace\.css', v='20260929-release-acceptance-1'/);
    assert.match(template, /round_workspace_tab_scrollbar\.js', v='20260823-tab-scrollbar-overflow-1'/);
    assert.match(template, /round_workspace\.js', v='20260823-tab-scrollbar-overflow-1'/);
});

test('missing optional scrollbar global or pane container is a safe no-op', () => {
    const invoke = new Function(
        'els', 'TabScrollbar', 'node', 'tabScrollbarControllers', 'reportTabScrollbarFailure',
        `${installSource}; installTabScrollbars(); return tabScrollbarControllers.length;`
    );
    assert.equal(invoke({ panes: null }, null, () => {}, [], () => {}), 0);
    assert.equal(invoke({ panes: { querySelectorAll() { throw new Error('must not run'); } } }, undefined, () => {}, [], () => {}), 0);
});

test('an exception from optional scrollbar creation is contained to that enhancement', () => {
    let removed = 0;
    let warnings = 0;
    const tabs = { id: '', querySelector() { return null; } };
    const track = { hidden: true, setAttribute() {}, append() {}, remove() { removed += 1; } };
    const thumb = { setAttribute() {} };
    const header = { querySelector() { return tabs; }, prepend() {} };
    const els = { panes: { querySelectorAll() { return [header]; } } };
    const invoke = new Function(
        'els', 'TabScrollbar', 'node', 'tabScrollbarControllers', 'reportTabScrollbarFailure',
        `${installSource}; installTabScrollbars(); return tabScrollbarControllers.length;`
    );
    assert.doesNotThrow(() => invoke(
        els,
        { create() { throw new Error('observer setup failed'); } },
        (_tag, className) => className === 'rw-tab-scrollbar' ? track : thumb,
        [],
        () => { warnings += 1; }
    ));
    assert.equal(removed, 1);
    assert.equal(warnings, 1);
});

test('controller remains optional and functional without browser observer APIs', () => {
    const originals = [global.requestAnimationFrame, global.ResizeObserver, global.MutationObserver];
    const frames = [];
    global.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length; };
    global.ResizeObserver = undefined;
    global.MutationObserver = undefined;
    const scroller = new MockElement({ clientWidth: 200, scrollWidth: 500 });
    const track = new MockElement({ clientWidth: 200 });
    const thumb = new MockElement();
    let controller = null;
    try {
        controller = scrollbar.create(scroller, track, thumb);
        while (frames.length) frames.shift()();
        assert.equal(track.hidden, false);
        assert.equal(track.getAttribute('aria-valuemax'), '300');
    } finally {
        if (controller) controller.destroy();
        global.requestAnimationFrame = originals[0];
        global.ResizeObserver = originals[1];
        global.MutationObserver = originals[2];
    }
});

test('production dependency preamble permits a missing optional scrollbar asset', () => {
    const start = workspaceSource.indexOf('  const CFG =');
    const end = workspaceSource.indexOf('  const $ =', start);
    const preamble = workspaceSource.slice(start, end);
    const window = { roundWorkspaceConfig: {}, DiscoveryReportFigures: {} };
    assert.doesNotThrow(() => new Function('window', preamble)(window));
    assert.equal(window.RoundWorkspaceTabScrollbar, undefined);
    assert.match(workspaceSource, /window\.roundWorkspace=\{state,reloadTree:loadExplorer.*renderPanes\(\).*loadExplorer\(\)/s);
});
