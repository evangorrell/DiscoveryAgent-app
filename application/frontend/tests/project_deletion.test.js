'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.join(__dirname, '..', 'da_selection_ui');
const source = fs.readFileSync(path.join(root, 'static', 'projects.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'templates', 'projects.html'), 'utf8');

function functionBlock(name, nextName) {
    const start = source.indexOf(`function ${name}`);
    assert.notEqual(start, -1);
    const end = source.indexOf(`function ${nextName}`, start + 1);
    return source.slice(start, end === -1 ? source.length : end);
}

test('confirmation distinguishes the complete project from external input data', () => {
    assert.match(template, /id="delete-project-title">Delete this project\?<\/h2>/);
    assert.match(source, /deleteTitle\.textContent = 'Delete this project\?'/);
    assert.doesNotMatch(template, /Remove this project\?/);
    assert.doesNotMatch(source, /deleteTitle\.textContent = 'Remove this project\?'/);
    assert.match(template, /complete DiscoveryAgent project workspace\s+will be moved to Trash\./);
    assert.match(template, /External DiscoveryAgent Data sources will remain in their original locations\./);
    assert.doesNotMatch(template, /editable.*path|delete-project-path/i);
});

test('delete submission is single-flight and exposes an accessible busy state', () => {
    const confirm = functionBlock('confirmDeleteProject', 'resetProjectModalScroll');
    assert.match(confirm, /deleteConfirm\.disabled = true/);
    assert.match(confirm, /setAttribute\('aria-busy', 'true'\)/);
    assert.match(confirm, /removeAttribute\('aria-busy'\)/);
    assert.match(confirm, /method: 'DELETE'/);
});

test('failed deletion keeps the modal and visible project list intact', () => {
    const confirm = functionBlock('confirmDeleteProject', 'resetProjectModalScroll');
    const failure = confirm.slice(confirm.indexOf('if (!(resp.ok'));
    const success = failure.indexOf('els.deleteOverlay.hidden = true');
    const failureReturn = failure.indexOf('return;');
    assert.ok(failureReturn !== -1 && failureReturn < success);
    assert.equal(failure.slice(0, failureReturn).includes('loadProjects()'), false);
    assert.ok(confirm.indexOf('loadProjects()') > confirm.indexOf('els.deleteOverlay.hidden = true'));
});
