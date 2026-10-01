const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ui = path.join(__dirname, '..', 'da_selection_ui');
const source = fs.readFileSync(path.join(ui, 'static/hypothesis_studio.js'), 'utf8');
const css = fs.readFileSync(path.join(ui, 'static/hypothesis_studio.css'), 'utf8');
function between(start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from);
    return source.slice(from, to);
}

function element(tag, classes = '', text = '') {
    const names = new Set(classes.split(' ').filter(Boolean));
    return {
        tag, children: [], dataset: {}, style: {}, attributes: {}, listeners: {}, textContent: text,
        classList: { add: (...xs) => xs.forEach(x => names.add(x)), remove: x => names.delete(x), contains: x => names.has(x) },
        appendChild(child) { this.children.push(child); return child; },
        setAttribute(key, value) { this.attributes[key] = value; },
        addEventListener(type, fn) { this.listeners[type] = fn; },
    };
}
function find(node, name) {
    if (node.classList.contains(name)) return node;
    return node.children.map(child => find(child, name)).find(Boolean);
}

function harness() {
    const state = {
        readOnly: false, readyToStart: true, currentRoundIndex: 0,
        rounds: [{ round_index: 0, is_current: true, target_round_number: 1 }],
        focusIndex: 1, expanded: {}, manualEditIndex: null,
    };
    const build = new Function('state', 'el', `
        const esc = String;
        const renderMarkdown = String;
        const hypothesisBodyMarkdown = h => h.full_text;
        const cardTitle = h => h.title;
        const isRegeneratingHypothesis = () => false;
        const regenerationViewFor = h => ({ results: [{}, {}, {}], viewedSnapshot: h, viewedPosition: 2 });
        const buildOverflow = () => el('div', 'hs-card-overflow');
        const regenerationVersionControls = (h, view, compact) => {
            const nav = el('div', 'hs-regeneration-nav');
            nav.appendChild(el('button', 'hs-regeneration-arrow', '‹'));
            nav.appendChild(el('span', 'hs-regeneration-count', compact ? '3 / 3' : 'Version 3 of 3'));
            nav.appendChild(el('button', 'hs-regeneration-arrow', '›'));
            return nav;
        };
        ${between('    function renderCard(', '    function regenerationVersionControls(')}
        ${between('    function selectedRoundNumber(', '    function openStartRoundModal(')}
        return {renderCard, selectedRoundNumber};
    `)(state, element);
    return { ...build, state };
}

test('selected card footer has separate version/action groups and no round CTA', () => {
    const h = harness();
    const card = h.renderCard({ index: 1, title: 'A naturally wrapping scientific hypothesis title' }, 1);
    const footer = find(card, 'hs-card-controls');
    assert.deepEqual(footer.children.map(n => n.classList.contains('hs-regeneration-nav') ? 'versions' : 'actions'), ['versions', 'actions']);
    assert.deepEqual(footer.children[0].children.map(n => n.textContent), ['‹', '3 / 3', '›']);
    assert.deepEqual(footer.children[1].children.map(n => n.textContent), ['View full hypothesis', 'Selected']);
    assert.equal(find(card, 'hs-card-start-round'), undefined);
    assert.equal(find(card, 'hs-card-overflow'), undefined); // selected lock preserved
});

test('unselected footer keeps selection action, semantic buttons and existing overflow menu', () => {
    const h = harness();
    const card = h.renderCard({ index: 2, title: 'Another long scientific title' }, 1);
    const actions = find(card, 'hs-card-actions').children;
    assert.deepEqual(actions.map(n => n.textContent), ['View full hypothesis', 'Select for next round']);
    assert.ok(actions.every(n => n.tag === 'button' && n.type === 'button'));
    assert.ok(find(card, 'hs-card-overflow'));
    assert.equal(find(card, 'hs-card-title').textContent, 'Another long scientific title');
    assert.equal(find(card, 'hs-regeneration-count').textContent, '3 / 3');
});

test('Studio retains current next-round calculation, including Round 5 and fallback', () => {
    const h = harness();
    assert.equal(h.selectedRoundNumber(null), 1);
    h.state.rounds = [{ round_index: 4, is_current: true, target_round_number: 5 }];
    h.state.currentRoundIndex = 4;
    assert.equal(h.selectedRoundNumber(null), 5);
    h.state.rounds = [];
    assert.equal(h.selectedRoundNumber(null), 5);
    assert.equal(h.selectedRoundNumber({ project_round: 7 }), 7);
});

