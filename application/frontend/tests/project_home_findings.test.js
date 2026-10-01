const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../da_selection_ui/static/project_home.js'), 'utf8');

// Execute the real page module/render callback with only the DOM/network boundary stubbed.
async function render(state) {
    class Element {
        constructor() { this.children = []; this.innerHTML = ''; }
        set textContent(text) {
            this.innerHTML = String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
        }
        appendChild(child) { this.children.push(child); return child; }
        addEventListener() {}
        setAttribute() {}
        get html() { return this.innerHTML + this.children.map(child => child.html).join(' '); }
    }
    const nodes = {};
    let onLoad;
    const fetch = async () => ({ ok: true, status: 200, json: async () => ({success: true, ...state}) });
    const document = {
        createElement: () => new Element(),
        createDocumentFragment: () => new Element(),
        getElementById: id => nodes[id] ||= new Element(),
        addEventListener: (event, callback) => { if (event === 'DOMContentLoaded') onLoad = callback; },
    };
    vm.runInNewContext(source, { document, fetch, window: { fetch, DiscoveryReportFigures: {} } });
    await onLoad();
    assert.equal(nodes['ph-error'].hidden, true);
    return nodes['ph-content'].html;
}

test('no completed rounds renders intentional empty state in either Home mode', async () => {
    for (const mode of ['pre_research', 'research_briefing']) {
        const html = await render({mode, recent_rounds: []});
        assert.match(html, /Latest Findings/);
        assert.match(html, /Findings will appear here after the first research round is completed\./);
    }
});

test('completed findings render actual escaped text, status and link, newest first', async () => {
    const html = await render({mode: 'research_briefing', recent_rounds: [
        {round_index: 1, outcome_preview: 'Coastal burden increased < 5%...', destination: '/rounds/?round=1'},
        {round_index: 0, outcome_preview: 'Baseline findings...', destination: '/rounds/?round=0'},
    ]});
    assert.match(html, /Round 1 Complete Coastal burden increased &lt; 5%\.\.\. Open Round 1/);
    assert.ok(html.indexOf('Round 1') < html.indexOf('Round 0'));
    assert.doesNotMatch(html, /Findings will appear|not available/);
});

test('completed round without readable preview renders fallback and retains report navigation', async () => {
    const html = await render({mode: 'research_briefing', recent_rounds: [
        {round_index: 0, outcome_preview: null, destination: '/rounds/?round=0'},
    ]});
    assert.match(html, /A findings preview is not available for this round\. Open the round report to review its results\./);
    assert.match(html, /Round 0 Complete/);
    assert.match(html, /Open Round 0/);
    assert.doesNotMatch(html, /Findings will appear here/);
});
