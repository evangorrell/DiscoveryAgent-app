const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const rootPath = path.resolve(__dirname, '..');
const figureSource = fs.readFileSync(
    path.join(rootPath, 'da_selection_ui/static/report_figures.js'),
    'utf8',
);
const workspaceSource = fs.readFileSync(
    path.join(rootPath, 'da_selection_ui/static/round_workspace.js'),
    'utf8',
);
const sharedCss = fs.readFileSync(
    path.join(rootPath, 'da_selection_ui/static/style.css'),
    'utf8',
);
const workspaceCss = fs.readFileSync(
    path.join(rootPath, 'da_selection_ui/static/round_workspace.css'),
    'utf8',
);
const workspaceTemplate = fs.readFileSync(
    path.join(rootPath, 'da_selection_ui/templates/round_workspace.html'),
    'utf8',
);
const projectHomeTemplate = fs.readFileSync(
    path.join(rootPath, 'da_selection_ui/templates/project_home.html'),
    'utf8',
);

function sourceSlice(source, startText, endText) {
    const start = source.indexOf(startText);
    const end = source.indexOf(endText, start);
    assert.notEqual(start, -1, `missing ${startText}`);
    assert.notEqual(end, -1, `missing ${endText}`);
    return source.slice(start, end);
}

function markConnected(element, connected) {
    element.isConnected = connected;
    element.children.forEach(child => markConnected(child, connected));
}

class MockElement {
    constructor(tag, className = '') {
        this.tagName = tag.toUpperCase();
        this.className = className;
        this.children = [];
        this.parentElement = null;
        this.style = {};
        this.attributes = new Map();
        this.listeners = new Map();
        this.classNames = new Set(className.split(/\s+/).filter(Boolean));
        this.classList = {
            add: value => this.classNames.add(value),
            remove: value => this.classNames.delete(value),
            contains: value => this.classNames.has(value),
        };
        this.rectWidth = 0;
        this.isConnected = false;
        this.complete = false;
        this.naturalWidth = 0;
        this.naturalHeight = 0;
        this.capturedPointer = null;
    }

    append(...children) {
        children.forEach(child => {
            child.parentElement = this;
            this.children.push(child);
            markConnected(child, this.isConnected);
        });
    }

    replaceChildren(...children) {
        this.children.forEach(child => markConnected(child, false));
        this.children = [];
        this.append(...children);
    }

    setAttribute(name, value) {
        this.attributes.set(name, String(value));
    }

    getAttribute(name) {
        return this.attributes.get(name) ?? null;
    }

    addEventListener(type, listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type).add(listener);
    }

    removeEventListener(type, listener) {
        const listeners = this.listeners.get(type);
        if (listeners) listeners.delete(listener);
    }

    emit(type, overrides = {}) {
        const event = {
            type,
            pointerType: 'mouse',
            button: 0,
            pointerId: 1,
            clientX: 0,
            clientY: 0,
            key: '',
            defaultPrevented: false,
            propagationStopped: false,
            preventDefault() { this.defaultPrevented = true; },
            stopPropagation() { this.propagationStopped = true; },
            ...overrides,
        };
        [...(this.listeners.get(type) || [])].forEach(listener => listener(event));
        return event;
    }

    getBoundingClientRect() {
        let width = this.rectWidth;
        if (typeof this.style.width === 'string' && this.style.width.endsWith('px')) {
            width = Number.parseFloat(this.style.width);
        } else if (this.style.width === '100%' && this.parentElement) {
            width = this.parentElement.getBoundingClientRect().width;
        }
        return { width };
    }

    setPointerCapture(pointerId) {
        this.capturedPointer = pointerId;
    }

    hasPointerCapture(pointerId) {
        return this.capturedPointer === pointerId;
    }

    releasePointerCapture(pointerId) {
        if (this.capturedPointer === pointerId) this.capturedPointer = null;
    }

    listenerCount() {
        return [...this.listeners.values()].reduce((total, values) => total + values.size, 0);
    }

    findAll(className) {
        const found = this.classNames.has(className) ? [this] : [];
        return found.concat(...this.children.map(child => child.findAll(className)));
    }
}

function makeNode(tag, className, text) {
    const element = new MockElement(tag, className || '');
    if (text != null) element.textContent = text;
    return element;
}

