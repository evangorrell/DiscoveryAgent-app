const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'data_picker.js'),
    'utf8',
);

function control() {
    const attributes = new Map();
    return {
        disabled: false,
        setAttribute(name, value) { attributes.set(name, value); },
        removeAttribute(name) { attributes.delete(name); },
        getAttribute(name) { return attributes.get(name); },
    };
}

function pickerWith(fetch) {
    const context = { window: {}, fetch };
    vm.runInNewContext(source, context, { filename: 'data_picker.js' });
    return context.window.DiscoveryAgentDataPicker;
}

test('shared picker sends one request and clears accessible busy state', async () => {
    let finish;
    const calls = [];
    const picker = pickerWith((url, options) => {
        calls.push({ url, options });
        return new Promise((resolve) => { finish = resolve; });
    });
    const firstControl = control();
    const secondControl = control();

    const first = picker.choose({ controls: [firstControl, secondControl] });
    assert.equal(picker.isPending(), true);
    assert.equal(firstControl.disabled, true);
    assert.equal(secondControl.getAttribute('aria-busy'), 'true');

    const duplicate = await picker.choose({ controls: [firstControl] });
    assert.equal(duplicate.ignored, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/data-library/choose');
    assert.equal(calls[0].options.method, 'POST');
    assert.equal(calls[0].options.credentials, 'include');

    finish({ ok: true, json: async () => ({ success: true, cancelled: true }) });
    const result = await first;
    assert.equal(result.cancelled, true);
    assert.equal(picker.isPending(), false);
    assert.equal(firstControl.disabled, false);
    assert.equal(secondControl.disabled, false);
    assert.equal(firstControl.getAttribute('aria-busy'), undefined);
    assert.equal(secondControl.getAttribute('aria-busy'), undefined);
});

test('shared picker exposes server errors and still permits retry', async () => {
    let attempt = 0;
    const action = control();
    const picker = pickerWith(async () => {
        attempt += 1;
        if (attempt === 1) {
            return { ok: false, json: async () => ({ success: false, code: 'picker_failed', error: 'Picker failed.' }) };
        }
        return { ok: true, json: async () => ({ success: true, cancelled: false, added: [] }) };
    });

    await assert.rejects(picker.choose({ controls: [action] }), /Picker failed\./);
    assert.equal(action.disabled, false);
    assert.equal(action.getAttribute('aria-busy'), undefined);
    assert.equal(picker.isPending(), false);

    const retried = await picker.choose({ controls: [action] });
    assert.equal(retried.success, true);
    assert.equal(attempt, 2);
});

test('shared picker clears busy state after a bridge rejection and allows another invocation', async () => {
    let attempt = 0;
    const action = control();
    const picker = pickerWith(async () => {
        attempt += 1;
        if (attempt === 1) throw new Error('Native bridge disconnected.');
        return { ok: true, json: async () => ({ success: true, cancelled: true }) };
    });

    await assert.rejects(
        picker.choose({ controls: [action] }),
        /Native bridge disconnected\./,
    );
    assert.equal(picker.isPending(), false);
    assert.equal(action.disabled, false);
    assert.equal(action.getAttribute('aria-busy'), undefined);

    const retry = await picker.choose({ controls: [action] });
    assert.equal(retry.cancelled, true);
    assert.equal(attempt, 2);
});
