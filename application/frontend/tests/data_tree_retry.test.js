const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'da_selection_ui', 'static', 'data_tree.js'), 'utf8');

class Element {
    constructor(tag) {
        this.tagName = tag;
        this.children = [];
        this.dataset = {};
        this.attributes = {};
        this.listeners = {};
        this.className = '';
        this.classList = { toggle() {} };
        this.style = { setProperty() {} };
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name] || null; }
    append(...children) { children.forEach((child) => this.appendChild(child)); }
    appendChild(child) { child.parent = this; this.children.push(child); return child; }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
    async fire(name) { for (const listener of this.listeners[name] || []) await listener({ target: this }); }
    querySelector(selector) {
        const wanted = selector.slice(1);
        return this.children.find((child) => child.className.split(' ').includes(wanted)) || null;
    }
    remove() {
        if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
    }
}

function renderer(fetchChildren) {
    const context = { window: {}, document: { createElement: (tag) => new Element(tag) } };
    vm.runInNewContext(source, context);
    return context.window.DiscoveryAgentDataTree.createRenderer({ fetchChildren });
}

test('a failed folder expansion can be retried by closing and reopening it', async () => {
    let calls = 0;
    const tree = renderer(async () => {
        if (++calls === 1) throw new Error('Temporary failure');
        return { children: [{ name: 'data.csv', path: 'data.csv', kind: 'file', size_bytes: 3 }] };
    });
    const sourceRow = tree.renderSource({ id: 'ds_1', display_name: 'Input', kind: 'directory' });
    const [row, children] = sourceRow.children;
    await row.fire('click');
    assert.equal(children.children[0].textContent, 'Temporary failure');
    assert.equal(children.dataset.loaded, undefined);
    await row.fire('click');
    await row.fire('click');
    assert.equal(calls, 2);
    assert.equal(children.dataset.loaded, 'true');
    assert.equal(children.children[0].children[0].children.some((child) => child.textContent === 'data.csv'), true);
});

test('a failed pagination request keeps Load more available for retry', async () => {
    let pageCalls = 0;
    const tree = renderer(async (_entry, _path, cursor) => {
        if (!cursor) return { children: [], next_cursor: 1 };
        if (++pageCalls === 1) throw new Error('Temporary failure');
        return { children: [{ name: 'data.csv', path: 'data.csv', kind: 'file', size_bytes: 3 }], next_cursor: null };
    });
    const sourceRow = tree.renderSource({ id: 'ds_1', display_name: 'Input', kind: 'directory' });
    const [row, children] = sourceRow.children;
    await row.fire('click');
    const moreItem = children.children[0];
    const more = moreItem.children[0];
    await more.fire('click');
    assert.equal(more.disabled, false);
    assert.equal(moreItem.children[1].textContent, 'Temporary failure');
    await more.fire('click');
    assert.equal(pageCalls, 2);
    assert.equal(children.children.length, 1);
    assert.notEqual(children.children[0], moreItem);
});