test('read-only historical cards retain no selection or overflow action', () => {
    const h = harness();
    h.state.readOnly = true;
    const card = h.renderCard({ index: 2, title: 'History' }, 1);
    assert.equal(find(card, 'hs-card-select'), undefined);
    assert.equal(find(card, 'hs-card-overflow'), undefined);
});

test('expanded cards separate their document from grouped footer controls', () => {
    for (const selected of [1, null]) {
        const h = harness();
        h.state.expanded[1] = true;
        const card = h.renderCard({index: 1, title: 'Long document', full_text: '1. An ordered finding\n\nFinal line.'}, selected);
        assert.deepEqual(card.children.map(n => n.classList.contains('hs-card-head') ? 'head' :
            n.classList.contains('hs-card-details') ? 'document' : 'footer'), ['head', 'document', 'footer']);
        assert.match(find(card, 'hs-hypothesis-markdown').textContent, /Final line/);
        assert.equal(find(card, 'hs-card-expand').textContent, 'Hide full hypothesis');
        assert.equal(find(card, 'hs-regeneration-count').textContent, 'Version 3 of 3');
        assert.equal(find(card, 'hs-card-expand').attributes['aria-expanded'], 'true');
        assert.equal(find(card, 'hs-card-start-round'), undefined);
        assert.ok(find(card, selected ? 'hs-card-selected-static' : 'hs-card-select'));
        h.state.expanded[1] = false;
        assert.equal(find(h.renderCard({index: 1, title: 'Long document'}, selected), 'hs-card-details'), undefined);
    }
});

test('expanded documents are unscaled, lists are indented, and footer height is reserved', () => {
    assert.match(css, /\.hs-card\.is-expanded\.is-dimmed\s*\{[^}]*transform: none/);
    assert.match(css, /\.hs-hypothesis-markdown :is\(ol, ul\)\s*\{[^}]*padding-inline-start: 1\.8em/);
    assert.match(css, /\.hs-card-controls\s*\{[^}]*flex: 0 0 auto/);
    const sticky = css.slice(css.indexOf('.hs-hypothesis-surface.has-expanded-card .hs-card.is-expanded .hs-card-controls {'));
    assert.match(sticky, /position: sticky;[\s\S]*?margin: 0;/);
    assert.doesNotMatch(sticky.slice(0, sticky.indexOf('}')), /margin-(left|right|bottom): -/);
    assert.match(source, /toggle\.focus\(\{ preventScroll: true \}\)/);
});

test('wrapping footer groups preserve action labels and usable widths', () => {
    assert.match(css, /\.hs-card-controls\s*\{[^}]*flex-wrap: wrap;/);
    assert.match(css, /\.hs-card-actions\s*\{[^}]*flex-wrap: wrap;/);
    assert.match(css, /\.hs-card-actions > \.hs-card-action\s*\{[^}]*white-space: nowrap;/);
    assert.match(css, /\.hs-regeneration-nav\s*\{[^}]*flex: 0 0 auto;/);
    assert.match(css, /@container hs-hypotheses \(max-width: 640px\)/);
});

test('compact rows align bottom footers and use compact-only sizing without altering expanded packing', () => {
    assert.match(source, /if \(!grid\.querySelector\('\.hs-card\.is-expanded'\)\) return;/);
    assert.match(source, /HSL\.packCards\(cards\.map\(card => card\.offsetHeight\)/);
    assert.match(css, /\.hs-hypothesis-surface\.is-static-row \.hs-card-grid \{ align-items: stretch; \}/);
    assert.match(css, /\.hs-card-controls\s*\{[^}]*margin-top: auto;/);
    assert.match(css, /\.hs-card\.is-collapsed \.hs-card-controls\s*\{[^}]*justify-content: space-between;[^}]*padding-top: 8px;/);
    assert.match(css, /\.hs-card\.is-collapsed \.hs-regeneration-arrow\s*\{[^}]*width: 24px;[^}]*height: 24px;/);
});

test('page CTA is before Back to Projects, uses a real button and the existing launch handler', () => {
    const base = fs.readFileSync(path.join(ui, 'templates/base.html'), 'utf8');
    assert.ok(base.indexOf('id="project-start-round"') < base.indexOf('Back to Projects'));
    assert.match(base, /<button type="button" id="project-start-round" class="v2-btn v2-btn-primary" data-start-round hidden>/);
    assert.match(source, /Number\(els.startRound.getAttribute\('data-start-round-number'\)\)/);
    assert.match(source, /Number.isInteger\(round\) && round >= 1\) openStartRoundModal\(round\)/);
    assert.match(source, /document.activeElement === els.startRound\s*\? els.startRound/);
});
