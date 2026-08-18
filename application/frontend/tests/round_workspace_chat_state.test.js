// Unit tests for Round Workspace chat request and persistence state.
// Run with: node --test tests/round_workspace_chat_state.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const ChatState = require('../da_selection_ui/static/round_workspace_chat_state.js');

function response(status) {
  return { status, ok: status >= 200 && status < 300 };
}

test('a conversation-state 409 reconciles and retries persistence only', async () => {
  const writes = [];
  let reads = 0;
  const local = {
    rounds: {
      4: [
        { id: 'user-4', role: 'user', text: 'What changed?' },
        { id: 'pending-4', requestId: 'user-4', role: 'assistant', text: '', pending: true },
      ],
    },
  };
  const remote = {
    rounds: {
      4: [{ id: 'greeting-4', role: 'assistant', text: 'Ask about Round 4.' }],
    },
  };

  const result = await ChatState.persistWithConflict(
    local,
    async (snapshot) => {
      writes.push(snapshot);
      return response(writes.length === 1 ? 409 : 200);
    },
    async () => {
      reads += 1;
      return { ok: true, state: remote };
    },
  );

  assert.equal(writes.length, 2);
  assert.equal(reads, 1);
  assert.equal(result.response.status, 200);
  assert.deepEqual(
    result.snapshot.rounds[4].map((message) => message.id),
    ['greeting-4', 'user-4', 'pending-4'],
  );
});

test('queued jobs survive normalization and refresh without duplication', async () => {
  const restored = ChatState.recoverMessages([
    { id: 'user-a', requestId: 'user-a', jobId: 'job-a', jobStatus: 'running', role: 'user', text: 'Question A' },
    { id: 'assistant-a', requestId: 'user-a', jobId: 'job-a', jobStatus: 'running', role: 'assistant', text: '', pending: true },
    { id: 'user-b', requestId: 'user-b', jobId: 'job-b', jobStatus: 'queued', role: 'user', text: 'Question B', queued: true },
    { id: 'assistant-b', requestId: 'user-b', jobId: 'job-b', jobStatus: 'queued', role: 'assistant', text: '', queued: true },
  ], 4);

  assert.equal(restored[1].pending, true);
  assert.equal(restored[2].queued, true);
  assert.equal(restored[3].queued, true);
  assert.equal(restored[3].jobId, 'job-b');

  const merged = ChatState.mergeStates(
    { rounds: { 4: restored } },
    { rounds: { 4: restored } },
  );
  assert.deepEqual(merged.rounds[4].map((message) => message.id), [
    'user-a', 'assistant-a', 'user-b', 'assistant-b',
  ]);

  let persistenceWrites = 0;
  await ChatState.persistWithConflict(
    merged,
    async () => response((persistenceWrites += 1) === 1 ? 409 : 200),
    async () => ({ ok: true, state: merged }),
  );
  assert.equal(persistenceWrites, 2);
});

test('a durable completed answer wins over stale browser pending state', () => {
  const durable = { rounds: { 4: [{
    id: 'pending-4', requestId: 'user-4', jobId: 'job-4', role: 'assistant',
    text: 'Completed answer.', pending: false, jobStatus: 'succeeded',
  }] } };
  const browser = { rounds: { 4: [{
    id: 'pending-4', requestId: 'user-4', role: 'assistant', text: '', pending: true,
    jobStatus: 'pending',
  }] } };
  const merged = ChatState.mergeStates(durable, browser);
  assert.equal(merged.rounds[4][0].text, 'Completed answer.');
  assert.equal(merged.rounds[4][0].pending, undefined);
  assert.equal(merged.rounds[4][0].jobStatus, 'succeeded');
  assert.equal(merged.rounds[4][0].jobId, 'job-4');
});

test('a completed user turn cannot be resurrected as queued after refresh', () => {
  const durable = { rounds: { 4: [{
    id: 'user-4', requestId: 'user-4', jobId: 'job-4', role: 'user',
    text: 'Question B', queued: false, jobStatus: 'succeeded',
  }] } };
  const staleBrowser = { rounds: { 4: [{
    id: 'user-4', requestId: 'user-4', jobId: 'job-4', role: 'user',
    text: 'Question B', queued: true, jobStatus: 'queued',
  }] } };
  const merged = ChatState.mergeStates(durable, staleBrowser);
  assert.equal(merged.rounds[4][0].jobStatus, 'succeeded');
  assert.equal(merged.rounds[4][0].queued, undefined);
});

