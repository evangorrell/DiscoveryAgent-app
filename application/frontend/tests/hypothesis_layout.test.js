const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const HSL = require('../da_selection_ui/static/hypothesis_layout.js');

const studioSource = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'hypothesis_studio.js'),
    'utf8'
);
const primeStart = studioSource.indexOf('    function primeHypothesisExpansionPanel(');
const primeEnd = studioSource.indexOf('    function manualEditRequest(', primeStart);
assert.notEqual(primeStart, -1);
assert.notEqual(primeEnd, -1);
const primeSource = studioSource.slice(primeStart, primeEnd);

function runExpansionPrime({ expanded = {}, height = 300, ratio = 0.3 } = {}) {
    const state = { expanded, regenerationRequests: {} };
    const hypothesisSplit = { height, ratio, temporaryExpandedRatio: null };
    const prime = new Function(
        'state', 'HSL', 'els', 'hypothesisSplit',
        'DEFAULT_HYPOTHESIS_PANEL_RATIO', 'EXPANDED_VIEW_DEFAULT_RATIO',
        `${primeSource}\nreturn primeHypothesisExpansionPanel;`
    )(
        state,
        HSL,
        { workspaceMain: { getBoundingClientRect: () => ({ height: 1000 }) } },
        hypothesisSplit,
        0.3,
        0.5
    );
    prime(1, true);
    return hypothesisSplit.temporaryExpandedRatio;
}

const cards = [1, 2, 3, 4].map((index) => ({ index }));

test('fourth card defaults to the center column', () => {
    assert.equal(HSL.singleOverflowColumn(cards, {}), 2);
});

test('expanded center prefers a collapsed left card, then right', () => {
    assert.equal(HSL.singleOverflowColumn(cards, { 2: true }), 1);
    assert.equal(HSL.singleOverflowColumn(cards, { 1: true, 2: true }), 3);
});

test('center remains stable while it is collapsed', () => {
    assert.equal(HSL.singleOverflowColumn(cards, { 1: true }), 2);
    assert.equal(HSL.singleOverflowColumn(cards, { 3: true }), 2);
    assert.equal(HSL.singleOverflowColumn(cards, { 1: true, 3: true }), 2);
});

test('all expanded falls back to the center column', () => {
    assert.equal(HSL.singleOverflowColumn(cards, { 1: true, 2: true, 3: true }), 2);
});

test('the overflow rule is scoped to exactly four cards', () => {
    assert.equal(HSL.singleOverflowColumn(cards.slice(0, 3), {}), null);
    assert.equal(HSL.singleOverflowColumn(cards.concat({ index: 5 }), {}), null);
});

const fiveCards = [1, 2, 3, 4, 5].map((index) => ({ index }));

test('five-card packing is inactive while the top row is fully collapsed', () => {
    assert.equal(HSL.packedOverflowPreferences(fiveCards, {}), null);
});

test('five-card packing fills both remaining collapsed top columns', () => {
    assert.deepEqual(HSL.packedOverflowPreferences(fiveCards, { 1: true }), [2, 3]);
    assert.deepEqual(HSL.packedOverflowPreferences(fiveCards, { 2: true }), [1, 3]);
    assert.deepEqual(HSL.packedOverflowPreferences(fiveCards, { 3: true }), [1, 2]);
});

test('after the only collapsed column, the next card uses measured shortest-column packing', () => {
    assert.deepEqual(HSL.packedOverflowPreferences(fiveCards, { 1: true, 2: true }), [3, null]);
    assert.deepEqual(HSL.packedOverflowPreferences(fiveCards, { 1: true, 3: true }), [2, null]);
    assert.deepEqual(HSL.packedOverflowPreferences(fiveCards, { 2: true, 3: true }), [1, null]);
    assert.deepEqual(HSL.packedOverflowPreferences(fiveCards, { 1: true, 2: true, 3: true }), [null, null]);
});

test('expanded-view floor moves a divider above halfway down to halfway', () => {
    assert.equal(HSL.hypothesisExpansionPanelRatio(0.31, 0.5), 0.5);
});

test('expanded-view floor never moves a divider already below halfway upward', () => {
    assert.equal(HSL.hypothesisExpansionPanelRatio(0.62, 0.5), 0.62);
});

test('normal full-hypothesis expansion is visible and uses the expanded rail floor', () => {
    assert.equal(HSL.isOpeningFirstVisibleExpansion({}, {}, 2, true), true);
    assert.equal(HSL.hypothesisExpansionPanelRatio(0.31, 0.5), 0.5);
    assert.deepEqual(HSL.visibleExpandedState({ 2: true }, {}), { 2: true });
});

test('shared full-view priming gives manual edit the same halfway floor', () => {
    assert.equal(runExpansionPrime(), 0.5);
    assert.equal(runExpansionPrime({ height: 620, ratio: 0.62 }), 0.62);
    assert.equal(runExpansionPrime({ expanded: { 2: true } }), null);
});

test('regeneration does not suppress the same card full expansion', () => {
    const stored = { 1: true };
    const regenerating = { 1: { requestId: 7 } };
    assert.deepEqual(HSL.visibleExpandedState(stored, regenerating), { 1: true });
    assert.equal(HSL.isOpeningFirstVisibleExpansion(stored, regenerating, 2, true), false);
});

test('starting regeneration leaves a different expanded hypothesis visible', () => {
    const expanded = { 2: true };
    assert.deepEqual(
        HSL.visibleExpandedState(expanded, { 1: { requestId: 7 } }),
        { 2: true },
    );
});

test('regeneration completion does not collapse a different expanded hypothesis', () => {
    const expanded = { 2: true };
    assert.deepEqual(HSL.visibleExpandedState(expanded, {}), { 2: true });
});

test('regeneration cancellation does not collapse a different expanded hypothesis', () => {
    const expanded = { 2: true };
    assert.deepEqual(HSL.visibleExpandedState(expanded, {}), { 2: true });
});

test('closing the only full hypothesis during regeneration collapses the rail', () => {
    const regenerating = { 1: { requestId: 7 } };
    assert.deepEqual(
        HSL.visibleExpandedState({ 1: false, 2: false }, regenerating),
        {},
    );
});

test('regeneration and expansion remain scoped to exact hypothesis indexes', () => {
    const expanded = { 1: true, 2: true, 3: true };
    const regenerating = { 1: { requestId: 7 }, 3: { requestId: 8 } };
    assert.deepEqual(HSL.visibleExpandedState(expanded, regenerating), expanded);
});
