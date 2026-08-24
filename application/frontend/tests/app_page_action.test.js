const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'app_page_action.js'),
    'utf8',
);

function classList() {
    const values = new Set();
    return {
        add(value) { values.add(value); },
        remove(value) { values.delete(value); },
        toggle(value, force) { if (force) values.add(value); else values.delete(value); },
        contains(value) { return values.has(value); },
    };
}

function surface() {
    const properties = new Map();
    return {
        classList: classList(),
        style: {
            setProperty(name, value) { properties.set(name, value); },
            removeProperty(name) { properties.delete(name); },
            getPropertyValue(name) { return properties.get(name) || ''; },
        },
    };
}

test('shared page action stays in flow for short content and becomes sticky only for a long document page', () => {
    const frames = [];
    const listeners = new Map();
    const documentListeners = new Map();
    const resizeObservers = [];
    const mutationObservers = [];
    const root = Object.assign(surface(), {
        scrollHeight: 520,
        getBoundingClientRect() { return { top: 100 }; },
    });
    const content = surface();
    const action = Object.assign(surface(), { hidden: false, offsetHeight: 58 });

    class ResizeObserver {
        constructor(callback) { this.callback = callback; this.targets = []; resizeObservers.push(this); }
        observe(target) { this.targets.push(target); }
        disconnect() { this.disconnected = true; }
    }
    class MutationObserver {
        constructor(callback) { this.callback = callback; mutationObservers.push(this); }
        observe(target, options) { this.target = target; this.options = options; }
        disconnect() { this.disconnected = true; }
    }

    const window = {
        innerHeight: 900,
        scrollY: 0,
        ResizeObserver,
        MutationObserver,
        requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
        cancelAnimationFrame() {},
        addEventListener(name, callback) { listeners.set(name, callback); },
        removeEventListener(name) { listeners.delete(name); },
    };
    const document = {
        documentElement: { clientHeight: 900 },
        addEventListener(name, callback) { documentListeners.set(name, callback); },
        removeEventListener(name) { documentListeners.delete(name); },
    };
    vm.runInNewContext(source, { window, document }, { filename: 'app_page_action.js' });
    const layout = window.DiscoveryAgentAppPageAction.create({ root, content, action });
    const flush = () => { const callback = frames.shift(); assert.ok(callback); callback(); };

    flush();
    assert.equal(action.classList.contains('is-sticky'), false);
    assert.equal(root.classList.contains('has-sticky-action'), false);
    assert.equal(root.style.getPropertyValue('--v2-sticky-action-tray-height'), '');
    assert.equal(root.style.getPropertyValue('--v2-sticky-action-reserve'), '');

    root.scrollHeight = 920;
    resizeObservers[0].callback();
    flush();
    assert.equal(action.classList.contains('is-sticky'), true);
    assert.equal(root.classList.contains('has-sticky-action'), true);
    assert.equal(root.style.getPropertyValue('--v2-sticky-action-tray-height'), '58px');
    assert.equal(root.style.getPropertyValue('--v2-sticky-action-reserve'), '70px');

    root.scrollHeight = 500;
    listeners.get('resize')();
    flush();
    assert.equal(action.classList.contains('is-sticky'), false);
    assert.equal(root.style.getPropertyValue('--v2-sticky-action-tray-height'), '');
    assert.equal(root.style.getPropertyValue('--v2-sticky-action-reserve'), '');

    root.scrollHeight = 940;
    mutationObservers[0].callback();
    flush();
    assert.equal(action.classList.contains('is-sticky'), true);
    action.hidden = true;
    mutationObservers[0].callback();
    flush();
    assert.equal(action.classList.contains('is-sticky'), false);
    assert.equal(root.style.getPropertyValue('--v2-sticky-action-tray-height'), '');
    assert.equal(root.style.getPropertyValue('--v2-sticky-action-reserve'), '');

    assert.deepEqual(resizeObservers[0].targets, [root, content, action]);
    assert.equal(mutationObservers[0].target, root);
    assert.equal(mutationObservers[0].options.subtree, true);
    assert.equal(mutationObservers[0].options.childList, true);
    assert.deepEqual(Array.from(mutationObservers[0].options.attributeFilter), ['hidden', 'aria-expanded']);
    assert.ok(listeners.has('focus'));
    assert.ok(listeners.has('pageshow'));
    assert.ok(documentListeners.has('visibilitychange'));
    assert.equal(listeners.has('scroll'), false);
    layout.destroy();
    assert.equal(resizeObservers[0].disconnected, true);
    assert.equal(mutationObservers[0].disconnected, true);
    assert.equal(listeners.has('focus'), false);
    assert.equal(documentListeners.has('visibilitychange'), false);
});

test('page-scroll helper is bounded, remeasures restored pages, and never duplicates the action', () => {
    assert.match(source, /document\.fonts && document\.fonts\.ready/);
    assert.match(source, /document\.documentElement[\s\S]*clientHeight/);
    assert.match(source, /window\.addEventListener\('resize', refresh/);
    assert.match(source, /window\.addEventListener\('focus', refresh/);
    assert.match(source, /document\.addEventListener\('visibilitychange', refresh/);
    assert.match(source, /new window\.ResizeObserver\(refresh\)/);
    assert.match(source, /new window\.MutationObserver\(refresh\)/);
    assert.doesNotMatch(source, /setInterval|addEventListener\('scroll'|cloneNode|appendChild|insertAdjacent/);
    assert.match(source, /const shouldStick = naturalHeight > availableHeight \+ 1/);
    assert.match(source, /config\.safetyGap == null \? 12 : config\.safetyGap/);
    assert.match(source, /const trayHeight = Math\.ceil\(action\.offsetHeight\)/);
    assert.match(source, /trayHeight \+ safetyGap \+ 'px'/);
});