test('a durable cancellation wins over stale browser pending state', () => {
  const durable = { rounds: { 4: [{
    id: 'pending-4', requestId: 'user-4', jobId: 'job-4', role: 'assistant',
    text: 'Round Chat response cancelled.', pending: false, jobStatus: 'cancelled',
    cancelled: true,
  }] } };
  const browser = { rounds: { 4: [{
    id: 'pending-4', requestId: 'user-4', role: 'assistant', text: '', pending: true,
    jobStatus: 'running',
  }] } };
  const merged = ChatState.mergeStates(durable, browser);
  assert.equal(merged.rounds[4][0].text, 'Round Chat response cancelled.');
  assert.equal(merged.rounds[4][0].pending, undefined);
  assert.equal(merged.rounds[4][0].jobStatus, 'cancelled');
  assert.equal(merged.rounds[4][0].cancelled, true);
  assert.equal('paused' in merged.rounds[4][0], false);
});

test('browser transcript ordering survives server acceptance racing persistence', () => {
  const serverAccepted = { rounds: { 4: [
    { id: 'user-4', requestId: 'user-4', role: 'user', text: 'Question' },
    { id: 'pending-4', requestId: 'user-4', jobId: 'job-4', role: 'assistant', text: '', pending: true },
  ] } };
  const browserSnapshot = { rounds: { 4: [
    { id: 'greeting-4', role: 'assistant', text: 'Greeting' },
    { id: 'user-4', requestId: 'user-4', role: 'user', text: 'Question' },
    { id: 'pending-4', requestId: 'user-4', role: 'assistant', text: '', pending: true },
  ] } };
  const merged = ChatState.mergeStates(serverAccepted, browserSnapshot);
  assert.deepEqual(
    merged.rounds[4].map((message) => message.id),
    ['greeting-4', 'user-4', 'pending-4'],
  );
  assert.equal(merged.rounds[4][2].jobId, 'job-4');
});

test('legacy messages get stable ids so local and durable state can reconcile', () => {
  const legacy = { rounds: { 4: [{ role: 'user', text: 'same text' }] } };
  const first = ChatState.mergeStates({ rounds: {} }, legacy);
  const second = ChatState.mergeStates(first, legacy);
  assert.equal(second.rounds[4].length, 1);
  assert.match(second.rounds[4][0].id, /^legacy-4-0-user-/);
});

test('composer control switches between accessible Send and Stop states', () => {
  assert.deepEqual(ChatState.composerControlState(false, false), {
    action: 'send', ariaLabel: 'Send message', icon: 'send', disabled: false,
  });
  assert.deepEqual(ChatState.composerControlState(true, false), {
    action: 'stop', ariaLabel: 'Stop response', icon: 'stop', disabled: false,
  });
  assert.deepEqual(ChatState.composerControlState(true, true), {
    action: 'stop', ariaLabel: 'Stop response', icon: 'stop', disabled: true,
  });
  assert.equal(ChatState.composerControlState(false, false).icon, 'send');
});

test('composer button sends while idle but only stops while active', () => {
  const calls = [];
  const callbacks = {
    send: (message) => calls.push(['send', message]),
    stop: () => calls.push(['stop']),
  };
  assert.equal(ChatState.performComposerAction('send', '  Question A  ', callbacks), 'send');
  assert.deepEqual(calls, [['send', 'Question A']]);

  calls.length = 0;
  const draft = 'Question B stays in the composer';
  assert.equal(ChatState.performComposerAction('stop', draft, callbacks), 'stop');
  assert.deepEqual(calls, [['stop']]);
  assert.equal(draft, 'Question B stays in the composer');
});

test('composer keyboard intent queues Enter and preserves newline/composition behavior', () => {
  assert.equal(ChatState.composerKeyboardAction({ key: 'Enter', shiftKey: false }, 'Question B'), 'send');
  assert.equal(ChatState.composerKeyboardAction({ key: 'Enter', shiftKey: true }, 'Question B'), null);
  assert.equal(ChatState.composerKeyboardAction({ key: 'Enter', shiftKey: false }, '   '), 'noop');
  assert.equal(ChatState.composerKeyboardAction({ key: 'Enter', shiftKey: false, isComposing: true }, '質問'), null);
  assert.equal(ChatState.composerKeyboardAction({ key: 'Enter', shiftKey: false, keyCode: 229 }, '質問'), null);
});

test('empty idle composer does not invoke send', () => {
  let sends = 0;
  assert.equal(ChatState.performComposerAction('send', ' \n ', { send: () => { sends += 1; } }), 'noop');
  assert.equal(sends, 0);
});
