const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'da_selection_ui');
const template = fs.readFileSync(path.join(root, 'templates', 'project_skills.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'static', 'project_skills.js'), 'utf8');

test('Skills uploader advertises exactly the three supported file forms', () => {
    assert.match(template, /Drop a \.skill, \.zip, or SKILL\.md file/);
    assert.match(template, /id="skill-import-file" type="file" accept="\.skill,\.zip,\.md" hidden/);
    assert.doesNotMatch(template, /accept="[^"]*application\/zip/);
    assert.doesNotMatch(template, /\bmultiple\b/);
    assert.match(template, /project_skills\.js', v='20260823-active-round-skill-import-1'/);
});

test('client accepts archive or plain skill inputs by filename without trusting MIME', () => {
    const gate = script.slice(
        script.indexOf('function isSupportedSkillFilename'),
        script.indexOf('async function previewFile'),
    );
    assert.match(gate, /name === 'SKILL\.md'/);
    assert.match(gate, /lower\.endsWith\('\.skill'\)/);
    assert.match(gate, /lower\.endsWith\('\.zip'\)/);
    assert.doesNotMatch(gate, /endsWith\('\.md'\)/);
    assert.doesNotMatch(gate, /file\.type|mime|contentType/i);
    assert.match(script, /Choose a \.skill, \.zip, or SKILL\.md file\./);
});

test('browse cancellation is silent while multi-file selection stays rejected', () => {
    const selection = script.slice(
        script.indexOf('function previewSelectedFiles'),
        script.indexOf('function beginFileDrag'),
    );
    const cancel = selection.indexOf('files.length === 0');
    const multi = selection.indexOf('files.length !== 1');
    assert.ok(cancel >= 0 && cancel < multi);
    assert.match(selection, /files\.length === 0[\s\S]*setImportError\(null\);[\s\S]*return;/);
    assert.match(selection, /Choose exactly one \.skill, \.zip, or SKILL\.md file\./);
});

test('browse and drag-and-drop reuse the same one-file preview path and keyboard behavior', () => {
    assert.match(script, /elements\.file\.addEventListener\('change', \(\) => previewSelectedFiles\(elements\.file\.files\)\)/);
    assert.match(script, /previewSelectedFiles\(event\.dataTransfer && event\.dataTransfer\.files\)/);
    assert.match(script, /event\.key !== 'Enter' && event\.key !== ' '/);
    assert.match(script, /body\.append\('skill', file, file\.name\)/);
    assert.match(script, /body\.append\('skill', selectedFile, selectedFile\.name\)/);
});

test('active rounds keep import available while disabling only removal', () => {
    assert.doesNotMatch(template, /skills-running-note|Skills cannot be changed while a research round is running/);
    assert.match(script, /elements\.open\.disabled = !statusResolved/);
    assert.match(script, /button\.disabled = !statusResolved \|\| running/);
    assert.doesNotMatch(script, /if \(!statusResolved \|\| running\) return/);
    assert.doesNotMatch(script, /if \(!selectedFile \|\| running\) return/);
    assert.match(script, /async function reconcileRemovalBlocked/);
    assert.match(script, /Skills cannot be removed while a research round is running\./);
});