function sharedResizeHarness() {
    const frames = new Map();
    let nextFrame = 1;
    const browserRoot = {
        requestAnimationFrame(callback) {
            const id = nextFrame++;
            frames.set(id, callback);
            return id;
        },
        cancelAnimationFrame(id) {
            frames.delete(id);
        },
    };
    const code = sourceSlice(
        figureSource,
        '    function attachFigureResizer(',
        '    function attachEmbeddedFigureResizer(',
    );
    const attachFigureResizer = new Function(
        'root',
        'node',
        `${code}; return attachFigureResizer;`,
    )(browserRoot, makeNode);
    return { attachFigureResizer, frames };
}

function resizableFixture(parentWidth = 600, shellWidth = 400, options = {}) {
    const { attachFigureResizer, frames } = sharedResizeHarness();
    const parent = new MockElement('div');
    parent.rectWidth = parentWidth;
    parent.isConnected = true;
    const shell = new MockElement('div', 'rw-figure-resize-shell');
    shell.rectWidth = shellWidth;
    const stage = new MockElement('div', 'rw-figure-image-stage');
    parent.append(shell);
    shell.append(stage);
    const controller = attachFigureResizer(shell, stage, options);
    return { parent, shell, stage, controller, handle: controller.handle, frames };
}

function standaloneHarness() {
    const cleanupSource = sourceSlice(
        workspaceSource,
        '  const standaloneFigureViewCleanups',
        '  function reportTabScrollbarFailure',
    );
    const renderSource = sourceSlice(
        workspaceSource,
        '  function renderStandaloneImage(',
        '  async function renderFileBody(',
    );
    const shared = sharedResizeHarness();
    const ReportFigures = { attachFigureResizer: shared.attachFigureResizer };
    const api = new Function(
        'node',
        'artifactUrl',
        'ReportFigures',
        'reportTabScrollbarFailure',
        `${cleanupSource}\n${renderSource}\nreturn {
            renderStandaloneImage,
            clearStandaloneFigureViews,
            cleanups: standaloneFigureViewCleanups,
        };`,
    )(
        makeNode,
        file => '/artifact/' + file.name,
        ReportFigures,
        error => { throw error; },
    );
    return { ...api, frames: shared.frames };
}

function imageFrom(body) {
    return body.findAll('rw-artifact-image')[0];
}

test('standalone images receive exactly one shared bottom-right resize handle', () => {
    const { renderStandaloneImage } = standaloneHarness();
    const body = new MockElement('div', 'rw-pane-body');
    body.rectWidth = 700;
    body.isConnected = true;

    renderStandaloneImage(body, { name: 'wide.png' });
    const image = imageFrom(body);
    image.naturalWidth = 1200;
    image.naturalHeight = 600;
    image.emit('load');

    const handles = body.findAll('rw-figure-resize-handle');
    assert.equal(handles.length, 1);
    assert.equal(handles[0].getAttribute('role'), 'slider');
    assert.equal(handles[0].getAttribute('aria-label'), 'Resize figure');
    assert.match(sharedCss, /\.rw-figure-resize-handle\{[^}]*right:-10px;bottom:-10px/);
    assert.match(sharedCss, /cursor:nwse-resize;touch-action:none;user-select:none/);
    assert.match(workspaceCss, /\.rw-standalone-figure-image-stage>\.rw-figure-resize-handle\{z-index:2\}/);
});
test('wide tall transparent and narrow images keep a reachable resize control', () => {
    const cases = [
        { name: 'wide.jpg', bodyWidth: 700, width: 1400, height: 120 },
        { name: 'tall.png', bodyWidth: 420, width: 100, height: 1200 },
        { name: 'transparent.webp', bodyWidth: 500, width: 320, height: 240 },
        { name: 'narrow.gif', bodyWidth: 220, width: 600, height: 400 },
    ];
    cases.forEach(item => {
        const harness = standaloneHarness();
        const body = new MockElement('div', 'rw-pane-body');
        body.rectWidth = item.bodyWidth;
        body.isConnected = true;
        harness.renderStandaloneImage(body, { name: item.name });
        const image = imageFrom(body);
        image.naturalWidth = item.width;
        image.naturalHeight = item.height;
        image.emit('load');
        harness.frames.forEach(callback => callback());

        const handle = body.findAll('rw-figure-resize-handle')[0];
        assert.ok(handle, item.name);
        assert.equal(
            handle.getAttribute('aria-valuemax'),
            String(item.bodyWidth - 40),
            item.name,
        );
        assert.ok(Number(handle.getAttribute('aria-valuenow')) <= item.bodyWidth - 40);
    });
});



