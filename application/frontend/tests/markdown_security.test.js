const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../da_selection_ui/static/report_figures.js'), 'utf8');
const marked = require('../da_selection_ui/static/vendor/marked-9.1.6.min.js');

for (const failure of ['missing sanitizer', 'throwing sanitizer', 'missing parser', 'throwing parser']) {
    test(`Markdown fails closed: ${failure}`, () => {
        const window = { marked, DOMPurify: { sanitize() { throw new Error('sanitize'); } } };
        if (failure === 'missing sanitizer') delete window.DOMPurify;
        if (failure === 'missing parser') delete window.marked;
        if (failure === 'throwing parser') window.marked = { parse() { throw new Error('parse'); } };
        vm.runInNewContext(source, { window });
        const input = '<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[bad](javascript:alert(1))';
        const html = window.DiscoveryReportFigures.safeMarkdownHtml(input);
        assert.doesNotMatch(html, /<script|<img|href=/i);
        assert.match(html, /&lt;script&gt;/);
    });
}

test('successful rendering inserts only the sanitizer result', () => {
    let received;
    const window = { marked, DOMPurify: { sanitize(html) { received = html; return '<p>sanitized</p>'; } } };
    vm.runInNewContext(source, { window });
    assert.equal(window.DiscoveryReportFigures.safeMarkdownHtml('**science**'), '<p>sanitized</p>');
    assert.match(received, /<strong>science<\/strong>/);
});

test('report renderer construction failure also fails closed', () => {
    const window = { marked: {Renderer() {throw Error('unavailable')}}, DOMPurify: {} };
    vm.runInNewContext(source, { window });
    const element = {innerHTML: ''};
    window.DiscoveryReportFigures.renderReportMarkdown(element, '<img src=x onerror=alert(1)>', []);
    assert.doesNotMatch(element.innerHTML, /<img/);
    assert.match(element.innerHTML, /&lt;img/);
});
