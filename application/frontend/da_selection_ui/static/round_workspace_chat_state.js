(function (root) {
  'use strict';

  function createMessageId() {
    if (root.crypto && typeof root.crypto.randomUUID === 'function') {
      return root.crypto.randomUUID();
    }
    return 'rw-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }

  function legacyMessageId(message, round, index) {
    const role = message && message.role === 'assistant' ? 'assistant' : 'user';
    const text = String((message && message.text) || '');
    let hash = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return 'legacy-' + round + '-' + index + '-' + role + '-' + (hash >>> 0).toString(36);
  }

  function normalizeMessage(message, round, index) {
    if (!message || !['user', 'assistant'].includes(message.role) || typeof message.text !== 'string') {
      return null;
    }
    const id = typeof message.id === 'string' && message.id ? message.id : legacyMessageId(message, round, index);
    const normalized = {
      id,
      role: message.role,
      text: message.text,
      round,
    };
    if (message.role === 'user' && message.queued === true) normalized.queued = true;
    if (message.role === 'assistant' && message.pending === true) normalized.pending = true;
    if (typeof message.requestId === 'string' && message.requestId) normalized.requestId = message.requestId;
    if (typeof message.jobId === 'string' && message.jobId) normalized.jobId = message.jobId;
    if (typeof message.jobStatus === 'string' && message.jobStatus) normalized.jobStatus = message.jobStatus;
    if (message.failed === true) normalized.failed = true;
    if (message.retryable === true) normalized.retryable = true;
    if (typeof message.error === 'string' && message.error) normalized.error = message.error;
    return normalized;
  }

  function recoverMessages(messages, round) {
    const normalized = (Array.isArray(messages) ? messages : [])
      .map((message, index) => normalizeMessage(message, round, index))
      .filter(Boolean);
    return normalized;
  }

  function normalizeState(value) {
    const rounds = value && typeof value === 'object' && value.rounds && typeof value.rounds === 'object'
      ? value.rounds
      : {};
    const normalized = {};
    Object.entries(rounds).forEach(([roundKey, messages]) => {
      const round = Number(roundKey);
      if (!Number.isFinite(round)) return;
      normalized[roundKey] = (Array.isArray(messages) ? messages : [])
        .map((message, index) => normalizeMessage(message, round, index))
        .filter(Boolean)
        .map(({ round: _round, ...message }) => message);
    });
    return { rounds: normalized };
  }

  function mergeStates(remoteValue, localValue) {
    const remote = normalizeState(remoteValue);
    const local = normalizeState(localValue);
    const merged = { rounds: { ...remote.rounds } };
    Object.entries(local.rounds).forEach(([round, localMessages]) => {
      const remoteMessages = Array.isArray(merged.rounds[round]) ? merged.rounds[round] : [];
      const remoteById = new Map(remoteMessages.map((message) => [message.id, message]));
      const localIds = new Set(localMessages.map((message) => message.id));
      const ordered = remoteMessages.filter((message) => !localIds.has(message.id));
      localMessages.forEach((message) => {
        const prior = remoteById.get(message.id);
        const priorTerminal = prior && prior.role === 'assistant'
          && ['succeeded', 'failed'].includes(prior.jobStatus) && !prior.pending;
        const localTerminal = message.role === 'assistant'
          && ['succeeded', 'failed'].includes(message.jobStatus) && !message.pending;
        if (priorTerminal && !localTerminal) {
          ordered.push(prior);
          return;
        }
        ordered.push({
          ...(prior || {}),
          ...message,
          ...((prior && prior.jobId && !message.jobId) ? { jobId: prior.jobId } : {}),
          ...((prior && prior.requestId && !message.requestId) ? { requestId: prior.requestId } : {}),
        });
      });
      merged.rounds[round] = ordered.slice(-50);
    });
    return merged;
  }

  async function persistWithConflict(snapshot, writeState, readState) {
    let response = await writeState(snapshot);
    if (!response || response.status !== 409) return { response, snapshot };

    const latest = await readState();
    if (!latest || latest.ok === false) return { response, snapshot };
    const reconciled = mergeStates(latest.state, snapshot);
    response = await writeState(reconciled);
    return { response, snapshot: reconciled };
  }

  function canSubmit(inFlight, round) {
    return Boolean(inFlight && typeof inFlight.has === 'function' && !inFlight.has(round));
  }

  const api = {
    createMessageId,
    recoverMessages,
    mergeStates,
    persistWithConflict,
    canSubmit,
  };
  root.RoundWorkspaceChatState = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
