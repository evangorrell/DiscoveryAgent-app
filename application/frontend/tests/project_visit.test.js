const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../da_selection_ui/static/project_context.js'), 'utf8');

function page(storage, projectId = 'project-A', projectSlug = 'project-a', blocked = false) {
    const events = {};
    const window = {
        discoveryProjectContext: { projectId, projectSlug },
        sessionStorage: {
            getItem(key) { if (blocked) throw Error('blocked'); return storage.get(key) || null; },
            setItem(key, value) { if (blocked) throw Error('blocked'); storage.set(key, value); },
            removeItem(key) { if (blocked) throw Error('blocked'); storage.delete(key); },
        },
        crypto: { randomUUID: () => require('node:crypto').randomUUID() },
        addEventListener(name, fn) { events[name] = fn; },
        fetch: async () => ({}),
        location: { href: 'http://localhost/projects/' + projectSlug, origin: 'http://localhost' },
    };
    vm.runInNewContext(source, { window, URL, Request, Headers });
    return { visit: window.discoveryProjectVisit, restore: () => events.pageshow({ persisted: true }) };
}
const view = { chatId: 'chosen-chat', draft: 'Follow up' };
const read = (p) => JSON.parse(JSON.stringify(p.visit.readAuditView()));

test('project navigation and reload retain the same tab-local Audit view', () => {
    const storage = new Map();
    page(storage).visit.rememberAuditView(view);
    assert.deepEqual(read(page(storage)), view);
    assert.deepEqual(read(page(storage, 'project-A', 'renamed-project')), view);
});

test('global Projects clears the visit despite the server retaining its last project ID', () => {
    const storage = new Map();
    page(storage).visit.rememberAuditView(view);
    page(storage, 'project-A', '');
    assert.equal(read(page(storage)), null);
});

test('switching projects resets Audit and stale pages cannot overwrite a newer visit', () => {
    const storage = new Map();
    const old = page(storage);
    old.visit.rememberAuditView(view);
    const other = page(storage, 'project-B', 'project-b');
    assert.equal(read(other), null);
    const returned = page(storage);
    old.visit.rememberAuditView(view);
    assert.equal(read(returned), null);
});

test('browser back/forward restoration revalidates the project visit', () => {
    const storage = new Map();
    const cached = page(storage);
    cached.visit.rememberAuditView(view);
    page(storage);
    cached.restore();
    assert.deepEqual(read(cached), view);
    page(storage, 'project-A', '');
    cached.restore();
    assert.equal(read(cached), null);
});

test('separate browser-tab storage does not share selected Audit views', () => {
    page(new Map()).visit.rememberAuditView(view);
    assert.equal(read(page(new Map())), null);
});

test('unavailable or malformed session storage safely falls back to a fresh view', () => {
    const storage = new Map([['discoveryAgentProjectVisitV1', '{invalid']]);
    assert.equal(read(page(storage)), null);
    const blocked = page(storage, 'project-A', 'project-a', true);
    blocked.visit.rememberAuditView(view);
    assert.equal(read(blocked), null);
});