test('shared pointer and keyboard calculations preserve aspect ratio and enforce bounds', () => {
    const { shell, handle, controller } = resizableFixture();
    const down = handle.emit('pointerdown', {
        pointerId: 7,
        clientX: 100,
        clientY: 100,
    });
    assert.equal(down.defaultPrevented, true);
    assert.equal(down.propagationStopped, true);
    assert.equal(handle.capturedPointer, 7);
    assert.equal(shell.classList.contains('is-resizing'), true);

    handle.emit('pointermove', {
        pointerId: 7,
        clientX: 200,
        clientY: 160,
    });
    assert.equal(shell.style.width, '480px');
    assert.equal(handle.getAttribute('aria-valuenow'), '480');

    controller.resize(-1000);
    assert.equal(shell.style.width, '240px');
    controller.resize(5000);
    assert.equal(shell.style.width, '100%');
    assert.equal(handle.getAttribute('aria-valuemax'), '600');

    shell.style.width = '400px';
    handle.emit('keydown', { key: 'Home' });
    assert.equal(shell.style.width, '240px');
    handle.emit('keydown', { key: 'End' });
    assert.equal(shell.style.width, '100%');

    assert.match(sharedCss, /\.rw-figure-image-stage>img\{[^}]*width:100%[^}]*height:auto/);
    assert.match(workspaceCss, /\.rw-standalone-figure-image-stage>\.rw-artifact-image\{[^}]*width:100%[^}]*height:auto/);
});

test('standalone bounds use the same controller inside the reachable viewer gutter', () => {
    const embedded = resizableFixture(500, 300);
    const standalone = resizableFixture(500, 300, { horizontalGutter: 40 });

    assert.deepEqual(embedded.controller.limits(), { min: 240, max: 500 });
    assert.deepEqual(standalone.controller.limits(), { min: 240, max: 460 });
    standalone.controller.resize(9999);
    assert.equal(standalone.handle.getAttribute('aria-valuemax'), '460');
    assert.equal(standalone.shell.style.width, '100%');

    assert.match(workspaceCss, /\.rw-pane-body\{[^}]*overflow:auto/);
    assert.match(workspaceCss, /\.rw-standalone-figure-resize-shell\{width:calc\(100% - 40px\);max-width:calc\(100% - 40px\);margin:20px auto\}/);
});

test('standalone images keep natural fit initially and reset when reopened', () => {
    const harness = standaloneHarness();
    const body = new MockElement('div', 'rw-pane-body');
    body.rectWidth = 500;
    body.isConnected = true;
    const file = { name: 'small.webp' };

    harness.renderStandaloneImage(body, file);
    let image = imageFrom(body);
    image.naturalWidth = 200;
    image.naturalHeight = 100;
    image.emit('load');
    let shell = body.findAll('rw-standalone-figure-resize-shell')[0];
    let handle = body.findAll('rw-figure-resize-handle')[0];
    assert.equal(shell.style.width, '200px');

    handle.emit('pointerdown', { pointerId: 2, clientX: 0, clientY: 0 });
    handle.emit('pointermove', { pointerId: 2, clientX: 200, clientY: 200 });
    handle.emit('pointerup', { pointerId: 2 });
    assert.equal(shell.style.width, '400px');

    harness.clearStandaloneFigureViews();
    body.replaceChildren();
    harness.renderStandaloneImage(body, file);
    image = imageFrom(body);
    image.naturalWidth = 200;
    image.naturalHeight = 100;
    image.emit('load');
    shell = body.findAll('rw-standalone-figure-resize-shell')[0];
    assert.equal(shell.style.width, '200px');
});

