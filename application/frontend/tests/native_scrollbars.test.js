const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../da_selection_ui');
test('shared native scrollbars keep OS visibility preferences and accessible native interaction', () => {
    const css = fs.readFileSync(path.join(root, 'static/native_scrollbars.css'), 'utf8');
    assert.match(css, /scrollbar-width: thin/);
    assert.match(css, /scrollbar-color: auto/);
    assert.match(css, /forced-colors: active/);
    assert.match(css, /pointer: coarse/);
    assert.doesNotMatch(css, /scrollbar-width:\s*none|opacity:\s*0|display:\s*none|transition:/);
    assert.match(fs.readFileSync(path.join(root, 'templates/base.html'), 'utf8'), /native_scrollbars\.css/);
    assert.doesNotMatch(fs.readFileSync(path.join(root, 'static/hypothesis_studio.css'), 'utf8'), /::-webkit-scrollbar/);
});
