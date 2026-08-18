const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(
  path.join(root, 'da_selection_ui/static/report_figures.js'),
  'utf8',
);
const context = { window: {} };
vm.runInNewContext(source, context);
const render = context.window.DiscoveryReportFigures.renderFallbackMarkdown;

test('dash unordered Markdown renders as a real list', () => {
  assert.equal(render('- One\n- Two'), '<ul><li>One</li><li>Two</li></ul>');
});

test('asterisk unordered Markdown renders as a real list', () => {
  assert.equal(render('* One\n* Two'), '<ul><li>One</li><li>Two</li></ul>');
});

test('numbered Markdown renders as an ordered list', () => {
  assert.equal(render('1. One\n2. Two'), '<ol><li>One</li><li>Two</li></ol>');
  assert.equal(
    render('9. Nine\n\n10. Ten'),
    '<ol start="9"><li>Nine</li><li>Ten</li></ol>',
  );
});

test('Project Home can preserve its established report heading offset', () => {
  assert.equal(render('# Report', { headingOffset: 1 }), '<h2>Report</h2>');
});

test('nested unordered and ordered lists retain an additional level', () => {
  assert.equal(
    render('- Parent\n  - Child\n    1. Grandchild'),
    '<ul><li>Parent<ul><li>Child<ol><li>Grandchild</li></ol></li></ul></li></ul>',
  );
  assert.equal(
    render('1. Parent\n   1. Child'),
    '<ol><li>Parent<ol><li>Child</li></ol></li></ol>',
  );
});

test('mixed ordered and unordered nesting stays structurally scoped', () => {
  assert.equal(
    render('1. Parent\n   - Child\n     1. Grandchild\n2. Next'),
    '<ol><li>Parent<ul><li>Child<ol><li>Grandchild</li></ol></li></ul></li><li>Next</li></ol>',
  );
});

test('wrapped list text remains inside the same list item', () => {
  assert.equal(
    render('- A long scientific recommendation\n  that continues on a second source line.'),
    '<ul><li>A long scientific recommendation<br>that continues on a second source line.</li></ul>',
  );
  assert.doesNotMatch(render('alpha-beta prose'), /<ul>|<ol>/);
});

test('shared report CSS owns matching first-level ul and ol indentation', () => {
  const sharedCss = fs.readFileSync(
    path.join(root, 'da_selection_ui/static/style.css'),
    'utf8',
  );
  const workspaceCss = fs.readFileSync(
    path.join(root, 'da_selection_ui/static/round_workspace.css'),
    'utf8',
  );
  assert.match(
    sharedCss,
    /\.v2-report-markdown ul,\s*\.v2-report-markdown ol\s*\{[\s\S]*?padding-inline-start: var\(--v2-report-list-indent\);[\s\S]*?list-style-position: outside;/,
  );
  assert.match(sharedCss, /--v2-report-list-indent: 2rem;/);
  assert.match(sharedCss, /\.v2-report-markdown li > ul,[\s\S]*?padding-inline-start: 1\.55rem;/);
  assert.doesNotMatch(workspaceCss, /\.rw-markdown-document ul,\.rw-markdown-document ol\{/);
});