test('rapid file switching destroys stale controllers and ignores late image loads', () => {
    const harness = standaloneHarness();
    const body = new MockElement('div', 'rw-pane-body');
    body.rectWidth = 520;
    body.isConnected = true;

    harness.renderStandaloneImage(body, { name: 'first.png' });
    const firstImage = imageFrom(body);
    harness.clearStandaloneFigureViews();
    body.replaceChildren();

    firstImage.naturalWidth = 400;
    firstImage.emit('load');
    assert.equal(firstImage.parentElement.findAll('rw-figure-resize-handle').length, 0);

    harness.renderStandaloneImage(body, { name: 'second.jpg' });
    const secondImage = imageFrom(body);
    secondImage.naturalWidth = 300;
    secondImage.emit('load');
    const handle = body.findAll('rw-figure-resize-handle')[0];
    assert.ok(handle);
    assert.ok(handle.listenerCount() > 0);

    handle.emit('pointerdown', { pointerId: 9 });
    const shell = body.findAll('rw-standalone-figure-resize-shell')[0];
    assert.equal(shell.classList.contains('is-resizing'), true);
    harness.clearStandaloneFigureViews();
    assert.equal(shell.classList.contains('is-resizing'), false);
    assert.equal(handle.capturedPointer, null);
    assert.equal(handle.listenerCount(), 0);
    assert.equal(harness.cleanups.length, 0);
});

test('only successfully loaded supported image views install the resize controller', () => {
    const renderBody = sourceSlice(
        workspaceSource,
        '  async function renderFileBody(',
        '  function applyPaneRatio',
    );
    assert.match(renderBody, /if\(isFigureFile\(file\.name\)\)\{renderStandaloneImage\(body,file\);return;\}/);
    const pdfBranch = renderBody.split("if (ext === '.pdf'", 2)[1].split("if (['.md'", 1)[0];
    assert.match(pdfBranch, /node\('iframe','rw-artifact-frame'\)/);
    assert.doesNotMatch(pdfBranch, /renderStandaloneImage/);
    assert.match(renderBody, /node\('pre','rw-code'\)/);
    assert.match(renderBody, /node\('div','rw-unsupported'\)/);

    const harness = standaloneHarness();
    const body = new MockElement('div', 'rw-pane-body');
    body.rectWidth = 500;
    body.isConnected = true;
    harness.renderStandaloneImage(body, { name: 'missing.tiff' });
    imageFrom(body).emit('error');
    assert.equal(body.findAll('rw-figure-resize-handle').length, 0);

    assert.match(workspaceSource, /validViewerFile\(file\).*isDirectory===true/);
});

test('embedded reports keep the same wrapper while standalone teardown adds no global pointer listeners', () => {
    assert.match(
        figureSource,
        /function attachEmbeddedFigureResizer\(shell, stage\) \{\s*return attachFigureResizer\(shell, stage\);/,
    );
    assert.match(figureSource, /attachEmbeddedFigureResizer\(shell, stage\)/);
    assert.match(workspaceSource, /clearStandaloneFigureViews\(\);\s*clearTabScrollbarControllers\(\)/);
    assert.match(workspaceSource, /beforeunload',clearStandaloneFigureViews/);

    const controllerSource = sourceSlice(
        figureSource,
        '    function attachFigureResizer(',
        '    function attachEmbeddedFigureResizer(',
    );
    assert.doesNotMatch(controllerSource, /window\.addEventListener|root\.addEventListener/);
    assert.match(controllerSource, /handle\.removeEventListener\('pointermove'/);
    assert.match(controllerSource, /handle\.releasePointerCapture/);
    assert.match(controllerSource, /event\.stopPropagation\(\)/);
    assert.match(controllerSource, /handle\.draggable = false/);
});

test('shared renderer loads before the viewer under matching fresh asset keys', () => {
    const key = '20260927-safe-markdown-1';
    assert.ok(workspaceTemplate.indexOf("filename='report_figures.js'")
        < workspaceTemplate.indexOf("filename='round_workspace.js'"));
    assert.match(workspaceTemplate, new RegExp("report_figures\\.js', v='" + key));
    assert.match(workspaceTemplate, /round_workspace\.js', v='[^']+'/);
    assert.match(workspaceTemplate, /round_workspace\.css', v='20261001-configuration-polish-1'/);
    assert.match(projectHomeTemplate, new RegExp("report_figures\\.js', v='" + key));
});
