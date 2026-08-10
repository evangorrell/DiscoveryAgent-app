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

test('one UI submission remains one job POST across duplicate events and navigation', async () => {
  const inFlight = new Map();
  let roundChatJobPosts = 0;
  let releaseRequest;
  const requestFinished = new Promise((resolve) => { releaseRequest = resolve; });

  async function submit(round) {
    if (!ChatState.canSubmit(inFlight, round)) return false;
    const user = { id: 'user-4', role: 'user', text: 'Summarize Round 4.' };
    const pending = {
      id: 'pending-4', requestId: user.id, role: 'assistant', text: '', pending: true,
    };
    inFlight.set(round, { pending });
    roundChatJobPosts += 1;
    await requestFinished;
    inFlight.delete(round);
    return true;
  }

  const firstSubmit = submit(4);
  await submit(4); // A second submit/Enter event while the request is pending.
  assert.equal(roundChatJobPosts, 1);

  // A newly loaded page keeps the server job identity and pending state. It
  // resumes status polling; it never invokes the job-submission callback.
  const restored = ChatState.recoverMessages([
    { id: 'user-4', requestId: 'user-4', role: 'user', text: 'Summarize Round 4.' },
    { id: 'pending-4', requestId: 'user-4', jobId: 'job-4', jobStatus: 'running', role: 'assistant', text: '', pending: true },
  ], 4);
  assert.equal(restored[1].pending, true);
  assert.equal(restored[1].jobId, 'job-4');
  assert.equal(restored[1].jobStatus, 'running');
  assert.equal(roundChatJobPosts, 1);

  let persistenceWrites = 0;
  await ChatState.persistWithConflict(
    { rounds: { 4: restored } },
    async () => response((persistenceWrites += 1) === 1 ? 409 : 200),
    async () => ({ ok: true, state: { rounds: {} } }),
  );
  assert.equal(persistenceWrites, 2);
  assert.equal(roundChatJobPosts, 1); // The 409 retried state only, never the job.

  releaseRequest();
  await firstSubmit;
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
