const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const studioPath = path.join(__dirname, '..', 'da_selection_ui', 'static', 'hypothesis_studio.js');
const studioSource = fs.readFileSync(studioPath, 'utf8');

function sourceBetween(startMarker, endMarker) {
    const start = studioSource.indexOf(startMarker);
    const end = studioSource.indexOf(endMarker, start);
    assert.notEqual(start, -1, 'missing ' + startMarker);
    assert.notEqual(end, -1, 'missing ' + endMarker);
    return studioSource.slice(start, end);
}

const buildOverflowSource = sourceBetween(
    '    function buildOverflow(',
    '    function menuItem('
);
const menuItemSource = sourceBetween(
    '    function menuItem(',
    '    function removeMenuItem('
);

class FakeElement {
    constructor(tag, className, text) {
        this.tag = tag;
        this.className = className || '';
        this.text = text || '';
        this.children = [];
        this.attributes = {};
        this.listeners = {};
        this.disabled = false;
        this.title = '';
        this.classList = {
            contains: (name) => this.className.split(/\s+/).includes(name),
            add: (name) => { this.className += ' ' + name; },
        };
    }

    appendChild(child) { this.children.push(child); return child; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    dispatchClick() {
        const listener = this.listeners.click;
        if (listener) listener({ stopPropagation() {} });
    }
}

function makeHarness() {
    let editActive = true;
    let regenerating = false;
    let regenerateCalls = 0;
    let manualEditCalls = 0;
    let closeCalls = 0;
    const activeThreadRequests = {};
    const state = {};
    const el = (tag, className, text) => new FakeElement(tag, className, text);
    const build = new Function(
        'state', 'el', 'esc', 'hasAnyLlmEditActive', 'isRegeneratingHypothesis',
        'runRegenerate', 'openManualEdit', 'removeMenuItem', 'closeAllMenus',
        `${buildOverflowSource}\n${menuItemSource}\nreturn buildOverflow;`
    )(
        state,
        el,
        (value) => value,
        () => editActive,
        () => regenerating,
        () => { regenerateCalls += 1; },
        () => { manualEditCalls += 1; },
        () => new FakeElement('div', 'remove'),
        () => { closeCalls += 1; }
    );
    return {
        build,
        setEditActive(value) { editActive = value; },
        setRegenerating(value) { regenerating = value; },
        counts() { return { regenerateCalls, manualEditCalls, closeCalls }; },
    };
}

test('LLM editing disables Regenerate and Edit manually, then restores Regenerate', () => {
    const harness = makeHarness();
    const hypothesis = { index: 1 };
    const displayed = { index: 1 };
    const versionView = { viewedPosition: 0 };

    const lockedMenu = harness.build(hypothesis, displayed, versionView).children[1];
    const lockedRegenerate = lockedMenu.children[0];
    const lockedManualEdit = lockedMenu.children[1];
    assert.equal(lockedRegenerate.disabled, true);
    assert.equal(lockedManualEdit.disabled, true);
    assert.equal(lockedRegenerate.attributes['aria-disabled'], 'true');
    assert.equal(lockedManualEdit.attributes['aria-disabled'], 'true');
    assert.equal(lockedRegenerate.title, lockedManualEdit.title);

    // Dispatch directly to prove the handler itself also refuses activation;
    // native mouse and keyboard activation are already suppressed by disabled.
    lockedRegenerate.dispatchClick();
    assert.deepEqual(harness.counts(), {
        regenerateCalls: 0,
        manualEditCalls: 0,
        closeCalls: 0,
    });

    harness.setEditActive(false);
    const unlockedMenu = harness.build(hypothesis, displayed, versionView).children[1];
    const unlockedRegenerate = unlockedMenu.children[0];
    assert.equal(unlockedRegenerate.disabled, false);
    assert.equal(unlockedRegenerate.attributes['aria-disabled'], 'false');
    unlockedRegenerate.dispatchClick();
    assert.deepEqual(harness.counts(), {
        regenerateCalls: 1,
        manualEditCalls: 0,
        closeCalls: 1,
    });
});

test('ordinary Chat pending does not create the LLM edit menu lock', () => {
    const harness = makeHarness();
    harness.setEditActive(false);
    const menu = harness.build(
        { index: 1 }, { index: 1 }, { viewedPosition: 0 }
    ).children[1];

    assert.equal(menu.children[0].disabled, false);
    assert.equal(menu.children[1].disabled, false);
    assert.equal(menu.children[2].disabled, false);
});

test('regeneration locks mutation menu actions without locking version browsing', () => {
    const harness = makeHarness();
    harness.setEditActive(false);
    harness.setRegenerating(true);
    const menu = harness.build(
        { index: 1 }, { index: 1 }, { viewedPosition: 0 }
    ).children[1];

    assert.equal(menu.children[0].disabled, true);
    assert.equal(menu.children[1].disabled, true);
    assert.equal(menu.children[0].attributes['aria-disabled'], 'true');
    assert.equal(menu.children[1].attributes['aria-disabled'], 'true');
});
