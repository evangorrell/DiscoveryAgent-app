const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'report_figures.js'),
    'utf8',
);

class MockNode {
    constructor(tag) {
        this.tagName = tag;
        this.className = '';
        this.attributes = new Map();
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) || null; }
    replaceWith(node) { this.replacement = node; }
}

function harness() {
    let renderedHtml = '';
    const placeholder = new MockNode('div');
    placeholder.setAttribute('data-rw-report-pdf-token', 'rw_fig_0');
    const element = {
        set innerHTML(value) { renderedHtml = value; },
        get innerHTML() { return renderedHtml; },
        querySelectorAll(selector) {
            if (selector === '[data-rw-report-pdf-token]') return [placeholder];
            return [];
        },
    };
    const window = {
        marked: {
            Renderer: class {},
            parse(_text, options) {
                return options.renderer.image(
                    'rw-report-figure:rw_fig_0', '', 'Polarization over time',
                );
            },
        },
        DOMPurify: { sanitize: value => value },
    };
    vm.runInNewContext(source, {
        window,
        document: { createElement: tag => new MockNode(tag) },
    });
    return { api: window.DiscoveryReportFigures, element, placeholder };
}

test('trusted PDF report figures are hydrated as lazy inline frames', () => {
    const { api, element, placeholder } = harness();
    api.renderReportMarkdown(element, 'ignored', [{
        token: 'rw_fig_0',
        alt: 'Polarization over time',
        available: true,
        url: '/api/round_workspace_artifact?source=trusted&path=plot.pdf',
        media_type: 'pdf',
    }]);

    assert.match(element.innerHTML, /data-rw-report-pdf-token="rw_fig_0"/);
    assert.equal(placeholder.replacement.tagName, 'iframe');
    assert.equal(placeholder.replacement.className, 'rw-report-pdf');
    assert.equal(placeholder.replacement.loading, 'lazy');
    assert.equal(placeholder.replacement.referrerPolicy, 'no-referrer');
    assert.equal(
        placeholder.replacement.src,
        '/api/round_workspace_artifact?source=trusted&path=plot.pdf',
    );
    assert.equal(placeholder.replacement.title, 'Figure: Polarization over time');
});
