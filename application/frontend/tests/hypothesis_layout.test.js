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

test('three wide cards start left to right even with a long expanded middle card', () => {
    assert.deepEqual(HSL.packCards([180, 5000, 220], 3, 14), [
        {column: 1, top: 0}, {column: 2, top: 0}, {column: 3, top: 0},
    ]);
});

test('medium layout places Card 3 directly below compact Card 1, not expanded Card 2', () => {
    assert.deepEqual(HSL.packCards([180, 5000, 220], 2, 14), [
        {column: 1, top: 0}, {column: 2, top: 0}, {column: 1, top: 194},
    ]);
});

test('expanding Card 1 instead fills the available space beneath Card 2', () => {
    assert.deepEqual(HSL.packCards([5000, 200, 220], 2, 14)[2], {column: 2, top: 214});
});

test('single column stays in numbered order with exact natural spacing', () => {
    assert.deepEqual(HSL.packCards([180, 5000, 220], 1, 14), [
        {column: 1, top: 0}, {column: 1, top: 194}, {column: 1, top: 5208},
    ]);
});

test('four and five-card overflow uses shortest columns rather than rigid rows', () => {
    assert.deepEqual(HSL.packCards([180, 5000, 200, 150, 190], 3, 14).slice(3), [
        {column: 1, top: 194}, {column: 3, top: 214},
    ]);
    assert.deepEqual(HSL.packCards([180, 5000, 200, 150], 3, 14)[3], {column: 1, top: 194});
});

test('collapse and resize recompute positions without stale expanded offsets', () => {
    const heights = [180, 200, 220];
    assert.deepEqual(HSL.packCards(heights, 2, 14)[2], {column: 1, top: 194});
    assert.deepEqual(HSL.packCards(heights, 3, 14)[2], {column: 3, top: 0});
    assert.deepEqual(heights, [180, 200, 220]);
});

test('ten varying-height cards never overlap in a column and ties stay deterministic', () => {
    const heights = [180, 190, 5000, 240, 160, 180, 800, 200, 190, 220];
    for (const columns of [1, 2, 3]) {
        const positions = HSL.packCards(heights, columns, 14);
        const bottoms = Array(columns).fill(-14);
        positions.forEach((p, i) => {
            assert.equal(p.top, bottoms[p.column - 1] + 14);
            bottoms[p.column - 1] = p.top + heights[i];
        });
    }
});

test('packing retains direct DOM children and measures untransformed heights after width assignment', () => {
    const sync = studioSource.slice(studioSource.indexOf('    function syncCardLayout('), studioSource.indexOf('    function renderCard('));
    assert.match(sync, /cards\.map\(card => card\.offsetHeight\)/);
    assert.match(sync, /overflowLayoutObserver\.observe\(grid\)/);
    assert.match(sync, /requestAnimationFrame\(syncPositions\)/);
    assert.doesNotMatch(sync, /appendChild|insertBefore|\.sort\(|tabIndex|position\s*=\s*['"]absolute/);
});

test('automatic pane measurement is independent of scroll and only includes the actual first row minimum', () => {
    const start = studioSource.indexOf('    function rememberCollapsedHypothesisHeight(');
    const end = studioSource.indexOf('    function hypothesisPanelContextKey(', start);
    const cards = [{offsetHeight:200}, {offsetHeight:240}, {offsetHeight:220}];
    const grid = {children:cards, offsetHeight:474};
    const surface = {scrollTop:2500, querySelector:()=>grid};
    const els = {cards:{querySelector:()=>surface}, workspaceMain:{classList:{contains:()=>false}}};
    const style = e => e===grid ? {getPropertyValue:()=> '2'} : {paddingTop:'6px',paddingBottom:'10px'};
    const split = {};
    const functions = new Function('els','hypothesisSplit','getComputedStyle','window','hypothesisPanelCollapsedHeightKey',
        studioSource.slice(start,end)+'\nreturn {rememberCollapsedHypothesisHeight,completeCollapsedHypothesisHeight};'
    )(els,split,style,{getComputedStyle:style,localStorage:{setItem(){}}},'fixture');
    functions.rememberCollapsedHypothesisHeight();
    assert.equal(split.collapsedHeight,256);
    assert.equal(functions.completeCollapsedHypothesisHeight(),492);
    surface.scrollTop=0;
    assert.equal(functions.completeCollapsedHypothesisHeight(),492);
});

test('stale oversized pane minimum cannot push chat and composer outside the workspace', () => {
    const start = studioSource.indexOf('    function hypothesisPanelLimits(');
    const end = studioSource.indexOf('    function applyHypothesisPanelHeight(', start);
    const limits = new Function('els','hypothesisSplit','visibleOuterHeight','MIN_TRANSCRIPT_PREVIEW_HEIGHT',
        studioSource.slice(start,end)+'\nreturn hypothesisPanelLimits();'
    )({workspaceMain:{getBoundingClientRect:()=>({height:800})},chatScope:40,composer:100,workspaceDivider:12},
        {collapsedHeight:2505},node=>node,88);
    assert.deepEqual(limits,{min:560,max:560,workspaceHeight:800});
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
