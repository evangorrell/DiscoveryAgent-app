const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'da_selection_ui');
const source = fs.readFileSync(path.join(root, 'static', 'projects.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'templates', 'projects.html'), 'utf8');

function sourceBlock(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert.ok(start >= 0 && end > start, `missing source block: ${startMarker}`);
    return source.slice(start, end);
}

test('successful Create closes its workflow and replaces history with the server-generated Projects URL', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const createSuccess = persist.slice(persist.indexOf("if (typeof data.projects_url"));

    assert.match(createSuccess, /typeof data\.projects_url !== 'string'/);
    assert.match(createSuccess, /resetSubmitState\(\)[\s\S]*closeModal\(\{ force: true \}\)[\s\S]*window\.location\.replace\(data\.projects_url\)/);
    assert.doesNotMatch(createSuccess, /window\.location\.href\s*=\s*data\.destination/);
    assert.doesNotMatch(createSuccess, /['"]\/projects(?:['"]|\/)/);
    assert.match(template, /projects\.js'\) \}\}\?v=20260823-first-run-welcome-1/);
});

test('Create failure stays in the modal and duplicate submission protection remains intact', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const failure = persist.slice(
        persist.indexOf('if (!(resp.ok && data && data.success))'),
        persist.indexOf("if (modalMode === 'edit')"),
    );
    const busy = sourceBlock('function setSubmitBusy()', 'async function persistProject');

    assert.match(failure, /setFormError\(message\)/);
    assert.match(failure, /resetSubmitState\(\)/);
    assert.doesNotMatch(failure, /window\.location|closeModal\(\{ force: true \}\)/);
    assert.match(busy, /els\.submit\.disabled = true/);
    assert.match(busy, /els\.submit\.classList\.add\('is-busy'\)/);
    assert.match(persist, /if \(projectSubmissionRunning\) return false/);
    assert.ok(persist.indexOf('setSubmitBusy()') < persist.indexOf('await fetch(endpoint'));
});

test('Edit Save and explicit Continue retain their established destinations', () => {
    const persist = sourceBlock('async function persistProject(body, options)', 'async function confirmProjectRelocation');
    const editSuccess = persist.slice(
        persist.indexOf("if (modalMode === 'edit')"),
        persist.indexOf("if (typeof data.projects_url"),
    );
    const continueProject = sourceBlock('async function continueProject(project, invoker)', '// ---- modal (create + edit)');
    const wiring = sourceBlock('function wire()', "document.addEventListener('DOMContentLoaded'");

    assert.match(editSuccess, /closeModal\(\{ force: true \}\)[\s\S]*loadProjects\(\)/);
    assert.doesNotMatch(editSuccess, /projects_url|location\.replace/);
    assert.match(continueProject, /window\.location\.href = data\.destination/);
    assert.match(wiring, /els\.cancel\.addEventListener\('click',[\s\S]*closeModal/);
});

test('first-run welcome owns the single New Project action while populated pages keep the sticky tray', () => {
    assert.equal((template.match(/id="open-new-project"/g) || []).length, 1);
    assert.ok(template.includes("{% macro new_project_button(label='New Project') -%}"));
    assert.ok(template.includes('id="open-new-project"'));
    assert.ok(template.includes('{% if projects_empty %}'));
    assert.ok(template.includes('Welcome to DiscoveryAgent!'));
    assert.ok(template.includes(`v2-onboarding-cta">{{ new_project_button('Create Your First Project') }}`));
    assert.ok(template.includes('id="projects-primary-action" hidden'));
    assert.ok(template.includes('{{ new_project_button() }}'));
    assert.ok(template.includes('aria-label="{{ label }}"'));
    assert.ok(template.includes("new_project_button('Create Your First Project')"));
    assert.ok(source.includes('if (helper && els.primaryAction) pageActionLayout = helper.create'));
    assert.ok(source.includes('if (empty !== initialProjectsEmpty)'));
    assert.ok(source.includes('window.location.reload()'));
    assert.equal(source.split("els.openBtn.addEventListener('click', openCreate)").length - 1, 1);
});
