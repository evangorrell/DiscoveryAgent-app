const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
    path.join(__dirname, '..', 'da_selection_ui', 'static', 'hypothesis_studio.js'),
    'utf8'
);
const start = source.indexOf('    async function runManualEdit(');
const end = source.indexOf('    function cancelManualEdit(', start);
assert.ok(start >= 0 && end > start);
const runManualEditSource = source.slice(start, end);

test('manual save completion preserves a newer draft opened on another hypothesis', async () => {
    const state = {
        manualEditIndex: 1,
        manualEditDraft: 'First card draft',
        busyIndex: null,
        busyKind: null,
        events: [],
        viewedRegenerationPositions: {},
    };
    const activeManualEditRequests = {};
    let resolveSave;
    const saveResponse = new Promise((resolve) => { resolveSave = resolve; });
    let renders = 0;
    const runManualEdit = new Function(
        'state', 'activeManualEditRequests', 'activeMutationHypothesis',
        'isRegeneratingHypothesis', 'hasAnyLlmEditActive', 'isManualEditSaving',
        'setComposerError', 'isPendingSelectedCard', 'selectedCardLockMessage',
        'regenerationViewFor', 'window', 'renderCards', 'apiJson',
        'manualEditRequest', 'nextEventId', 'render', 'pageUnloading',
        `let manualEditRequestSeq = 0; ${runManualEditSource}\nreturn runManualEdit;`
    )(
        state, activeManualEditRequests, (h) => h,
        () => false, () => false,
        (index) => !!activeManualEditRequests[index],
        () => {}, () => false, () => '',
        () => ({ isViewingCurrent: true, viewedResultEventId: 'version-1' }),
        { AbortController }, () => {}, () => saveResponse,
        (index) => activeManualEditRequests[index],
        () => 'revision-1', () => { renders += 1; }, false
    );

    const pendingSave = runManualEdit({ index: 1, state_id: 'state-1' }, 'Updated first card');
    assert.ok(activeManualEditRequests[1]);
    state.manualEditIndex = 2;
    state.manualEditDraft = 'Unsaved second card draft';

    resolveSave({
        resp: { ok: true },
        data: { success: true, hypothesis: { index: 1, state_id: 'state-2' } },
    });
    await pendingSave;

    assert.equal(state.manualEditIndex, 2);
    assert.equal(state.manualEditDraft, 'Unsaved second card draft');
    assert.equal(state.events.length, 1);
    assert.equal(state.events[0].kind, 'revision');
    assert.equal(renders, 1);
});
