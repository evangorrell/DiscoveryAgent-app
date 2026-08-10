const test = require('node:test');
const assert = require('node:assert/strict');
const HSL = require('../da_selection_ui/static/hypothesis_layout.js');

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
