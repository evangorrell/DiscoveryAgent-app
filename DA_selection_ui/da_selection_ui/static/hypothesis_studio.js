// Hypothesis Studio (V2) — candidate hypothesis cards + a per-hypothesis
// refinement chat.
//
// Product model:
//   * DiscoveryAgent generates a set of candidate hypotheses.
//   * The candidates stay visible as cards across the top of the workspace.
//   * The scientist focuses one card; the chat BELOW is scoped only to that
//     hypothesis and has its OWN independent conversation thread.
//   * Switching cards switches threads — Hypothesis 2 never inherits
//     Hypothesis 1's refinement conversation. Each thread begins from the same
//     shared scientific baseline the V1 refinement agent received, then
//     accumulates only its own messages (the backend enforces this isolation
//     via per-hypothesis HypothesisGenAgent message threads).
//   * The focused hypothesis updates in place as it is revised.
//
// This controller reuses the EXISTING hypothesis backend APIs and does not
// reimplement backend semantics. Per-hypothesis chat isolation is a backend
// guarantee (one saved conversation per hypothesis); the client only sends the
// focused hypothesis_index.
//
// Backend contract reused:
//   POST /api/initialize          -> restores generator + round redirect.
//   POST /api/generate            -> { num_hypotheses, guidance }.
//   POST /api/chat                -> { message, mode:'hypothesis',
//                                     hypothesis_index } scoped discussion
//                                     (never mutates the hypothesis).
//   POST /api/edit                -> revise (feedback) / manual edit one
//                                     hypothesis.
//   POST /api/regenerate_guidance -> regenerate one hypothesis.
//   POST /api/remove_hypothesis   -> remove one candidate.
//   POST /api/select              -> finalize the selected hypothesis.
//   GET  /api/hypothesis_history[/<n>] -> sidebar + one round's read-only
//                                     event history (projected into cards +
//                                     per-hypothesis threads).
//
// The user only ever sees "DiscoveryAgent", never internal agent names.
(function () {
    'use strict';

    // Redirect to login on an expired session (shared minimal pattern).
    if (!window.__authFetchWrapped) {
        const nativeFetch = window.fetch;
        window.fetch = async (...args) => {
            const response = await nativeFetch(...args);
            if (response.status === 401) window.location.href = '/';
            return response;
        };
        window.__authFetchWrapped = true;
    }

    const CFG = window.hypothesisStudioConfig || {};
    const MIN_N = CFG.minNumHypotheses || 1;
    const MAX_N = CFG.maxNumHypotheses || 10;

    const $ = (id) => document.getElementById(id);
    const els = {
        loading: $('hs-loading'), error: $('hs-error'), errorDetail: $('hs-error-detail'), retry: $('hs-retry'),
        shell: $('hs-shell'),
        welcome: $('hs-welcome'), welcomeGuidance: $('hs-welcome-guidance'),
        welcomeError: $('hs-welcome-error'), init: $('hs-init'),
        activity: $('hs-activity'), activityLabel: $('hs-activity-label'),
        activityCancel: $('hs-cancel-generation'), activityError: $('hs-activity-error'),
        workspaceMain: $('hs-workspace-main'), cards: $('hs-cards'), thread: $('hs-thread'),
        sidebar: $('hs-sidebar'), roundList: $('hs-round-list'),
        readonlyBar: $('hs-readonly-bar'), readonlyTitle: $('hs-readonly-title'), readonlyText: $('hs-readonly-text'),
        readonlyCaveat: $('hs-readonly-caveat'), returnCurrent: $('hs-return-current'),
        composer: $('hs-composer'), chatScope: $('hs-chat-scope'),
        chatScopeTitle: $('hs-chat-scope-title'), chatScopeHelp: $('hs-chat-scope-help'),
        modeToggle: $('hs-mode-toggle'), modeTrigger: $('hs-mode-trigger'), modeLabel: $('hs-mode-label'),
        modeMenu: $('hs-mode-menu'), modeChat: $('hs-mode-chat'), modeEdit: $('hs-mode-edit'),
        input: $('hs-input'), toolbarLeft: $('hs-toolbar-left'), queuedMessages: $('hs-queued-messages'),
        countMenu: $('hs-count-menu'), countTrigger: $('hs-count-trigger'), countLabel: $('hs-count-label'), countList: $('hs-count-list'),
        send: $('hs-send'), composerError: $('hs-composer-error'),
        review: $('hs-review'), reviewHypothesis: $('hs-review-hypothesis'),
        reviewJustBlock: $('hs-review-just-block'), reviewJustification: $('hs-review-justification'),
        reviewError: $('hs-review-error'), reviewBack: $('hs-review-back'), reviewConfirm: $('hs-review-confirm'),
    };

    const HSE = window.HSEvents;
    const HS_VERSION = '20260806-preserve-background-chat-scroll';
    const HS_DEBUG_ENABLED = (() => {
        try {
            return new URLSearchParams(window.location.search).has('hs_debug') ||
                window.localStorage.getItem('hs_debug') === '1';
        } catch (err) {
            return false;
        }
    })();
    const diagnostics = window.__hypothesisStudioDebug = {
        version: HS_VERSION,
        loaded: true,
        loadedAt: new Date().toISOString(),
        initialExploration: !!CFG.initialExploration,
        buttonFound: !!els.init,
        buttonBound: false,
        clicks: 0,
        generateCalls: 0,
        lastPayload: null,
        lastFetchStartedAt: null,
        lastError: null,
    };
    function hsDebug(message, details) {
        if (!HS_DEBUG_ENABLED) return;
        if (details !== undefined) console.debug('[HS] ' + message, details);
        else console.debug('[HS] ' + message);
    }
    function hsDebugError(message, err) {
        diagnostics.lastError = err && (err.stack || err.message) ? (err.stack || err.message) : String(err || message);
        if (HS_DEBUG_ENABLED) console.error('[HS] ' + message, err);
    }
    hsDebug('script loaded', diagnostics);

    // ---- state ---------------------------------------------------------------
    //
    // The live workspace derives from an ordered `events` array (the same event
    // vocabulary the durable backend history uses), so live and restored rounds
    // render through one path:
    //   { kind:'hypotheses', id, hypotheses:[...] }          full generated set
    //   { kind:'revision',   id, index, source, hypothesis } one revised card
    //   { kind:'removal',    id, index, hypotheses:[...] }    re-indexed survivors
    //   { kind:'selection',  id, hypothesis }                final choice
    //   { kind:'message', role, content, hypothesisIndex }   one thread turn
    // Cards come from the newest set-defining event (+ later revisions); each
    // hypothesis's chat thread is the message events attributed to its index.

    const state = {
        count: CFG.defaultNumHypotheses || 3,
        events: [],
        generationId: null,
        generating: false,
        focusIndex: null,          // 1-based index of the focused card
        busy: false,
        busyIndex: null,           // index of a card being mutated
        busyKind: null,            // mutation type for contextual busy UI
        expanded: {},              // index -> details expanded?
        manualEditIndex: null,      // index whose full hypothesis is being edited inline
        manualEditDraft: '',
        viewedRegenerationPositions: {}, // index -> zero-based slot-result preview
        reviewTarget: null,
        readOnly: false,
        partialHistory: false,
        rounds: [],
        currentRoundIndex: null,
        currentRoundLabel: null,
        viewRoundIndex: null,
        viewRoundLabel: null,
        pendingThreadRequests: {}, // index -> { kind, resultEventId } while that chat context is in flight
        queuedThreadMessages: {},  // index -> [{ text, mode, createdAt }] queued while a reply is pending
        pausedThreadQueues: {},    // index -> true after the user stops a reply with queued follow-ups
        composerMode: 'chat',      // 'chat' answers questions; 'edit' calls /api/edit
        cancelRequested: false,
        regenerationRequests: {}, // index -> { requestId, canceled } for independent card regenerations
    };

    let eventSeq = 0;
    let regenerateSeq = 0;
    let activeThreadRequestSeq = 0;
    const activeThreadRequests = {}; // index -> request/controller state for independent hypothesis chats
    let pageUnloading = false;
    window.addEventListener('beforeunload', () => { pageUnloading = true; });
    const nextEventId = () => 'e' + (++eventSeq);

    function pendingRegenerationKey() {
        return 'da_hs_pending_regeneration_v1_' + (CFG.projectSlug || 'project');
    }

    function canceledRegenerationKey() {
        return 'da_hs_canceled_regeneration_v1_' + (CFG.projectSlug || 'project');
    }

    function rememberCanceledRegeneration(index) {
        const idx = Number(index);
        if (!idx) return;
        try {
            const canceled = readCanceledRegenerations().filter((entry) => entry.index !== idx);
            canceled.push({ index: idx, canceledAt: Date.now() });
            window.localStorage.setItem(canceledRegenerationKey(), JSON.stringify({ canceled: canceled }));
        } catch (err) { /* storage unavailable */ }
    }

    function readCanceledRegenerations() {
        try {
            const raw = window.localStorage.getItem(canceledRegenerationKey());
            if (!raw) return [];
            const data = JSON.parse(raw);
            const candidates = Array.isArray(data && data.canceled) ? data.canceled : [data];
            const canceled = candidates.map((entry) => {
                const idx = Number(entry && entry.index);
                const canceledAt = Number(entry && entry.canceledAt);
                if (!idx || !canceledAt || Date.now() - canceledAt > 30 * 60 * 1000) return null;
                return { index: idx, canceledAt: canceledAt };
            }).filter(Boolean);
            if (!canceled.length) {
                window.localStorage.removeItem(canceledRegenerationKey());
                return [];
            }
            return canceled;
        } catch (err) {
            return [];
        }
    }

    function readCanceledRegeneration(index) {
        const idx = index == null ? null : Number(index);
        const canceled = readCanceledRegenerations();
        return idx == null ? (canceled[0] || null) : (canceled.find((entry) => entry.index === idx) || null);
    }

    function clearCanceledRegeneration(index) {
        try {
            if (index == null) {
                window.localStorage.removeItem(canceledRegenerationKey());
                return;
            }
            const idx = Number(index);
            const remaining = readCanceledRegenerations().filter((entry) => entry.index !== idx);
            if (remaining.length) {
                window.localStorage.setItem(canceledRegenerationKey(), JSON.stringify({ canceled: remaining }));
            } else window.localStorage.removeItem(canceledRegenerationKey());
        } catch (err) { /* storage unavailable */ }
    }

    function isCanceledRegeneration(regeneration) {
        const idx = Number(regeneration && regeneration.index);
        const canceled = readCanceledRegeneration(idx);
        if (!canceled || !idx || canceled.index !== idx) return false;
        const startedAt = Number(regeneration && regeneration.started_at);
        if (!startedAt) return true;
        return startedAt * 1000 <= canceled.canceledAt;
    }

    function rememberPendingRegeneration(index) {
        const idx = Number(index);
        if (!idx) return;
        try {
            const pending = readPendingRegenerations().filter((entry) => entry.index !== idx);
            pending.push({ index: idx, startedAt: Date.now() });
            window.localStorage.setItem(pendingRegenerationKey(), JSON.stringify({ pending: pending }));
        } catch (err) { /* storage unavailable */ }
    }

    function readPendingRegenerations() {
        try {
            const raw = window.localStorage.getItem(pendingRegenerationKey());
            if (!raw) return [];
            const data = JSON.parse(raw);
            const candidates = Array.isArray(data && data.pending) ? data.pending : [data];
            const pending = candidates.map((entry) => {
                const idx = Number(entry && entry.index);
                const startedAt = Number(entry && entry.startedAt);
                if (!idx || !startedAt || Date.now() - startedAt > 30 * 60 * 1000) return null;
                return { index: idx, startedAt: startedAt };
            }).filter(Boolean);
            if (!pending.length) {
                window.localStorage.removeItem(pendingRegenerationKey());
                return [];
            }
            return pending;
        } catch (err) {
            return [];
        }
    }

    function readPendingRegeneration(index) {
        const idx = index == null ? null : Number(index);
        const pending = readPendingRegenerations();
        return idx == null ? (pending[0] || null) : (pending.find((entry) => entry.index === idx) || null);
    }

    function clearPendingRegeneration(index) {
        try {
            if (index == null) {
                window.localStorage.removeItem(pendingRegenerationKey());
                return;
            }
            const idx = Number(index);
            const remaining = readPendingRegenerations().filter((entry) => entry.index !== idx);
            if (remaining.length) {
                window.localStorage.setItem(pendingRegenerationKey(), JSON.stringify({ pending: remaining }));
            } else window.localStorage.removeItem(pendingRegenerationKey());
        } catch (err) { /* storage unavailable */ }
    }

    function regenerationRequest(index) {
        const idx = Number(index);
        return idx && state.regenerationRequests[idx] ? state.regenerationRequests[idx] : null;
    }

    function isRegeneratingHypothesis(index) {
        return !!regenerationRequest(index);
    }

    function hasAnyRegeneration() {
        return Object.keys(state.regenerationRequests || {}).length > 0;
    }

    function hasBlockingCardMutation() {
        return state.busyIndex != null && state.busyKind !== 'revise';
    }

    function pendingThreadKey() {
        return 'da_hs_pending_thread_v1_' + (CFG.projectSlug || 'project');
    }

    function makeClientRequestId() {
        if (window.crypto && window.crypto.getRandomValues) {
            const bytes = new Uint32Array(2);
            window.crypto.getRandomValues(bytes);
            return 'hs_' + bytes[0].toString(36) + bytes[1].toString(36);
        }
        return 'hs_' + Date.now().toString(36) + Math.random().toString(36).slice(2);
    }

    function rememberPendingThread(kind, index, message, requestId, resultEventId) {
        const idx = Number(index);
        const content = String(message || '').trim();
        if (!idx || !content) return;
        try {
            const requests = readPendingThreads().filter((entry) => entry.index !== idx);
            requests.push({
                kind: kind === 'revise' ? 'revise' : 'discuss',
                index: idx,
                message: content,
                requestId: requestId || null,
                resultEventId: resultEventId || null,
                startedAt: Date.now(),
            });
            window.localStorage.setItem(pendingThreadKey(), JSON.stringify({ requests: requests }));
        } catch (err) { /* storage unavailable */ }
    }

    function readPendingThreads() {
        try {
            const raw = window.localStorage.getItem(pendingThreadKey());
            if (!raw) return [];
            const data = JSON.parse(raw);
            // Accept the original single-request payload so an in-flight reply
            // from an older page build still restores safely.
            const candidates = Array.isArray(data && data.requests) ? data.requests : [data];
            const requests = candidates.map((entry) => {
                const idx = Number(entry && entry.index);
                const message = String(entry && entry.message || '').trim();
                const startedAt = Number(entry && entry.startedAt);
                if (!idx || !message || !startedAt || Date.now() - startedAt > 30 * 60 * 1000) return null;
                return {
                    kind: entry.kind === 'revise' ? 'revise' : 'discuss',
                    index: idx,
                    message: message,
                    requestId: entry.requestId ? String(entry.requestId) : null,
                    resultEventId: entry.resultEventId ? String(entry.resultEventId) : null,
                    startedAt: startedAt,
                };
            }).filter(Boolean);
            if (!requests.length) {
                window.localStorage.removeItem(pendingThreadKey());
                return [];
            }
            return requests;
        } catch (err) {
            return [];
        }
    }

    function readPendingThread(index) {
        const idx = index == null ? null : Number(index);
        const requests = readPendingThreads();
        return idx == null ? (requests[0] || null) : (requests.find((entry) => entry.index === idx) || null);
    }

    function clearPendingThread(index) {
        try {
            if (index == null) {
                window.localStorage.removeItem(pendingThreadKey());
                return;
            }
            const idx = Number(index);
            const remaining = readPendingThreads().filter((entry) => entry.index !== idx);
            if (remaining.length) {
                window.localStorage.setItem(pendingThreadKey(), JSON.stringify({ requests: remaining }));
            } else {
                window.localStorage.removeItem(pendingThreadKey());
            }
        } catch (err) { /* storage unavailable */ }
    }

    function pendingThreadRequest(index) {
        const idx = Number(index);
        return idx && state.pendingThreadRequests[idx] ? state.pendingThreadRequests[idx] : null;
    }

    function hasPendingThread(index) {
        return !!pendingThreadRequest(index);
    }

    function isViewedThreadPending(index) {
        const idx = Number(index);
        const pending = pendingThreadRequest(idx);
        if (!idx || !pending) return false;
        const active = activeMutationHypothesis({ index: idx });
        const view = regenerationViewFor(active);
        return !pending.resultEventId || pending.resultEventId === view.viewedResultEventId;
    }

    function hasAnyPendingThread() {
        return Object.keys(state.pendingThreadRequests || {}).length > 0;
    }

    function normalizeQueuedThreadMessages(queues) {
        if (!queues || typeof queues !== 'object') return {};
        const normalized = {};
        Object.keys(queues).forEach((key) => {
            const idx = Number(key);
            if (!idx || !Array.isArray(queues[key])) return;
            const items = queues[key]
                .map((entry) => {
                    if (typeof entry === 'string') return { text: entry.trim(), mode: 'chat', createdAt: Date.now() };
                    if (!entry || typeof entry !== 'object') return null;
                    const text = String(entry.text || '').trim();
                    if (!text) return null;
                    return {
                        text,
                        mode: entry.mode === 'edit' ? 'edit' : 'chat',
                        createdAt: Number.isFinite(entry.createdAt) ? entry.createdAt : Date.now(),
                    };
                })
                .filter(Boolean)
                .slice(0, 20);
            if (items.length) normalized[idx] = items;
        });
        return normalized;
    }

    function getQueuedThreadMessages(index) {
        const idx = Number(index);
        return idx && Array.isArray(state.queuedThreadMessages[idx]) ? state.queuedThreadMessages[idx] : [];
    }

    function queueThreadMessage(index, text, mode) {
        const idx = Number(index);
        const content = String(text || '').trim();
        if (!idx || !content) return;
        const next = getQueuedThreadMessages(idx).slice();
        next.push({ text: content, mode: mode === 'edit' ? 'edit' : 'chat', createdAt: Date.now() });
        state.queuedThreadMessages[idx] = next.slice(0, 20);
    }

    function shiftQueuedThreadMessage(index) {
        const idx = Number(index);
        if (!idx) return null;
        const queue = getQueuedThreadMessages(idx).slice();
        const next = queue.shift() || null;
        if (queue.length) state.queuedThreadMessages[idx] = queue;
        else delete state.queuedThreadMessages[idx];
        return next;
    }

    function removeQueuedThreadMessage(index, queueIndex) {
        const idx = Number(index);
        const pos = Number(queueIndex);
        if (!idx || !Number.isInteger(pos)) return;
        const queue = getQueuedThreadMessages(idx).slice();
        if (pos < 0 || pos >= queue.length) return;
        queue.splice(pos, 1);
        if (queue.length) state.queuedThreadMessages[idx] = queue;
        else delete state.queuedThreadMessages[idx];
    }

    function studioViewKey() {
        return 'da_hs_view_v1_' + (CFG.projectSlug || 'project');
    }

    function studioSnapshotKey() {
        return 'da_hs_snapshot_v1_' + (CFG.projectSlug || 'project');
    }

    function normalizedExpanded(value) {
        const out = {};
        if (!value || typeof value !== 'object') return out;
        Object.keys(value).forEach((key) => {
            const idx = Number(key);
            if (idx && value[key]) out[idx] = true;
        });
        return out;
    }

    function readStudioView() {
        try {
            const raw = window.localStorage.getItem(studioViewKey());
            if (!raw) return null;
            const data = JSON.parse(raw);
            const updatedAt = Number(data && data.updatedAt);
            if (!updatedAt || Date.now() - updatedAt > 14 * 24 * 60 * 60 * 1000) {
                window.localStorage.removeItem(studioViewKey());
                return null;
            }
            const viewRoundIndex = data.viewRoundIndex == null ? null : Number(data.viewRoundIndex);
            const focusIndex = data.focusIndex == null ? null : Number(data.focusIndex);
            return {
                viewRoundIndex: viewRoundIndex || null,
                readOnly: !!data.readOnly,
                focusIndex: focusIndex || null,
                expanded: normalizedExpanded(data.expanded),
                updatedAt: updatedAt,
            };
        } catch (err) {
            return null;
        }
    }

    function rememberStudioView() {
        try {
            const viewRoundIndex = state.viewRoundIndex == null ? state.currentRoundIndex : state.viewRoundIndex;
            window.localStorage.setItem(studioViewKey(), JSON.stringify({
                viewRoundIndex: viewRoundIndex == null ? null : Number(viewRoundIndex),
                readOnly: !!state.readOnly,
                focusIndex: state.focusIndex == null ? null : Number(state.focusIndex),
                expanded: normalizedExpanded(state.expanded),
                updatedAt: Date.now(),
            }));
        } catch (err) { /* storage unavailable */ }
    }

    function cloneJsonSafe(value, fallback) {
        try {
            return JSON.parse(JSON.stringify(value == null ? fallback : value));
        } catch (err) {
            return fallback;
        }
    }

    function boundedSnapshotEvents(events) {
        const cloned = cloneJsonSafe(Array.isArray(events) ? events : [], []);
        // A refinement thread can grow, but localStorage is only a warm-start
        // cache. If quota is tight, keep the latest set-defining event plus the
        // newest activity so cards can still paint immediately.
        const maxEvents = 250;
        if (cloned.length <= maxEvents) return cloned;
        const tailStart = cloned.length - maxEvents;
        const setPos = HSE && HSE.latestSetDefiningPos ? HSE.latestSetDefiningPos(cloned) : -1;
        if (setPos >= 0 && setPos < tailStart) return [cloned[setPos]].concat(cloned.slice(tailStart));
        return cloned.slice(tailStart);
    }

    function syncEventSeqFromEvents(events) {
        let max = eventSeq;
        (events || []).forEach((ev) => {
            const id = ev && typeof ev.id === 'string' ? ev.id : '';
            const m = id.match(/^e(\d+)$/);
            if (m) max = Math.max(max, Number(m[1]) || 0);
        });
        eventSeq = max;
    }

    function readStudioSnapshot() {
        try {
            const raw = window.localStorage.getItem(studioSnapshotKey());
            if (!raw) return null;
            const data = JSON.parse(raw);
            const updatedAt = Number(data && data.updatedAt);
            if (!updatedAt || Date.now() - updatedAt > 7 * 24 * 60 * 60 * 1000) {
                window.localStorage.removeItem(studioSnapshotKey());
                return null;
            }
            return data;
        } catch (err) {
            try { window.localStorage.removeItem(studioSnapshotKey()); } catch (_) {}
            return null;
        }
    }

    function clearStudioSnapshot() {
        try { window.localStorage.removeItem(studioSnapshotKey()); } catch (_) {}
    }

    function persistStudioSnapshot() {
        try {
            const hasRenderableState = !!(state.generating ||
                (Array.isArray(state.events) && state.events.length) ||
                (Array.isArray(state.rounds) && state.rounds.length) ||
                state.currentRoundIndex != null);
            if (!hasRenderableState) return;
            const payload = {
                version: 1,
                updatedAt: Date.now(),
                count: state.count,
                events: cloneJsonSafe(state.events || [], []),
                generationId: state.generationId,
                generating: !!state.generating,
                focusIndex: state.focusIndex == null ? null : Number(state.focusIndex),
                queuedThreadMessages: cloneJsonSafe(state.queuedThreadMessages || {}, {}),
                pausedThreadQueues: cloneJsonSafe(state.pausedThreadQueues || {}, {}),
                busyIndex: state.busyIndex == null ? null : Number(state.busyIndex),
                busyKind: state.busyKind || null,
                expanded: normalizedExpanded(state.expanded),
                readOnly: !!state.readOnly,
                partialHistory: !!state.partialHistory,
                rounds: cloneJsonSafe(state.rounds || [], []),
                currentRoundIndex: state.currentRoundIndex == null ? null : Number(state.currentRoundIndex),
                currentRoundLabel: state.currentRoundLabel || null,
                viewRoundIndex: state.viewRoundIndex == null ? null : Number(state.viewRoundIndex),
                viewRoundLabel: state.viewRoundLabel || null,
            };
            try {
                window.localStorage.setItem(studioSnapshotKey(), JSON.stringify(payload));
            } catch (quotaErr) {
                payload.events = boundedSnapshotEvents(payload.events);
                window.localStorage.setItem(studioSnapshotKey(), JSON.stringify(payload));
            }
        } catch (err) {
            // Quota/private-mode failures should never block the live studio.
        }
    }

    function restoreLocalSnapshot() {
        const snap = readStudioSnapshot();
        if (!snap) return false;
        const events = Array.isArray(snap.events) ? snap.events : [];
        const rounds = Array.isArray(snap.rounds) ? snap.rounds : [];
        const hasRenderableSnapshot = events.length || rounds.length || snap.generating;
        if (!hasRenderableSnapshot) return false;

        state.count = Number(snap.count) || state.count;
        state.events = boundedSnapshotEvents(events);
        syncEventSeqFromEvents(state.events);
        state.generationId = snap.generationId || null;
        state.generating = !!snap.generating;
        state.focusIndex = snap.focusIndex == null ? null : Number(snap.focusIndex);
        // Chat is the safe default on every Studio entry. Edit is an explicit
        // per-visit choice and must not leak in from a previous page snapshot.
        state.composerMode = 'chat';
        state.queuedThreadMessages = normalizeQueuedThreadMessages(snap.queuedThreadMessages);
        state.pausedThreadQueues = snap.pausedThreadQueues && typeof snap.pausedThreadQueues === 'object'
            ? snap.pausedThreadQueues : {};
        state.expanded = normalizedExpanded(snap.expanded);
        state.manualEditIndex = null;
        state.manualEditDraft = '';
        state.readOnly = !!snap.readOnly;
        state.partialHistory = !!snap.partialHistory;
        state.rounds = rounds;
        state.currentRoundIndex = snap.currentRoundIndex == null ? null : Number(snap.currentRoundIndex);
        state.currentRoundLabel = snap.currentRoundLabel || null;
        state.viewRoundIndex = snap.viewRoundIndex == null
            ? state.currentRoundIndex
            : Number(snap.viewRoundIndex);
        state.viewRoundLabel = snap.viewRoundLabel || state.currentRoundLabel;

        const pendingRegenerations = readPendingRegenerations().filter((entry) => !isCanceledRegeneration(entry));
        state.regenerationRequests = {};
        pendingRegenerations.forEach((entry) => {
            state.regenerationRequests[entry.index] = { requestId: 0, canceled: false, restored: true };
        });
        if (pendingRegenerations.length) {
            state.generating = false;
            state.focusIndex = pendingRegenerations[0].index;
        } else {
            // Busy state is transient. Only the dedicated pending-regeneration
            // record (and the server status checked during initialize) can
            // restore it; a page snapshot may outlive the request that made it.
            state.busyIndex = null;
            state.busyKind = null;
        }

        applyPendingThreadFromStorage();
        setBusy(!!(state.generating || state.busyIndex));
        showShell();
        if (state.generating && isViewingLiveRound()) startActivity();
        else stopActivity();
        render();
        renderSidebar();
        setCount(state.count);
        return true;
    }

    function applyStoredViewForDisplayedRound() {
        const saved = readStudioView();
        if (!saved) return false;
        const viewed = state.viewRoundIndex == null ? state.currentRoundIndex : state.viewRoundIndex;
        if (saved.viewRoundIndex != null && viewed != null && saved.viewRoundIndex !== viewed) return false;
        if (!state.readOnly && saved.focusIndex != null) state.focusIndex = saved.focusIndex;
        state.expanded = saved.expanded || {};
        return true;
    }

    function applyStoredLiveView() {
        if (state.readOnly) return false;
        const saved = readStudioView();
        if (!saved) return false;
        if (saved.viewRoundIndex != null && state.currentRoundIndex != null && saved.viewRoundIndex !== state.currentRoundIndex) return false;
        if (saved.focusIndex != null) state.focusIndex = saved.focusIndex;
        state.expanded = saved.expanded || {};
        return true;
    }

    // Snapshot of the live round kept while browsing a read-only historical round.
    let liveStash = null;

    // ---- tiny helpers --------------------------------------------------------

    function esc(t) { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }
    function el(tag, cls, html) { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
    function pluralHyp(n) { return n === 1 ? 'hypothesis' : 'hypotheses'; }
    function activitySpinner(className) {
        return el('div', className || 'hs-activity-spinner',
            '<svg class="hs-activity-arrows" viewBox="0 0 64 64" focusable="false" aria-hidden="true">' +
            '<circle cx="32" cy="32" r="21" fill="none" stroke="currentColor" stroke-width="3.2" opacity="0.14"/>' +
            '<path d="M47.6 46.1A21 21 0 0 1 16.4 46.1" fill="none" stroke="currentColor" stroke-width="3.6" stroke-linecap="round"/>' +
            '</svg>'
        );
    }
    function compactText(value) { return String(value == null ? '' : value).replace(/\s+/g, ' ').trim(); }
    function stripMarkdownSectionLabel(value) {
        return String(value == null ? '' : value)
            .trim()
            .replace(/^\s*\*{0,2}(?:Title|Primary factor|Hypothesis|Justification|Plan|Interpretation)\*{0,2}\s*:\s*/i, '')
            .trim();
    }
    function stripSectionLabel(value) { return compactText(stripMarkdownSectionLabel(value)); }
    function extractStructuredTitle(value) {
        const text = String(value == null ? '' : value);
        const match = text.match(/(?:^|\n)\s*\*{0,2}Title\*{0,2}\s*:\s*([\s\S]*?)(?=\n\s*\*{0,2}(?:Primary factor|Hypothesis|Justification|Plan)\*{0,2}\s*:|$)/i);
        return match ? stripSectionLabel(match[1]) : '';
    }
    // Card title contract (kept in sync with select_hypothesis.py):
    //   preferred 10-14 words · hard maximum 18 words.
    // Backend title normalization is canonical. Frontend normalization is a
    // defensive display fallback. Titles are word-budgeted, not display-truncated:
    // there is no character max, no ellipsis, and no line clamp — a valid title
    // is always shown in full. When a title is clearly invalid we derive a
    // title-like phrase instead of clipping it.
    const CARD_TITLE_PREFERRED_WORDS = 14;
    const CARD_TITLE_MAX_WORDS = 18;
    function cardTitleWordCount(value) {
        const m = String(value == null ? '' : value).match(/[A-Za-z0-9][A-Za-z0-9/_-]*/g);
        return m ? m.length : 0;
    }
    function titleNeedsRewrite(title, h) {
        const candidate = compactText(title);
        if (!candidate) return true;
        const words = cardTitleWordCount(candidate);
        // A title that merely echoes the hypothesis claim is only a problem when
        // it is also too long to read as a label; a short one-line claim doubles
        // fine as a fallback scanning title. Character count is never a trigger.
        const hypothesis = compactText(stripMarkdownSectionLabel(h && h.hypothesis));
        if (hypothesis && candidate.toLowerCase() === hypothesis.toLowerCase() && words > CARD_TITLE_PREFERRED_WORDS) return true;
        if (words > CARD_TITLE_MAX_WORDS) return true;
        if (words > CARD_TITLE_PREFERRED_WORDS && /[.!?]\s*$/.test(candidate)) return true;
        return false;
    }
    function deriveConciseTitle(title, hypothesisText) {
        // Semantic (not mechanical) derivation: drop the sentence tail and
        // clausal riders so the leading target/mechanism phrase survives.
        let source = compactText(title) || compactText(hypothesisText);
        source = source.replace(/^\s*\*{0,2}(?:Title|Hypothesis)\*{0,2}\s*:\s*/i, '');
        const sentence = source.match(/^(.*?[.!?])(?:\s|$)/);
        if (sentence) source = sentence[1];
        source = source.split(/\s+(?:because|while|whereas|whose|which|that|via|using|preventing|distinguished)\b/i)[0];
        source = source.split(/,\s*(?:and|but|while|which|whose|that|\w+ing)\b/i)[0];
        source = source.replace(/\bacts\s+as\s+an?\b/i, '')
            .replace(/\boperates\s+through\s+an?\b/i, '')
            .replace(/\brepresents?\s+an?\b/i, '')
            .replace(/\bcan\s+be\b.*$/i, '');
        source = compactText(source.replace(/^[\s,;:.-]+|[\s,;:.-]+$/g, ''));
        // Last-resort word-budget guard (never a character cap): only fires when
        // clause-stripping left a phrase still longer than a title should be.
        const words = source.split(/\s+/).filter(Boolean);
        if (words.length > CARD_TITLE_PREFERRED_WORDS) {
            source = words.slice(0, CARD_TITLE_PREFERRED_WORDS).join(' ').replace(/[\s,;:.-]+$/, '');
        }
        return source || 'Untitled research direction';
    }
    function explicitTitle(h) {
        const embeddedTitle = extractStructuredTitle(h && (h.full_text || h.formatted_output || h.title));
        const providedTitle = stripSectionLabel(h && h.title);
        if (providedTitle && !titleNeedsRewrite(providedTitle, h)) return providedTitle;
        if (embeddedTitle && !titleNeedsRewrite(embeddedTitle, h)) return embeddedTitle;
        return '';
    }
    function cardTitle(h) {
        const title = explicitTitle(h);
        if (title) return title;
        // No contract-compliant title survived; derive a concise label rather
        // than surfacing a full-sentence title (or full claim) on the card.
        const provided = stripSectionLabel(h && h.title);
        const embedded = extractStructuredTitle(h && (h.full_text || h.formatted_output || h.title));
        const hypothesisText = stripSectionLabel(h && h.hypothesis);
        return deriveConciseTitle(provided || embedded || hypothesisText, hypothesisText);
    }
    function markdownSection(label, value) {
        const body = stripMarkdownSectionLabel(value);
        return body ? '**' + label + '**: ' + body : '';
    }
    function stripStructuredTitleSection(value) {
        return String(value == null ? '' : value)
            .replace(/(?:^|\n)\s*\*{0,2}Title\*{0,2}\s*:\s*[\s\S]*?(?=\n\s*\*{0,2}(?:Primary factor|Hypothesis|Justification|Plan)\*{0,2}\s*:|$)/i, (match) => {
                return match.charAt(0) === '\n' ? '\n' : '';
            })
            .trim();
    }
    function hypothesisBodyMarkdown(h) {
        const sections = [
            markdownSection('Hypothesis', h && h.hypothesis),
            markdownSection('Justification', h && h.justification),
            markdownSection('Plan', h && h.plan),
        ].filter(Boolean);
        if (sections.length) return sections.join('\n\n');
        return stripStructuredTitleSection(h && (h.full_text || h.formatted_output || h.hypothesis));
    }
    function hypothesisMarkdown(h) {
        const title = explicitTitle(h);
        const body = hypothesisBodyMarkdown(h);
        const sections = [title ? '**Title**: ' + title : '', body].filter(Boolean);
        if (sections.length) return sections.join('\n\n');
        return String((h && (h.full_text || h.formatted_output || h.hypothesis)) || '').trim();
    }
    function activitySeenKey(roundIndex) {
        return 'da_hs_seen_v1_' + (CFG.projectSlug || 'project') + '_' + String(roundIndex);
    }
    function activitySignatureFor(roundIndex) {
        const entry = state.rounds.find((r) => {
            const idx = r.storage_round_index != null ? r.storage_round_index : r.round_index;
            return idx === roundIndex || r.round_index === roundIndex;
        });
        return entry ? entry.activity_signature : null;
    }
    function seenActivitySignature(roundIndex) {
        try { return window.localStorage.getItem(activitySeenKey(roundIndex)); }
        catch (err) { return null; }
    }
    function markRoundSeen(roundIndex, signature) {
        if (roundIndex == null || !signature) return;
        try { window.localStorage.setItem(activitySeenKey(roundIndex), signature); }
        catch (err) { /* ignore private-mode/quota failures */ }
        if (window.HypothesisActivity && window.HypothesisActivity.markRoundSeen) {
            window.HypothesisActivity.markRoundSeen(roundIndex, signature);
        } else {
            window.dispatchEvent(new Event('hs-activity-seen-changed'));
        }
    }
    function markViewedRoundSeen(signature) {
        if (document.hidden || state.viewRoundIndex == null) return;
        markRoundSeen(state.viewRoundIndex, signature || activitySignatureFor(state.viewRoundIndex));
    }
    function hasUnseenActivity(entry) {
        const signature = entry && entry.activity_signature;
        const idx = entry && (entry.storage_round_index != null ? entry.storage_round_index : entry.round_index);
        return !!(signature && idx != null && seenActivitySignature(idx) !== signature);
    }
    function isViewingLiveRound() {
        return !state.readOnly && state.viewRoundIndex === state.currentRoundIndex;
    }


    // Minimal, safe Markdown-ish renderer (raw HTML never trusted).
    function renderMarkdown(md) {
        if (!md) return '';
        const parts = [];
        const lines = String(md).replace(/\r\n/g, '\n').split('\n');
        let textBuf = [], codeBuf = [], inCode = false;
        function flushText() { if (!textBuf.length) return; parts.push(renderMarkdownText(textBuf.join('\n'))); textBuf = []; }
        function flushCode() { parts.push('<pre><code>' + esc(codeBuf.join('\n')) + '</code></pre>'); codeBuf = []; }
        lines.forEach((line) => {
            if (/^\s*```/.test(line)) {
                if (inCode) { flushCode(); inCode = false; } else { flushText(); inCode = true; }
                return;
            }
            if (inCode) codeBuf.push(line); else textBuf.push(line);
        });
        if (inCode) flushCode();
        flushText();
        return parts.join('');
    }
    function renderMarkdownText(text) {
        const blocks = text.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
        return blocks.map((block) => {
            const lines = block.split('\n');
            if (lines.length === 1) {
                const heading = lines[0].match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
                if (heading) {
                    const level = Math.min(6, heading[1].length);
                    return '<h' + level + '>' + inline(heading[2]) + '</h' + level + '>';
                }
                if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(lines[0])) return '<hr>';
            }
            if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
                return '<ul>' + lines.map((l) => '<li>' + inline(l.replace(/^\s*[-*]\s+/, '')) + '</li>').join('') + '</ul>';
            }
            if (lines.every((l) => /^\s*\d+[.)]\s+/.test(l))) {
                return '<ol>' + lines.map((l) => '<li>' + inline(l.replace(/^\s*\d+[.)]\s+/, '')) + '</li>').join('') + '</ol>';
            }
            return '<p>' + inline(block).replace(/\n/g, '<br>') + '</p>';
        }).join('');
    }
    function inline(text) {
        const codeTokens = [];
        let h = esc(text);
        h = h.replace(/`([^`]+)`/g, (_, code) => { codeTokens.push('<code>' + code + '</code>'); return '@@HS_CODE_' + (codeTokens.length - 1) + '@@'; });
        h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        h = h.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
        h = h.replace(/@@HS_CODE_(\d+)@@/g, (_, idx) => codeTokens[Number(idx)] || '');
        return h;
    }

    function setComposerError(msg) {
        if (!els.composerError) {
            if (msg) setWelcomeError(msg);
            return;
        }
        if (!msg) { els.composerError.hidden = true; els.composerError.textContent = ''; return; }
        els.composerError.textContent = msg;
        els.composerError.hidden = false;
    }
    function setWelcomeError(msg) {
        if (!msg) { els.welcomeError.hidden = true; els.welcomeError.textContent = ''; return; }
        els.welcomeError.textContent = msg;
        els.welcomeError.hidden = false;
    }
    function autoGrow() {
        els.input.style.height = 'auto';
        els.input.style.height = Math.min(els.input.scrollHeight, 44) + 'px';
    }
    function growTextAreaNode(node) {
        if (!node) return;
        node.style.height = 'auto';
        node.style.height = Math.max(node.scrollHeight + 2, 260) + 'px';
    }

    // ---- derivation: events -> cards + threads + selection -------------------

    function hasHypotheses() { return deriveCards().length > 0; }

    function sameThreadText(a, b) {
        return String(a == null ? '' : a).replace(/\s+/g, ' ').trim() ===
            String(b == null ? '' : b).replace(/\s+/g, ' ').trim();
    }

    function pendingThreadCompletionState(pending) {
        if (!pending) return 'none';
        let sawUser = false;
        for (const ev of state.events) {
            if (!ev) continue;
            if (ev.kind === 'message' && ev.role !== 'assistant' && Number(ev.hypothesisIndex) === pending.index &&
                    (!pending.resultEventId || ev.resultEventId === pending.resultEventId) &&
                    sameThreadText(ev.content, pending.message)) {
                sawUser = true;
                continue;
            }
            if (!sawUser) continue;
            if (pending.kind === 'discuss' && ev.kind === 'message' && ev.role === 'assistant' &&
                    Number(ev.hypothesisIndex) === pending.index &&
                    (!pending.resultEventId || ev.resultEventId === pending.resultEventId)) {
                return 'complete';
            }
            if (pending.kind === 'revise' && ev.kind === 'revision' && Number(ev.index) === pending.index &&
                    (!pending.resultEventId || ev.resultEventId === pending.resultEventId)) {
                return 'complete';
            }
        }
        return sawUser ? 'waiting' : 'missing-user';
    }

    function applyPendingThreadFromStorage() {
        const pendings = readPendingThreads();
        if (!pendings.length) return false;
        let restoredAny = false;
        pendings.forEach((pending) => {
            const completion = pendingThreadCompletionState(pending);
            if (completion === 'complete') {
                clearPendingThread(pending.index);
                delete state.pendingThreadRequests[pending.index];
                delete activeThreadRequests[pending.index];
                if (state.busyKind === 'revise' && state.busyIndex === pending.index) {
                    state.busyIndex = null;
                    state.busyKind = null;
                }
                return;
            }
            if (completion === 'missing-user') {
                state.events.push({
                    kind: 'message', role: 'user', content: pending.message,
                    hypothesisIndex: pending.index, resultEventId: pending.resultEventId,
                });
            }
            if (state.focusIndex == null) state.focusIndex = pending.index;
            if (pending.resultEventId) {
                const history = HSE.regenerationHistory(state.events, pending.index);
                const pendingPosition = history.results.findIndex(function (result) {
                    return result.eventId === pending.resultEventId;
                });
                if (pendingPosition >= 0) state.viewedRegenerationPositions[pending.index] = pendingPosition;
            }
            state.pendingThreadRequests[pending.index] = {
                kind: pending.kind,
                resultEventId: pending.resultEventId,
            };
            activeThreadRequests[pending.index] = {
                requestId: 0,
                clientRequestId: pending.requestId || null,
                controller: null,
                stopped: false,
            };
            if (pending.kind === 'revise') {
                state.busyIndex = pending.index;
                state.busyKind = 'revise';
            }
            restoredAny = true;
        });
        return restoredAny;
    }

    async function pollPendingThreadCompletion() {
        if (!readPendingThreads().length) return;
        window.setTimeout(async () => {
            if (!readPendingThreads().length) return;
            const restored = await restoreCurrentCardsFromHistory();
            if (restored) {
                const stillPending = applyPendingThreadFromStorage();
                if (!stillPending) {
                    state.pendingThreadRequests = {};
                    state.busyIndex = null;
                    if (state.busyKind === 'revise') state.busyKind = null;
                }
                showShell();
                render();
                renderSidebar();
            }
            if (readPendingThreads().length) pollPendingThreadCompletion();
        }, 2500);
    }

    function deriveCards() {
        const active = HSE.computeActiveVersions(state.events);
        return Object.keys(active)
            .map((k) => {
                const h = active[k] && active[k].hypothesis;
                if (!h) return null;
                return h.index == null ? Object.assign({}, h, { index: Number(k) }) : h;
            })
            .filter(Boolean)
            .sort((a, b) => Number(a.index) - Number(b.index));
    }

    function regenerationViewFor(h) {
        const idx = Number(h && h.index);
        const history = HSE.regenerationHistory(state.events, idx);
        let viewedPosition = Number(state.viewedRegenerationPositions[idx]);
        if (!Number.isInteger(viewedPosition) || viewedPosition < 0 || viewedPosition >= history.results.length) {
            viewedPosition = history.activePosition;
        }
        const viewed = history.results[viewedPosition];
        return {
            results: history.results,
            activePosition: history.activePosition,
            viewedPosition: viewedPosition,
            viewedResult: viewed || null,
            viewedResultEventId: viewed && viewed.eventId ? viewed.eventId : null,
            threadRecoverable: !!(viewed && viewed.threadRecoverable),
            viewedSnapshot: viewed && viewed.hypothesis ? viewed.hypothesis : h,
            isViewingCurrent: viewedPosition === history.activePosition,
        };
    }

    function navigateRegenerationVersion(index, position) {
        const idx = Number(index);
        if (!idx || hasBlockingCardMutation() || isRegeneratingHypothesis(idx) ||
                getQueuedThreadMessages(idx).length) return;
        if (state.manualEditIndex === idx) {
            setComposerError('Save or cancel your manual changes before viewing another regeneration.');
            return;
        }
        const history = HSE.regenerationHistory(state.events, idx);
        const next = Number(position);
        if (!Number.isInteger(next) || next < 0 || next >= history.results.length) return;
        state.viewedRegenerationPositions[idx] = next;
        // A version is a complete card + conversation context. Navigating a
        // version therefore focuses its slot and switches the transcript too,
        // even in read-only round history.
        state.focusIndex = idx;
        rememberStudioView();
        renderCards();
        renderThread();
        applyComposer();
    }

    // Per-hypothesis chat threads (index -> [{role, content}]). The pure event
    // reducer preserves surviving threads across a removal by shifting indexes
    // above the removed slot, matching the backend's stable thread IDs.
    function deriveThreads() {
        const threads = {};
        deriveCards().forEach((h) => {
            const view = regenerationViewFor(h);
            threads[Number(h.index)] = HSE.regenerationThread(
                state.events, h.index, view.viewedResultEventId
            );
        });
        return threads;
    }

    function latestSelectionSource() {
        const sel = HSE.latestSelection(state.events);
        return sel ? HSE.findSelectionSource(state.events, sel) : null;
    }

    function hasRecordedSelection() {
        return !!HSE.latestSelection(state.events);
    }

    function cardIndexExists(index) {
        const idx = Number(index);
        if (!idx) return false;
        return deriveCards().some((h) => Number(h.index) === idx);
    }

    // ---- count control -------------------------------------------------------

    function buildCountList() {
        els.countList.innerHTML = '';
        for (let n = MIN_N; n <= MAX_N; n++) {
            const opt = el('button', 'hs-count-option', esc(n + ' ' + pluralHyp(n)));
            opt.type = 'button';
            opt.setAttribute('role', 'option');
            opt.dataset.value = n;
            opt.addEventListener('click', (e) => { e.stopPropagation(); setCount(n); closeCountMenu(); });
            els.countList.appendChild(opt);
        }
    }
    function openCountMenu() {
        els.countList.hidden = false;
        els.countTrigger.setAttribute('aria-expanded', 'true');
        els.countList.querySelectorAll('.hs-count-option').forEach((o) => {
            o.setAttribute('aria-selected', Number(o.dataset.value) === state.count ? 'true' : 'false');
        });
    }
    function closeCountMenu() {
        els.countList.hidden = true;
        els.countTrigger.setAttribute('aria-expanded', 'false');
    }
    function setCount(n) {
        state.count = Math.max(MIN_N, Math.min(MAX_N, n));
        els.countLabel.textContent = state.count + ' ' + pluralHyp(state.count);
        persistStudioSnapshot();
    }

    // ---- location / round hash ----------------------------------------------

    function requestedRoundFromLocation() {
        const m = String(window.location.hash || '').match(/^#round-(\d+)$/);
        return m ? Number(m[1]) : null;
    }
    function setViewedRoundLocation(roundIndex, replace) {
        const url = new URL(window.location.href);
        if (roundIndex == null || roundIndex === state.currentRoundIndex) url.hash = '';
        else url.hash = 'round-' + roundIndex;
        if (replace) window.history.replaceState(null, '', url);
        else window.history.pushState(null, '', url);
    }

    // ---- render --------------------------------------------------------------

    function render() {
        applyLayout();
        if (!hasHypotheses() && !state.readOnly) {
            persistStudioSnapshot();
            return;
        }
        renderCards();
        renderThread();
        applyComposer();
        persistStudioSnapshot();
    }

    // Empty (welcome) vs. workspace layout.
    function applyLayout() {
        const showingLiveGeneration = state.generating && isViewingLiveRound();
        const empty = !state.readOnly && !showingLiveGeneration && !hasHypotheses();
        els.welcome.hidden = !empty || showingLiveGeneration;
        els.workspaceMain.hidden = empty || showingLiveGeneration;
        els.activity.hidden = !showingLiveGeneration;
        els.readonlyBar.hidden = !state.readOnly;
        syncWelcomeGenerateButton();
        if (state.readOnly) {
            els.readonlyTitle.textContent = state.viewRoundLabel || 'This round';
            els.readonlyText.textContent = CFG.runningRoundIndex != null
                ? 'Read-only while Round ' + CFG.runningRoundIndex + ' is in progress'
                : 'Read-only history';
            const viewingCurrentRound = (
                state.viewRoundIndex == null ||
                state.currentRoundIndex == null ||
                Number(state.viewRoundIndex) === Number(state.currentRoundIndex)
            );
            els.returnCurrent.hidden = viewingCurrentRound;
            if (els.readonlyCaveat) { els.readonlyCaveat.textContent = ''; els.readonlyCaveat.hidden = true; }
        }
    }

    function renderCards(opts) {
        opts = opts || {};
        els.cards.innerHTML = '';
        const cards = deriveCards();
        const selSource = latestSelectionSource();
        const selectedIndex = selSource ? Number(selSource.index) : null;

        if (!cards.length) {
            const surface = el('div', 'hs-hypothesis-surface is-empty');
            surface.appendChild(el('p', 'v2-muted hs-history-note',
                state.readOnly ? 'No hypotheses were recorded for this round.' : 'No hypotheses in this workspace.'));
            els.cards.appendChild(surface);
            persistStudioSnapshot();
            return;
        }
        const cardIndexes = new Set(cards.map((h) => Number(h.index)));
        // Valid focus/expanded state must point at existing cards.
        if (state.focusIndex != null && !cardIndexes.has(state.focusIndex)) {
            state.focusIndex = null;
        }
        Object.keys(state.expanded).forEach((key) => {
            if (!cardIndexes.has(Number(key))) delete state.expanded[key];
        });
        // Always scope the conversation panel. In read-only history, open the
        // selected hypothesis first so its recorded thread is visible on entry.
        if (state.focusIndex == null) {
            state.focusIndex = selectedIndex || Number(cards[0].index);
        }

        const surface = el('div', 'hs-hypothesis-surface');
        const anyExpanded = cards.some((h) => !!state.expanded[Number(h.index)]);
        if (els.workspaceMain) els.workspaceMain.classList.toggle('has-expanded-hypothesis', anyExpanded);
        surface.classList.add(anyExpanded ? 'has-expanded-card' : 'is-static-row');
        const grid = el('div', 'hs-card-grid');
        if (cards.length <= 1) grid.classList.add('is-single');
        cards.forEach((h) => grid.appendChild(renderCard(h, selectedIndex)));
        surface.appendChild(grid);
        if (anyExpanded) installVerticalOnlyScroll(surface);
        else installStaticScrollBlock(surface);
        els.cards.appendChild(surface);
        if (anyExpanded && opts.restoreScrollTop != null) {
            const top = Number(opts.restoreScrollTop) || 0;
            requestAnimationFrame(() => { surface.scrollTop = Math.min(top, surface.scrollHeight); });
        }
        persistStudioSnapshot();
    }

    function renderCard(h, selectedIndex) {
        const idx = Number(h.index);
        const versionView = regenerationViewFor(h);
        const displayHypothesis = versionView.viewedSnapshot || h;
        const isSelected = selectedIndex === idx;
        const isFocused = state.focusIndex === idx;
        const expanded = !!state.expanded[idx];
        const isManualEditing = !state.readOnly && state.manualEditIndex === idx;
        const isRegenerating = isRegeneratingHypothesis(idx);

        const card = el('article', 'hs-card');
        card.dataset.index = idx;
        if (isFocused) card.classList.add('is-focused');
        else if (!state.readOnly && state.focusIndex != null) card.classList.add('is-dimmed');
        if (isSelected) card.classList.add('is-selected');
        if (expanded) card.classList.add('is-expanded');
        else card.classList.add('is-collapsed');
        if ((state.busyIndex === idx && state.busyKind !== 'revise') || isRegenerating) card.classList.add('is-busy');
        if (isRegenerating) card.classList.add('is-regenerating');

        if (isRegenerating) {
            const loading = el('div', 'hs-card-regenerating');
            loading.appendChild(activitySpinner('hs-activity-spinner hs-card-spinner'));
            loading.appendChild(el('p', 'hs-card-regenerating-label', 'Regenerating hypothesis...'));
            card.appendChild(loading);
            const cancel = el('button', 'v2-btn v2-btn-secondary hs-card-regenerate-cancel', 'Cancel');
            cancel.type = 'button';
            cancel.addEventListener('click', (e) => {
                e.stopPropagation();
                cancelRegenerate(idx);
            });
            card.appendChild(cancel);
            return card;
        }

        // Header: number + title. Clicking the title focuses this hypothesis.
        const head = el('div', 'hs-card-head');
        const titleWrap = el('button', 'hs-card-titlebtn');
        titleWrap.type = 'button';
        titleWrap.setAttribute('aria-label', 'Focus hypothesis ' + idx);
        titleWrap.addEventListener('click', () => focusCard(idx));
        titleWrap.appendChild(el('span', 'hs-card-num', esc(String(idx))));
        // Titles are word-budgeted by the backend/JS contract, so the full title
        // is shown verbatim — no clamp, no ellipsis, no tooltip needed.
        titleWrap.appendChild(el('span', 'hs-card-title', esc(cardTitle(displayHypothesis))));
        head.appendChild(titleWrap);
        if (!state.readOnly) head.appendChild(buildOverflow(h, displayHypothesis, versionView));

        card.appendChild(head);

        const expandBtn = el('button', 'hs-card-action hs-card-expand', expanded ? 'Hide full hypothesis' : 'View full hypothesis');
        expandBtn.type = 'button';
        expandBtn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        expandBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const nextExpanded = !expanded;
            const priorSurface = els.cards.querySelector('.hs-hypothesis-surface.has-expanded-card');
            const priorScrollTop = priorSurface ? priorSurface.scrollTop : null;
            if (!nextExpanded && state.manualEditIndex === idx) {
                state.manualEditIndex = null;
                state.manualEditDraft = '';
            }
            state.expanded[idx] = nextExpanded;
            rememberStudioView();
            const stillExpanded = Object.keys(state.expanded).some((key) => !!state.expanded[key]);
            renderCards(!nextExpanded && stillExpanded ? { restoreScrollTop: priorScrollTop } : undefined);
        });

        if (expanded) {
            const body = el('div', 'hs-card-details');
            body.addEventListener('click', () => {
                if (state.focusIndex !== idx) focusCard(idx);
            });
            if (isManualEditing) {
                body.appendChild(manualEditBlock(h));
            } else {
                body.appendChild(el('div', 'v2-prose hs-hypothesis-markdown', renderMarkdown(hypothesisBodyMarkdown(displayHypothesis) || 'No hypothesis content was provided.')));
            }
            card.appendChild(body);
        }

        const controls = el('div', 'hs-card-controls');
        if (versionView.results.length > 1) {
            controls.appendChild(regenerationVersionControls(h, versionView));
        }
        controls.appendChild(expandBtn);
        if (state.readOnly && isSelected) {
            controls.appendChild(el('span', 'hs-card-action hs-card-selected-static', 'Selected'));
        } else if (!state.readOnly && !isSelected) {
            const selectBtn = el('button', 'hs-card-action hs-card-select', 'Select for next round');
            selectBtn.type = 'button';
            selectBtn.disabled = hasAnyRegeneration() || hasAnyPendingThread();
            selectBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openReview(displayHypothesis, h, versionView.viewedPosition);
            });
            controls.appendChild(selectBtn);
        }
        card.appendChild(controls);
        return card;
    }

    function regenerationVersionControls(activeHypothesis, view) {
        const idx = Number(activeHypothesis.index);
        const nav = el('div', 'hs-regeneration-nav');
        const blocked = state.manualEditIndex === idx || hasBlockingCardMutation() || isRegeneratingHypothesis(idx) ||
            getQueuedThreadMessages(idx).length > 0;

        const previous = el('button', 'hs-regeneration-arrow', '‹');
        previous.type = 'button';
        previous.setAttribute('aria-label', 'Earlier result');
        previous.disabled = blocked || view.viewedPosition <= 0;
        previous.addEventListener('click', (e) => {
            e.stopPropagation();
            navigateRegenerationVersion(idx, view.viewedPosition - 1);
        });
        nav.appendChild(previous);
        nav.appendChild(el('span', 'hs-regeneration-count',
            'Version ' + (view.viewedPosition + 1) + ' of ' + view.results.length));
        const next = el('button', 'hs-regeneration-arrow', '›');
        next.type = 'button';
        next.setAttribute('aria-label', 'Later result');
        next.disabled = blocked || view.viewedPosition >= view.results.length - 1;
        next.addEventListener('click', (e) => {
            e.stopPropagation();
            navigateRegenerationVersion(idx, view.viewedPosition + 1);
        });
        nav.appendChild(next);
        return nav;
    }

    function manualEditBlock(h) {
        const idx = Number(h.index);
        const box = el('div', 'hs-manual-edit');
        const ta = el('textarea', 'hs-manual-edit-input');
        ta.dataset.index = String(idx);
        ta.value = state.manualEditDraft;
        ta.setAttribute('aria-label', 'Edit full hypothesis ' + idx);
        ta.addEventListener('input', () => { state.manualEditDraft = ta.value; growTextAreaNode(ta); });
        ta.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                const text = ta.value.trim();
                if (text) runManualEdit(h, text);
            }
        });
        box.appendChild(ta);
        requestAnimationFrame(() => growTextAreaNode(ta));
        const actions = el('div', 'hs-manual-edit-actions');
        const cancel = el('button', 'v2-btn v2-btn-secondary', 'Cancel');
        cancel.type = 'button';
        cancel.addEventListener('click', (e) => {
            e.stopPropagation();
            state.manualEditIndex = null;
            state.manualEditDraft = '';
            renderCards();
        });
        const save = el('button', 'v2-btn v2-btn-primary', 'Save changes');
        save.type = 'button';
        save.addEventListener('click', (e) => {
            e.stopPropagation();
            const text = ta.value.trim();
            if (text) runManualEdit(h, text);
        });
        actions.appendChild(cancel);
        actions.appendChild(save);
        box.appendChild(actions);
        return box;
    }

    function buildOverflow(activeHypothesis, displayedHypothesis, versionView) {
        const h = activeHypothesis;
        const wrap = el('div', 'v2-menu-wrap hs-card-overflow');
        const btn = el('button', 'v2-row-menu', '<svg class="v2-dots" viewBox="0 0 4 16" width="4" height="16" aria-hidden="true"><circle cx="2" cy="2" r="1.55"/><circle cx="2" cy="8" r="1.55"/><circle cx="2" cy="14" r="1.55"/></svg>');
        btn.type = 'button';
        btn.setAttribute('aria-label', 'More actions for hypothesis ' + h.index);
        btn.setAttribute('aria-haspopup', 'menu');
        btn.setAttribute('aria-expanded', 'false');
        const menu = el('div', 'v2-menu', '');
        menu.setAttribute('role', 'menu');
        // Current-version actions are independent of candidate count. A lone
        // remaining hypothesis can still be regenerated or removed.
        if (versionView.isViewingCurrent) {
            menu.appendChild(menuItem('Regenerate', () => runRegenerate(h, '')));
        }
        menu.appendChild(menuItem('Edit manually', () => openManualEdit(
            displayedHypothesis, versionView.viewedPosition
        )));
        if (versionView.isViewingCurrent) menu.appendChild(removeMenuItem(h));
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const willOpen = !menu.classList.contains('is-open');
            closeAllMenus();
            if (willOpen) { menu.classList.add('is-open'); btn.setAttribute('aria-expanded', 'true'); }
        });
        wrap.appendChild(btn); wrap.appendChild(menu);
        return wrap;
    }
    function menuItem(labelText, onActivate) {
        const item = el('button', 'v2-menu-item', esc(labelText));
        item.type = 'button';
        item.setAttribute('role', 'menuitem');
        item.addEventListener('click', (e) => { e.stopPropagation(); closeAllMenus(); if (onActivate) onActivate(); });
        return item;
    }
    function removeMenuItem(h) {
        const row = el('div', 'hs-remove-menu-row');
        row.setAttribute('role', 'menuitem');
        const reset = () => {
            row.className = 'hs-remove-menu-row';
            row.innerHTML = '';
            const remove = el('button', 'v2-menu-item hs-remove-menu-trigger', 'Remove');
            remove.type = 'button';
            remove.addEventListener('click', (e) => {
                e.stopPropagation();
                if (state.busy || hasAnyRegeneration()) return;
                row.classList.add('is-confirming');
                row.innerHTML = '';
                const actions = el('span', 'hs-remove-menu-actions');
                const cancel = el('button', 'hs-remove-menu-cancel', 'Cancel');
                cancel.type = 'button';
                cancel.addEventListener('click', (cancelEvent) => { cancelEvent.stopPropagation(); reset(); });
                const del = el('button', 'hs-remove-menu-delete', 'Delete');
                del.type = 'button';
                del.addEventListener('click', (deleteEvent) => {
                    deleteEvent.stopPropagation();
                    closeAllMenus();
                    runRemove(h);
                });
                actions.appendChild(cancel);
                actions.appendChild(del);
                row.appendChild(actions);
                del.focus();
            });
            row.appendChild(remove);
        };
        row._resetRemoveMenu = reset;
        reset();
        return row;
    }
    function closeAllMenus() {
        document.querySelectorAll('.hs-remove-menu-row').forEach((row) => {
            if (typeof row._resetRemoveMenu === 'function') row._resetRemoveMenu();
        });
        document.querySelectorAll('.v2-menu.is-open').forEach((menu) => {
            menu.classList.remove('is-open');
            const b = menu.previousElementSibling;
            if (b && b.classList.contains('v2-row-menu')) b.setAttribute('aria-expanded', 'false');
        });
    }

    // ---- focused-hypothesis chat thread --------------------------------------

    function focusCard(index) {
        const idx = Number(index);
        if (!idx) return;
        if (state.focusIndex === idx) {
            // Reassert the visual state in case another card state transition
            // (selected/busy/pending) caused the DOM classes to be repainted.
            renderCards();
            renderThread();
            applyComposer();
            return;
        }
        const wasExpanded = !!state.expanded[idx];
        if (state.manualEditIndex !== idx) {
            state.manualEditIndex = null;
            state.manualEditDraft = '';
        }
        state.focusIndex = idx;
        state.expanded = wasExpanded ? { [idx]: true } : {};
        rememberStudioView();
        renderCards();
        renderThread();
        applyComposer();
        if (!state.readOnly) els.input.focus();
    }

    function renderThread() {
        els.thread.innerHTML = '';
        els.thread.hidden = false;
        if (state.focusIndex == null) {
            if (state.readOnly) {
                els.thread.hidden = true;
                return;
            }
            els.thread.appendChild(el('p', 'v2-muted hs-thread-empty', 'Select a hypothesis above to refine it.'));
            return;
        }
        const threads = deriveThreads();
        const messages = threads[state.focusIndex] || [];
        if (!messages.length && state.readOnly) {
            els.thread.appendChild(el('p', 'v2-muted hs-thread-empty', 'No refinement conversation was recorded for this hypothesis.'));
        }
        installVerticalOnlyScroll(els.thread);
        messages.forEach((m) => els.thread.appendChild(turnEl(m.role, m.role === 'user' ? esc(m.content) : renderMarkdown(m.content))));
        if (isViewedThreadPending(state.focusIndex)) {
            els.thread.appendChild(turnEl('assistant', '<span class="hs-typing" aria-label="DiscoveryAgent is thinking"><span></span><span></span><span></span></span>', true));
        }
        scrollThreadToBottom();
        renderQueuedMessages();
        persistStudioSnapshot();
    }

    function turnEl(role, contentHtml, pending) {
        const turn = el('div', 'hs-turn hs-turn-' + role);
        turn.appendChild(el('p', 'hs-turn-role', role === 'user' ? 'You' : 'DiscoveryAgent'));
        const body = el('div', 'hs-turn-body' + (role === 'assistant' ? ' v2-prose' : ''), contentHtml);
        if (pending) body.classList.add('is-pending');
        turn.appendChild(body);
        return turn;
    }

    function renderQueuedMessages() {
        if (!els.queuedMessages) return;
        els.queuedMessages.innerHTML = '';
        const queue = state.focusIndex == null ? [] : getQueuedThreadMessages(state.focusIndex);
        els.queuedMessages.hidden = state.readOnly || !queue.length;
        if (queue.length && state.pausedThreadQueues[state.focusIndex]) {
            const notice = el('div', 'hs-queue-paused');
            notice.appendChild(el('span', '', 'Queue paused because you interrupted'));
            const resume = el('button', 'hs-queue-resume', 'Resume');
            resume.type = 'button';
            resume.addEventListener('click', () => {
                const idx = state.focusIndex;
                delete state.pausedThreadQueues[idx];
                renderQueuedMessages();
                persistStudioSnapshot();
                promoteNextQueuedThreadMessage(idx);
            });
            notice.appendChild(resume);
            els.queuedMessages.appendChild(notice);
        }
        queue.forEach((entry, index) => {
            const item = el('div', 'hs-queued-item');
            item.appendChild(el('span', 'hs-queued-icon', '↳'));
            item.appendChild(el('span', 'hs-queued-text', esc(entry.text || '')));
            item.appendChild(el('span', 'hs-queued-status', entry.mode === 'edit' ? 'Edit queued' : 'Chat queued'));
            const deleteButton = el('button', 'hs-queued-delete', '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M6.2 2.4h3.6l.5 1.1h2.5v1.2H3.2V3.5h2.5l.5-1.1Zm1 .9-.1.2h1.8l-.1-.2H7.2ZM4.2 5.6h7.6l-.4 7.2c0 .7-.6 1.2-1.3 1.2H5.9c-.7 0-1.2-.5-1.3-1.2L4.2 5.6Zm1.3 1.2.3 5.9c0 .1.1.2.2.2h4.2c.1 0 .2-.1.2-.2l.3-5.9H5.5Zm1.4 1h1.1v4H6.9v-4Zm2.1 0h1.1v4H9v-4Z" fill="currentColor"/></svg>');
            deleteButton.type = 'button';
            deleteButton.setAttribute('aria-label', 'Remove queued message');
            deleteButton.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                removeQueuedThreadMessage(state.focusIndex, index);
                if (!getQueuedThreadMessages(state.focusIndex).length) {
                    delete state.pausedThreadQueues[state.focusIndex];
                }
                renderQueuedMessages();
                persistStudioSnapshot();
                if (!els.input.disabled) els.input.focus();
            });
            item.appendChild(deleteButton);
            els.queuedMessages.appendChild(item);
        });
    }

    function prefersReducedMotion() {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
    function scrollThreadToBottom() {
        const behavior = prefersReducedMotion() ? 'auto' : 'smooth';
        requestAnimationFrame(() => { els.thread.scrollTo({ top: els.thread.scrollHeight, behavior: behavior }); });
    }
    function blockDominantHorizontalWheel(e) {
        if (Math.abs(e.deltaX || 0) <= Math.abs(e.deltaY || 0)) return;
        e.preventDefault();
    }
    function blockAllWheel(e) {
        e.preventDefault();
        e.stopPropagation();
    }
    function blockStaticHypothesisSurfaceWheel(e) {
        const surface = e.target && e.target.closest ? e.target.closest('.hs-hypothesis-surface.is-static-row') : null;
        if (!surface) return;
        surface.scrollTop = 0;
        surface.scrollLeft = 0;
        blockAllWheel(e);
    }
    function installVerticalOnlyScroll(node) {
        if (!node || node.dataset.hsVerticalOnlyScroll === 'true') return;
        node.dataset.hsVerticalOnlyScroll = 'true';
        node.addEventListener('wheel', blockDominantHorizontalWheel, { passive: false });
    }
    function installStaticScrollBlock(node) {
        if (!node || node.dataset.hsStaticScrollBlock === 'true') return;
        node.dataset.hsStaticScrollBlock = 'true';
        node.scrollTop = 0;
        node.scrollLeft = 0;
        node.addEventListener('wheel', blockAllWheel, { passive: false });
        node.addEventListener('scroll', () => { node.scrollTop = 0; node.scrollLeft = 0; });
    }

    // ---- composer ------------------------------------------------------------

    function applyComposer() {
        els.composer.hidden = state.readOnly || !hasHypotheses();
        const focused = state.focusIndex != null;
        if (els.chatScope) {
            const threads = deriveThreads();
            const messages = focused ? (threads[state.focusIndex] || []) : [];
            const showStarterTitle = !state.readOnly && hasHypotheses() && focused && !messages.length;
            els.chatScope.hidden = !showStarterTitle;
            if (showStarterTitle) {
                els.chatScopeTitle.textContent = 'Chat About Hypothesis ' + state.focusIndex;
                if (els.chatScopeHelp) {
                    els.chatScopeHelp.textContent = '';
                    els.chatScopeHelp.hidden = true;
                }
            }
        }
        if (els.modeToggle) {
            const showModeToggle = !state.readOnly && hasHypotheses() && focused;
            els.modeToggle.hidden = !showModeToggle;
            if (!showModeToggle) closeModeMenu();
            if (els.modeLabel) els.modeLabel.textContent = state.composerMode === 'edit' ? 'Edit' : 'Chat';
            if (els.modeChat) {
                const active = state.composerMode !== 'edit';
                els.modeChat.classList.toggle('is-active', active);
                els.modeChat.setAttribute('aria-checked', active ? 'true' : 'false');
            }
            if (els.modeEdit) {
                const active = state.composerMode === 'edit';
                els.modeEdit.classList.toggle('is-active', active);
                els.modeEdit.setAttribute('aria-checked', active ? 'true' : 'false');
            }
        }
        renderQueuedMessages();
        if (els.composer.hidden) return;
        els.input.disabled = !focused;
        els.input.placeholder = focused
            ? (state.composerMode === 'edit'
                ? 'Describe the edit to apply to Hypothesis ' + state.focusIndex + '...'
                : 'Ask a question about Hypothesis ' + state.focusIndex + '...')
            : 'Select a hypothesis above to refine it...';
        refreshSendEnabled();
    }

    function openModeMenu() {
        if (!els.modeMenu || !els.modeTrigger || els.modeToggle.hidden) return;
        els.modeMenu.hidden = false;
        els.modeTrigger.setAttribute('aria-expanded', 'true');
    }
    function closeModeMenu() {
        if (!els.modeMenu || !els.modeTrigger) return;
        els.modeMenu.hidden = true;
        els.modeTrigger.setAttribute('aria-expanded', 'false');
    }
    function setComposerMode(mode) {
        state.composerMode = mode === 'edit' ? 'edit' : 'chat';
        closeModeMenu();
        applyComposer();
        persistStudioSnapshot();
        if (!state.readOnly && state.focusIndex != null) els.input.focus();
    }



    // ---- network -------------------------------------------------------------

    async function api(path, method, body, signal) {
        const opts = { method: method || 'GET', credentials: 'include', headers: { 'Accept': 'application/json' } };
        if (signal) opts.signal = signal;
        if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
        return fetch(path, opts);
    }
    async function apiJson(path, method, body, signal) {
        const resp = await api(path, method, body, signal);
        let data = null;
        try { data = await resp.json(); } catch (e) { /* non-JSON */ }
        return { resp, data };
    }
    async function apiJsonWithTimeout(path, method, body, timeoutMs) {
        const controller = window.AbortController ? new AbortController() : null;
        const timer = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
        try {
            const resp = await api(path, method, body, controller ? controller.signal : undefined);
            let data = null;
            try { data = await resp.json(); } catch (e) { /* non-JSON */ }
            return { resp, data };
        } finally {
            if (timer) window.clearTimeout(timer);
        }
    }

    // ---- initialize + restore ------------------------------------------------

    async function initialize(opts) {
        opts = opts || {};
        const restoredLocal = !opts.skipLocalSnapshot && restoreLocalSnapshot();
        if (!restoredLocal) showLoading();
        if (!opts.skipGenerationStatus) {
            const deferred = await resumeIfGenerationInProgress();
            if (deferred) return;
            if (state.generating) {
                state.generating = false;
                stopActivity();
                setBusy(!!(state.busyIndex || hasAnyPendingThread()));
            }
            readPendingRegenerations().forEach((entry) => {
                if (!state.regenerationRequests[entry.index]) {
                    state.regenerationRequests[entry.index] = { requestId: 0, canceled: false, restored: true };
                }
            });
        }
        let init;
        try {
            init = await apiJsonWithTimeout('/api/initialize', 'POST', { num_hypotheses: state.count, generate_plan: true }, 8000);
        } catch (err) {
            if (restoredLocal) {
                loadHistory();
                return;
            }
            await showStartupFallback('The server is still busy. You can stay on this screen, move elsewhere, or try again in a moment.');
            return;
        }
        if (!init.resp.ok || !init.data || init.data.success === false) {
            if (restoredLocal) return;
            showError((init.data && init.data.error) || 'The studio could not be initialized.');
            return;
        }
        if (init.data.redirect_to_round_monitor) {
            window.location.href = init.data.round_workspace_url || CFG.roundWorkspaceUrl || '/round_monitor';
            return;
        }

        const locallyRestoredFocus = restoredLocal && state.focusIndex != null ? Number(state.focusIndex) : null;
        const locallyRestoredExpanded = restoredLocal ? normalizedExpanded(state.expanded) : null;
        const restoredFromHistory = await restoreCurrentFromHistory(init.data);
        const restored = init.data.restored_hypotheses;
        if (!restoredFromHistory && Array.isArray(restored) && restored.length) {
            state.events.push({ kind: 'hypotheses', id: nextEventId(), hypotheses: restored });
        } else if (restoredLocal && !restoredFromHistory && !(Array.isArray(restored) && restored.length)) {
            // The server is authoritative. If hypothesis artifacts were reset
            // while this page was elsewhere, never let a local warm-start
            // snapshot resurrect the removed cards or welcome/workspace state.
            state.events = [];
            state.generationId = null;
            state.focusIndex = null;
            state.expanded = {};
            clearStudioSnapshot();
        }
        state.generationId = init.data.generation_id || state.generationId || null;
        if (!state.readOnly && locallyRestoredFocus != null && cardIndexExists(locallyRestoredFocus)) {
            state.focusIndex = locallyRestoredFocus;
            state.expanded = locallyRestoredExpanded || {};
        } else {
            applyStoredLiveView();
        }
        const restoredPendingThread = applyPendingThreadFromStorage();
        if (restoredPendingThread) pollPendingThreadCompletion();

        const restoredRequestedRound = await restoreRequestedRoundFromLocation();
        showShell();
        if (!restoredRequestedRound) {
            render();
            if (state.rounds.length) {
                renderSidebar();
                markViewedRoundSeen();
            } else {
                loadHistory();
            }
        }
        setCount(state.count);
    }

    async function initializeRunningRoundHistory() {
        showLoading();
        try {
            const list = await apiJson('/api/hypothesis_history', 'GET');
            if (!list.resp.ok || !list.data || list.data.success === false) {
                throw new Error('Could not load round history.');
            }
            applyHistoryList(list.data);
            if (state.currentRoundIndex == null) {
                throw new Error('No hypothesis history is available for the running round.');
            }
            const detail = await apiJson('/api/hypothesis_history/' + state.currentRoundIndex, 'GET');
            if (!detail.resp.ok || !detail.data || detail.data.success === false) {
                throw new Error('Could not open the running round history.');
            }
            state.readOnly = true;
            state.viewRoundIndex = detail.data.storage_round_index != null
                ? detail.data.storage_round_index : detail.data.round_index;
            state.viewRoundLabel = detail.data.label || state.currentRoundLabel || ('Round ' + CFG.runningRoundIndex);
            state.partialHistory = !!detail.data.partial_history;
            state.events = HSE.normalizeHistoryEvents(detail.data, nextEventId);
            state.viewedRegenerationPositions = {};
            state.focusIndex = null;
            showShell();
            renderSidebar();
            render();
            markViewedRoundSeen(detail.data.activity_signature);
        } catch (err) {
            showError(err.message || 'Could not open the running round history.');
        }
    }

    async function showStartupFallback(message) {
        const pendingRegeneration = readPendingRegeneration();
        if (pendingRegeneration) {
            const requestId = ++regenerateSeq;
            state.generating = false;
            state.regenerationRequests[pendingRegeneration.index] = {
                requestId: requestId, canceled: false, restored: true,
            };
            showShell();
            const restored = await restoreCurrentCardsFromHistory();
            if (restored) {
                state.focusIndex = pendingRegeneration.index;
                render();
                renderSidebar();
                pollRegenerationCompletion(requestId, pendingRegeneration.index);
                return;
            }
        }

        const restored = await restoreCurrentCardsFromHistory();
        if (restored) {
            state.generating = false;
            state.busyIndex = null;
            state.busyKind = null;
            setBusy(false);
            applyStoredLiveView();
            applyPendingThreadFromStorage();
            showShell();
            render();
            renderSidebar();
            setComposerError(null);
            if (readPendingThread()) pollPendingThreadCompletion();
            return;
        }

        state.generating = false;
        state.busyIndex = null;
        state.busyKind = null;
        setBusy(false);
        showShell();
        applyLayout();
        setWelcomeError(message || 'The studio is taking longer than expected to open. Please try again in a moment.');
        loadHistory();
    }

    // ---- history sidebar -----------------------------------------------------

    async function loadHistory() {
        try {
            const { resp, data } = await apiJson('/api/hypothesis_history', 'GET');
            if (!resp.ok || !data || data.success === false) return null;
            applyHistoryList(data);
            renderSidebar();
            markViewedRoundSeen();
            return data;
        } catch (err) { return null; }
    }

    function applyHistoryList(data) {
        state.rounds = Array.isArray(data.rounds) ? data.rounds : [];
        state.currentRoundIndex = data.current_round_index;
        const current = state.rounds.find((r) => r.round_index === state.currentRoundIndex || r.is_current);
        state.currentRoundLabel = current ? current.label : null;
        if (state.viewRoundIndex === null) {
            state.viewRoundIndex = data.current_round_index;
            state.viewRoundLabel = state.currentRoundLabel;
        } else {
            const viewed = state.rounds.find((r) => r.round_index === state.viewRoundIndex);
            if (viewed) state.viewRoundLabel = viewed.label;
        }
    }

    function renderSidebar() {
        els.roundList.innerHTML = '';
        if (!state.rounds.length) { els.sidebar.hidden = true; return; }
        els.sidebar.hidden = false;
        const ordered = state.rounds.slice().sort((a, b) => {
            const at = a.target_round_number != null ? a.target_round_number : a.round_index;
            const bt = b.target_round_number != null ? b.target_round_number : b.round_index;
            return bt - at;
        });
        ordered.forEach((r) => {
            const item = el('button', 'hs-round-item');
            item.type = 'button';
            if (r.round_index === state.viewRoundIndex) item.classList.add('is-active');
            item.appendChild(el('span', 'hs-round-item-name', esc(r.label || ('Round ' + r.round_index))));
            if (r.is_current) item.appendChild(el('span', 'hs-round-item-note', 'Current'));
            const rowIndex = r.storage_round_index != null ? r.storage_round_index : r.round_index;
            if (hasUnseenActivity(r) && rowIndex !== state.viewRoundIndex) {
                const dot = el('span', 'hs-round-unread-dot');
                dot.title = 'Unseen activity';
                dot.setAttribute('aria-label', 'Unseen activity');
                item.appendChild(dot);
            }
            item.addEventListener('click', () => selectRound(r));
            els.roundList.appendChild(item);
        });
        persistStudioSnapshot();
    }

    function selectRound(entry) {
        if ((state.busy || hasAnyRegeneration()) && !state.generating) return;
        if (entry.round_index === state.viewRoundIndex) return;
        if (entry.is_current || entry.round_index === state.currentRoundIndex) enterLiveRound();
        else enterHistoryRound(entry.round_index);
    }

    function stashLive() {
        if (liveStash) return;
        liveStash = {
            events: state.events, generationId: state.generationId,
            focusIndex: state.focusIndex, expanded: state.expanded,
            manualEditIndex: state.manualEditIndex, manualEditDraft: state.manualEditDraft,
            viewedRegenerationPositions: state.viewedRegenerationPositions,
            partialHistory: state.partialHistory, viewRoundLabel: state.viewRoundLabel,
        };
    }

    function enterLiveRound() {
        setViewedRoundLocation(null);
        if (liveStash) { Object.assign(state, liveStash); liveStash = null; }
        state.readOnly = CFG.runningRoundIndex != null || hasRecordedSelection();
        if (state.readOnly) state.focusIndex = null;
        state.viewRoundIndex = state.currentRoundIndex;
        state.viewRoundLabel = state.currentRoundLabel;
        state.partialHistory = false;
        renderSidebar();
        render();
        rememberStudioView();
        markViewedRoundSeen();
    }

    async function enterHistoryRound(roundIndex, opts) {
        opts = opts || {};
        stashLive();
        const backgroundGeneration = state.generating;
        if (!backgroundGeneration) setBusy(true);
        try {
            const { resp, data } = await apiJson('/api/hypothesis_history/' + roundIndex, 'GET');
            if (!resp.ok || !data || data.success === false) {
                setComposerError((data && data.error) || 'Could not open that round.');
                if (!backgroundGeneration) setBusy(false);
                return;
            }
            state.readOnly = true;
            state.viewRoundIndex = data.storage_round_index != null ? data.storage_round_index : data.round_index;
            state.viewRoundLabel = data.label || entryLabelFor(roundIndex);
            state.partialHistory = !!data.partial_history;
            state.focusIndex = null;
            state.expanded = {};
            state.manualEditIndex = null;
            state.manualEditDraft = '';
            state.events = HSE.normalizeHistoryEvents(data, nextEventId);
            state.viewedRegenerationPositions = {};
            applyStoredViewForDisplayedRound();
            setViewedRoundLocation(state.viewRoundIndex, !!opts.replaceLocation);
            if (!backgroundGeneration) setBusy(false);
            renderSidebar();
            render();
            rememberStudioView();
            markViewedRoundSeen(data.activity_signature);
        } catch (err) {
            setComposerError('Network error opening that round.');
            if (!backgroundGeneration) setBusy(false);
        }
    }

    function entryLabelFor(roundIndex) {
        const entry = state.rounds.find((r) => r.round_index === roundIndex || r.storage_round_index === roundIndex);
        return entry ? entry.label : null;
    }

    async function restoreCurrentFromHistory(initData) {
        try {
            const list = await apiJson('/api/hypothesis_history', 'GET');
            if (!list.resp.ok || !list.data || list.data.success === false) return false;
            applyHistoryList(list.data);
            if (state.currentRoundIndex == null) return false;
            const detail = await apiJson('/api/hypothesis_history/' + state.currentRoundIndex, 'GET');
            if (!detail.resp.ok || !detail.data || detail.data.success === false) return false;
            state.viewRoundIndex = detail.data.storage_round_index != null ? detail.data.storage_round_index : detail.data.round_index;
            state.viewRoundLabel = detail.data.label || state.currentRoundLabel;
            state.partialHistory = !!detail.data.partial_history;
            state.events = HSE.normalizeHistoryEvents(detail.data, nextEventId);
            state.viewedRegenerationPositions = {};
            state.readOnly = !!detail.data.read_only || hasRecordedSelection();
            if (state.readOnly) state.focusIndex = null;
            const initHasHypotheses = !!(initData && Array.isArray(initData.restored_hypotheses) && initData.restored_hypotheses.length);
            if (!hasHypotheses() && initHasHypotheses) return false;
            return true;
        } catch (err) {
            return false;
        }
    }

    async function restoreCurrentCardsFromHistory() {
        try {
            const list = await apiJsonWithTimeout('/api/hypothesis_history', 'GET', undefined, 2500);
            if (!list.resp.ok || !list.data || list.data.success === false) return false;
            applyHistoryList(list.data);
            if (state.currentRoundIndex == null) return false;
            const detail = await apiJsonWithTimeout('/api/hypothesis_history/' + state.currentRoundIndex, 'GET', undefined, 3500);
            if (!detail.resp.ok || !detail.data || detail.data.success === false) return false;
            state.viewRoundIndex = detail.data.storage_round_index != null ? detail.data.storage_round_index : detail.data.round_index;
            state.viewRoundLabel = detail.data.label || state.currentRoundLabel;
            state.partialHistory = !!detail.data.partial_history;
            state.events = HSE.normalizeHistoryEvents(detail.data, nextEventId);
            state.viewedRegenerationPositions = {};
            state.readOnly = !!detail.data.read_only || hasRecordedSelection();
            if (state.readOnly) state.focusIndex = null;
            return hasHypotheses();
        } catch (err) {
            return false;
        }
    }

    async function restoreRequestedRoundFromLocation() {
        const hashRequested = requestedRoundFromLocation();
        const saved = readStudioView();
        const requested = hashRequested != null
            ? hashRequested
            : (saved && saved.viewRoundIndex !== state.currentRoundIndex ? saved.viewRoundIndex : null);
        if (requested == null || requested === state.currentRoundIndex) {
            if (hashRequested != null) setViewedRoundLocation(null, true);
            return false;
        }
        const entry = state.rounds.find((r) => r.round_index === requested || r.storage_round_index === requested);
        if (!entry || entry.is_current) {
            if (hashRequested != null) setViewedRoundLocation(null, true);
            return false;
        }
        await enterHistoryRound(requested, { replaceLocation: true });
        return state.readOnly && state.viewRoundIndex === requested;
    }

    // ---- generation resume -----------------------------------------------------

    // Equivalent to apiJson('/api/generation_status', 'GET'), with a short
    // timeout so startup never gets trapped behind a busy mutation request.
    async function generationStatus() {
        try {
            const { resp, data } = await apiJsonWithTimeout('/api/generation_status', 'GET', undefined, 1800);
            if (!resp.ok || !data || data.success === false) return null;
            return data;
        } catch (err) {
            return { unknown: true };
        }
    }

    async function resumeIfGenerationInProgress() {
        const status = await generationStatus();
        const regen = status && status.regeneration;
        if (regen && regen.in_progress) {
            if (!isCanceledRegeneration(regen)) return resumeRegenerationInProgress(regen);
            clearPendingRegeneration(regen.index);
        } else {
            clearCanceledRegeneration();
        }
        const pendingRegeneration = readPendingRegeneration();
        if (pendingRegeneration && !isCanceledRegeneration(pendingRegeneration)) return resumeRegenerationInProgress(pendingRegeneration);
        if (status && status.cancel_requested) {
            state.generating = false;
            state.cancelRequested = false;
            setBusy(false);
            stopActivity();
            return false;
        }
        if (!status || (!status.in_progress && !status.unknown)) return false;
        state.generating = true;
        setBusy(true);
        showShell();
        applyLayout();
        startActivity();
        if (status.unknown && els.activityError) {
            els.activityError.textContent = 'Checking generation status...';
            els.activityError.hidden = false;
        }
        pollGenerationCompletion();
        return true;
    }

    async function resumeRegenerationInProgress(regeneration) {
        const idx = Number(regeneration && regeneration.index);
        if (!idx) return false;
        const requestId = ++regenerateSeq;
        state.generating = false;
        state.regenerationRequests[idx] = { requestId: requestId, canceled: false, restored: true };
        rememberPendingRegeneration(idx);
        showShell();
        const restored = await restoreCurrentCardsFromHistory();
        if (!regenerationRequest(idx) || regenerationRequest(idx).requestId !== requestId) return true;
        if (restored) {
            state.focusIndex = idx;
            render();
            renderSidebar();
        } else {
            applyLayout();
            setWelcomeError('Regenerating Hypothesis ' + idx + '...');
        }
        pollRegenerationCompletion(requestId, idx);
        return true;
    }

    function pollRegenerationCompletion(requestId, index) {
        window.setTimeout(async () => {
            const active = regenerationRequest(index);
            if (!active || active.requestId !== requestId) return;
            const status = await generationStatus();
            const regen = status && status.regeneration;
            if (!status || status.unknown ||
                    (regen && regen.in_progress && Number(regen.index) === Number(index))) {
                pollRegenerationCompletion(requestId, index);
                return;
            }
            delete state.regenerationRequests[index];
            clearPendingRegeneration(index);
            initialize({ skipGenerationStatus: true, skipLocalSnapshot: true });
        }, 2500);
    }

    function pollGenerationCompletion() {
        window.setTimeout(async () => {
            const status = await generationStatus();
            if (status && status.cancel_requested) {
                stopActivity();
                state.generating = false;
                state.cancelRequested = false;
                setBusy(false);
                applyLayout();
                return;
            }
            if (!status || status.unknown || status.in_progress) {
                if (status && !status.unknown && els.activityError) {
                    els.activityError.textContent = '';
                    els.activityError.hidden = true;
                }
                pollGenerationCompletion();
                return;
            }
            stopActivity();
            state.generating = false;
            setBusy(false);
            initialize({ skipGenerationStatus: true, skipLocalSnapshot: true });
        }, 2500);
    }

    // ---- generation (welcome "Initialize workspace") ------------------

    function startActivity() {
        state.cancelRequested = false;
        els.activityLabel.textContent = 'Generating hypotheses... This may take a few minutes.';
        if (els.activityError) { els.activityError.textContent = ''; els.activityError.hidden = true; }
        if (els.activityCancel) { els.activityCancel.disabled = false; els.activityCancel.textContent = 'Cancel'; }
        els.activity.hidden = false;
    }
    function stopActivity() {
        els.activity.hidden = true;
        if (els.activityCancel) { els.activityCancel.disabled = false; els.activityCancel.textContent = 'Cancel'; }
    }
    async function cancelGeneration() {
        if (!state.generating || state.cancelRequested) return;
        state.cancelRequested = true;
        if (els.activityCancel) { els.activityCancel.disabled = true; els.activityCancel.textContent = 'Cancelling...'; }
        if (els.activityError) { els.activityError.textContent = ''; els.activityError.hidden = true; }
        try {
            const { resp, data } = await apiJson('/api/cancel_generation', 'POST', {});
            if (resp.ok && data && data.success !== false) {
                state.generating = false;
                state.cancelRequested = false;
                stopActivity();
                setBusy(false);
                applyLayout();
                persistStudioSnapshot();
                return;
            }
            state.cancelRequested = false;
            if (els.activityCancel) { els.activityCancel.disabled = false; els.activityCancel.textContent = 'Cancel'; }
            if (els.activityError) {
                els.activityError.textContent = (data && data.error) || 'Could not cancel generation. Please try again.';
                els.activityError.hidden = false;
            }
        } catch (err) {
            state.cancelRequested = false;
            if (els.activityCancel) { els.activityCancel.disabled = false; els.activityCancel.textContent = 'Cancel'; }
            if (els.activityError) {
                els.activityError.textContent = 'Network error while cancelling. Please try again.';
                els.activityError.hidden = false;
            }
        }
    }

    async function generate() {
        diagnostics.generateCalls += 1;
        if (CFG.initialExploration) { hsDebug('generate skipped: initial_exploration true'); return; }
        if (state.generating) { hsDebug('generate skipped: already generating'); return; }
        const guidance = (els.welcomeGuidance.value || '').trim();
        const payload = {
            num_hypotheses: state.count,
            guidance: guidance || undefined,
        };
        diagnostics.lastPayload = payload;
        hsDebug('generate called', payload);
        try {
            setWelcomeError(null);
            setComposerError(null);
            state.generating = true;
            setBusy(true);
            applyLayout();
            startActivity();
            persistStudioSnapshot();
            diagnostics.lastFetchStartedAt = new Date().toISOString();
            hsDebug('fetch /api/generate', payload);
            const { resp, data } = await apiJson('/api/generate', 'POST', payload);
            stopActivity();
            state.generating = false;
            if (resp.ok && data && data.success && Array.isArray(data.hypotheses)) {
                const generatedEvents = [{
                    kind: 'hypotheses', id: data.result_event_id || nextEventId(),
                    hypotheses: data.hypotheses,
                }];
                const generationId = data.generation_id || null;
                if (isViewingLiveRound()) {
                    state.events = generatedEvents;
                    state.viewedRegenerationPositions = {};
                    state.generationId = generationId;
                    state.focusIndex = null;
                    state.expanded = {};
                    state.manualEditIndex = null;
                    state.manualEditDraft = '';
                    render();
                    await loadHistory();
                    markViewedRoundSeen();
                } else {
                    if (liveStash) {
                        liveStash.events = generatedEvents;
                        liveStash.viewedRegenerationPositions = {};
                        liveStash.generationId = generationId;
                        liveStash.focusIndex = null;
                        liveStash.expanded = {};
                    }
                    await loadHistory();
                    renderSidebar();
                    if (window.HypothesisActivity && window.HypothesisActivity.refresh) window.HypothesisActivity.refresh();
                }
            } else {
                applyLayout();
                if (data && data.cancelled) {
                    if (isViewingLiveRound()) setWelcomeError('Generation cancelled.');
                } else {
                    setWelcomeError((data && (data.message || data.error)) || 'Could not generate hypotheses. Check the server logs and try again.');
                }
            }
        } catch (err) {
            hsDebugError('generate failed before completion', err);
            stopActivity();
            state.generating = false;
            applyLayout();
            if (isViewingLiveRound()) setWelcomeError('Could not generate hypotheses. Check the server logs and try again.');
        } finally {
            setBusy(false);
            persistStudioSnapshot();
        }
    }

    // ---- focused-hypothesis chat submit --------------------------------------

    async function onSubmit(e) {
        if (e) e.preventDefault();
        if (isFocusedThreadPending() && !els.input.value.trim()) {
            cancelActiveThreadResponse();
            return;
        }
        await submitThreadMessage();
    }

    function isFocusedThreadPending() {
        return state.focusIndex != null && isViewedThreadPending(state.focusIndex);
    }

    function cancelActiveThreadResponse() {
        stopActiveThreadRequest();
    }

    async function submitThreadMessage() {
        if (state.readOnly || state.focusIndex == null) return;
        const idx = state.focusIndex;
        const message = els.input.value.trim();
        const pendingHere = isViewedThreadPending(idx);
        if (!message) return;
        const focusedMutationInProgress = state.generating ||
            isRegeneratingHypothesis(idx) ||
            (state.busyIndex === idx && state.busyKind !== null);
        if (focusedMutationInProgress && !pendingHere) return;
        setComposerError(null);
        els.input.value = '';
        autoGrow();
        // A follow-up queues only behind work in this hypothesis context. Chat
        // requests for other hypotheses are independent and are submitted now.
        // Keep edits serialized because they mutate the shared card set.
        if (pendingHere || state.pausedThreadQueues[idx] ||
                (state.composerMode === 'edit' && (hasAnyPendingThread() || hasAnyRegeneration()))) {
            queueThreadMessage(idx, message, state.composerMode);
            renderQueuedMessages();
            refreshSendEnabled();
            persistStudioSnapshot();
            els.input.focus();
            return;
        }
        if (state.composerMode === 'edit') await sendRevise(message, idx);
        else await sendDiscuss(message, idx);
    }

    function beginThreadRequest(idx, kind, message, resultEventId) {
        const requestId = ++activeThreadRequestSeq;
        const clientRequestId = makeClientRequestId();
        const controller = window.AbortController ? new AbortController() : null;
        const active = {
            requestId: requestId,
            clientRequestId: clientRequestId,
            controller: controller,
            stopped: false,
        };
        activeThreadRequests[idx] = active;
        state.pendingThreadRequests[idx] = {
            kind: kind,
            resultEventId: resultEventId || null,
        };
        rememberPendingThread(kind, idx, message, clientRequestId, resultEventId);
        refreshSendEnabled();
        return {
            requestId: requestId,
            clientRequestId: clientRequestId,
            signal: controller ? controller.signal : undefined,
            active: active,
        };
    }

    function finishThreadRequest(idx, requestId) {
        const active = activeThreadRequests[idx];
        if (!active || requestId !== active.requestId) return false;
        delete state.pendingThreadRequests[idx];
        delete activeThreadRequests[idx];
        clearPendingThread(idx);
        refreshSendEnabled();
        return true;
    }

    function stopActiveThreadRequest() {
        const idx = Number(state.focusIndex);
        if (!idx || !hasPendingThread(idx)) return;
        const activeRequest = activeThreadRequests[idx];
        if (activeRequest) {
            activeRequest.stopped = true;
            if (activeRequest.controller) activeRequest.controller.abort();
        }
        const requestId = activeRequest && activeRequest.clientRequestId;
        const pending = readPendingThread(idx);
        const active = activeMutationHypothesis({ index: idx });
        const view = regenerationViewFor(active);
        state.events.push({
            kind: 'message', role: 'assistant', content: 'Response stopped.', hypothesisIndex: idx,
            resultEventId: (pending && pending.resultEventId) || view.viewedResultEventId,
        });
        if (requestId) {
            apiJson('/api/cancel_hypothesis_thread', 'POST', { request_id: requestId, index: idx })
                .catch(() => { /* best-effort server cancellation */ });
        }
        delete state.pendingThreadRequests[idx];
        delete activeThreadRequests[idx];
        if (state.busyIndex === idx) state.busyIndex = null;
        if (state.busyKind === 'revise' && state.busyIndex == null) state.busyKind = null;
        clearPendingThread(idx);
        renderCards();
        renderThread();
        applyComposer();
        if (getQueuedThreadMessages(idx).length) state.pausedThreadQueues[idx] = true;
        renderQueuedMessages();
        persistStudioSnapshot();
    }

    function nextQueuedThreadIndex(preferredIndex) {
        const preferred = Number(preferredIndex);
        const preferredFirst = preferred ? getQueuedThreadMessages(preferred)[0] : null;
        if (preferred && preferredFirst && !hasPendingThread(preferred) &&
                !(preferredFirst.mode === 'edit' && hasAnyPendingThread()) &&
                !state.pausedThreadQueues[preferred]) return preferred;
        let bestIndex = null;
        let bestCreatedAt = Infinity;
        Object.keys(state.queuedThreadMessages || {}).forEach((key) => {
            const idx = Number(key);
            const first = getQueuedThreadMessages(idx)[0];
            if (!idx || !first || hasPendingThread(idx) || state.pausedThreadQueues[idx] ||
                    (first.mode === 'edit' && hasAnyPendingThread())) return;
            const createdAt = Number(first.createdAt) || 0;
            if (createdAt < bestCreatedAt) {
                bestCreatedAt = createdAt;
                bestIndex = idx;
            }
        });
        return bestIndex;
    }

    function promoteNextQueuedThreadMessage(index) {
        if (hasPendingThread(index)) return false;
        if (state.pausedThreadQueues[Number(index)]) {
            renderQueuedMessages();
            persistStudioSnapshot();
            return false;
        }
        if (isRegeneratingHypothesis(index)) { renderQueuedMessages(); persistStudioSnapshot(); return false; }
        const idx = nextQueuedThreadIndex(index);
        if (!idx) { renderQueuedMessages(); persistStudioSnapshot(); return false; }
        const next = shiftQueuedThreadMessage(idx);
        if (!next) { renderQueuedMessages(); persistStudioSnapshot(); return false; }
        if (next.mode === 'edit') sendRevise(next.text, idx);
        else sendDiscuss(next.text, idx);
        return true;
    }

    async function sendDiscuss(message, forcedIndex) {
        const idx = Number(forcedIndex || state.focusIndex);
        if (!idx) return;
        const active = activeMutationHypothesis({ index: idx });
        const versionView = regenerationViewFor(active);
        if (!versionView.isViewingCurrent) {
            setComposerError('Edit this historical result before continuing its discussion.');
            return;
        }
        const started = beginThreadRequest(idx, 'discuss', message, versionView.viewedResultEventId);
        state.events.push({
            kind: 'message', role: 'user', content: message, hypothesisIndex: idx,
            resultEventId: versionView.viewedResultEventId,
        });
        renderThread();
        applyComposer();
        try {
            const { resp, data } = await apiJson('/api/chat', 'POST', {
                message: message, mode: 'hypothesis', hypothesis_index: idx,
                request_id: started.clientRequestId,
            }, started.signal);
            if (started.active.stopped || activeThreadRequests[idx] !== started.active) return;
            finishThreadRequest(idx, started.requestId);
            if (resp.ok && data && data.success && data.reply) {
                state.events.push({
                    kind: 'message', role: 'assistant', content: data.reply, hypothesisIndex: idx,
                    resultEventId: versionView.viewedResultEventId,
                });
            } else {
                state.events.push({
                    kind: 'message', role: 'assistant',
                    content: (data && data.error) || 'DiscoveryAgent could not answer that. Please try again.',
                    hypothesisIndex: idx, resultEventId: versionView.viewedResultEventId,
                });
            }
            renderThread();
            applyComposer();
            promoteNextQueuedThreadMessage(idx);
        } catch (err) {
            if (pageUnloading) return;
            if (started.active.stopped || (err && err.name === 'AbortError')) return;
            finishThreadRequest(idx, started.requestId);
            state.events.push({
                kind: 'message', role: 'assistant',
                content: 'Network error reaching DiscoveryAgent. Please try again.',
                hypothesisIndex: idx, resultEventId: versionView.viewedResultEventId,
            });
            renderThread();
            applyComposer();
            promoteNextQueuedThreadMessage(idx);
        }
    }

    async function sendRevise(request, forcedIndex) {
        const idx = Number(forcedIndex || state.focusIndex);
        const h = activeMutationHypothesis({ index: idx });
        if (!h) { setComposerError('That hypothesis is no longer available.'); return; }
        const versionView = regenerationViewFor(h);
        const editingHistorical = !versionView.isViewingCurrent;
        const viewedText = versionView.viewedSnapshot && (
            versionView.viewedSnapshot.full_text ||
            versionView.viewedSnapshot.formatted_output ||
            hypothesisMarkdown(versionView.viewedSnapshot)
        );
        const started = beginThreadRequest(idx, 'revise', request, versionView.viewedResultEventId);
        state.busyIndex = idx;
        state.busyKind = 'revise';
        // The revise request appears in the thread as a normal scientist turn
        // (the backend also persists it just ahead of the revision).
        const optimisticRequestEvent = {
            kind: 'message', role: 'user', content: request, hypothesisIndex: idx,
            resultEventId: versionView.viewedResultEventId,
        };
        state.events.push(optimisticRequestEvent);
        renderCards();
        renderThread();
        applyComposer();
        try {
            const payload = {
                index: idx, state_id: h.state_id, feedback: request,
                request_id: started.clientRequestId,
            };
            if (editingHistorical) {
                payload.action = 'edit_regeneration_version';
                payload.regeneration_position = versionView.viewedPosition;
                payload.regeneration_result_id = versionView.viewedResultEventId;
            }
            const { resp, data } = await apiJson('/api/edit', 'POST', payload, started.signal);
            if (started.active.stopped || activeThreadRequests[idx] !== started.active) return;
            finishThreadRequest(idx, started.requestId);
            state.busyIndex = null;
            state.busyKind = null;
            if (resp.ok && data && data.success && data.hypothesis) {
                state.manualEditIndex = null;
                state.manualEditDraft = '';
                state.events.push({
                    kind: 'revision', id: nextEventId(), index: idx,
                    source: editingHistorical ? 'historical-edit' : 'revised',
                    regenerationPosition: editingHistorical ? versionView.viewedPosition : undefined,
                    sourceResultEventId: editingHistorical ? versionView.viewedResultEventId : undefined,
                    resultEventId: versionView.viewedResultEventId,
                    hypothesis: data.hypothesis,
                });
                if (editingHistorical) state.viewedRegenerationPositions[idx] = versionView.viewedPosition;
                render();
            } else {
                // Drop the optimistic request turn on failure.
                const optimisticPosition = state.events.indexOf(optimisticRequestEvent);
                if (optimisticPosition >= 0) state.events.splice(optimisticPosition, 1);
                setComposerError((data && data.error) || 'Could not revise that hypothesis.');
                renderCards();
                renderThread();
                applyComposer();
            }
            promoteNextQueuedThreadMessage(idx);
        } catch (err) {
            if (pageUnloading) return;
            if (started.active.stopped || (err && err.name === 'AbortError')) return;
            finishThreadRequest(idx, started.requestId);
            state.busyIndex = null;
            if (state.busyKind === 'revise') state.busyKind = null;
            const optimisticPosition = state.events.indexOf(optimisticRequestEvent);
            if (optimisticPosition >= 0) state.events.splice(optimisticPosition, 1);
            setComposerError('Network error while revising.');
            renderCards();
            renderThread();
            applyComposer();
            promoteNextQueuedThreadMessage(idx);
        }
    }

    // ---- regenerate / manual edit / remove -----------------------------------

    function activeMutationHypothesis(h) {
        const idx = Number(h && h.index);
        if (!idx) return h;
        const active = HSE.activeHypothesis(state.events, idx) || h;
        return active && active.index == null ? Object.assign({}, active, { index: idx }) : active;
    }

    async function runRegenerate(h, request) {
        h = activeMutationHypothesis(h);
        const idx = Number(h && h.index);
        const hasActiveOperation = !idx || state.generating ||
            state.busyIndex != null ||
            isRegeneratingHypothesis(idx) ||
            hasPendingThread(idx);
        if (hasActiveOperation) return;
        clearCanceledRegeneration(idx);
        const requestId = ++regenerateSeq;
        const activeRequest = { requestId: requestId, canceled: false, restored: false };
        state.regenerationRequests[idx] = activeRequest;
        rememberPendingRegeneration(idx);
        renderCards();
        try {
            const payload = { index: idx, request_hypothesis: hypothesisMarkdown(h) || h.full_text || h.hypothesis || '' };
            if (state.generationId != null) payload.generation_id = state.generationId;
            if (request) payload.feedback = request;
            const { resp, data } = await apiJson('/api/regenerate_guidance', 'POST', payload);
            if (regenerationRequest(idx) !== activeRequest || activeRequest.canceled) return;
            delete state.regenerationRequests[idx];
            clearPendingRegeneration(idx);
            if (resp.ok && data && data.success && data.hypothesis) {
                state.events.push({
                    kind: 'revision', id: data.result_event_id || nextEventId(), index: idx,
                    source: 'regenerated', before: h, hypothesis: data.hypothesis,
                });
                delete state.viewedRegenerationPositions[idx];
                if (Number(state.focusIndex) === idx) {
                    render();
                } else {
                    // A background card changed, but the focused transcript did
                    // not. Repaint only the cards so the reader's chat scroll
                    // position and selection remain untouched.
                    renderCards();
                }
            } else {
                setComposerError((data && data.error) || 'Could not regenerate that hypothesis.');
                renderCards();
            }
        } catch (err) {
            if (regenerationRequest(idx) !== activeRequest || activeRequest.canceled) return;
            delete state.regenerationRequests[idx];
            clearPendingRegeneration(idx);
            setComposerError('Network error while regenerating.');
            renderCards();
        } finally {
            if (!pageUnloading && regenerationRequest(idx) === activeRequest) {
                delete state.regenerationRequests[idx];
                clearPendingRegeneration(idx);
                renderCards();
            }
        }
    }

    function cancelRegenerate(idx) {
        const activeRequest = regenerationRequest(idx);
        if (!activeRequest) return;
        activeRequest.canceled = true;
        rememberCanceledRegeneration(idx);
        apiJson('/api/cancel_regeneration', 'POST', { index: idx })
            .catch(() => { /* best-effort server cancellation */ });
        clearPendingRegeneration(idx);
        delete state.regenerationRequests[idx];
        renderCards();
        persistStudioSnapshot();
    }
    function openManualEdit(h, regenerationPosition) {
        const idx = Number(h.index);
        if (Number.isInteger(Number(regenerationPosition))) {
            state.viewedRegenerationPositions[idx] = Number(regenerationPosition);
        }
        state.focusIndex = idx;
        state.expanded = { [idx]: true };
        state.manualEditIndex = idx;
        state.manualEditDraft = hypothesisBodyMarkdown(h);
        rememberStudioView();
        renderCards();
        renderThread();
        applyComposer();
        requestAnimationFrame(() => {
            const ta = els.cards.querySelector('.hs-manual-edit-input[data-index="' + idx + '"]');
            growTextAreaNode(ta);
            if (ta) {
                try { ta.focus({ preventScroll: true }); } catch (err) { ta.focus(); }
                ta.setSelectionRange(0, 0);
                ta.scrollTop = 0;
            }
        });
    }

    async function runManualEdit(h, text) {
        if (hasAnyRegeneration() || hasAnyPendingThread()) {
            setComposerError('Wait for the active hypothesis response to finish before saving an edit.');
            return;
        }
        h = activeMutationHypothesis(h);
        const idx = Number(h.index);
        const versionView = regenerationViewFor(h);
        const editingHistorical = !versionView.isViewingCurrent;
        setBusy(true);
        state.busyIndex = idx;
        renderCards();
        try {
            const payload = { index: idx, state_id: h.state_id, manual_hypothesis: text };
            if (editingHistorical) {
                payload.action = 'edit_regeneration_version';
                payload.regeneration_position = versionView.viewedPosition;
                payload.regeneration_result_id = versionView.viewedResultEventId;
            }
            const { resp, data } = await apiJson('/api/edit', 'POST', payload);
            state.busyIndex = null;
            if (resp.ok && data && data.success && data.hypothesis) {
                state.manualEditIndex = null;
                state.manualEditDraft = '';
                state.events.push({
                    kind: 'revision', id: nextEventId(), index: idx,
                    source: editingHistorical ? 'historical-edit' : 'revised',
                    regenerationPosition: editingHistorical ? versionView.viewedPosition : undefined,
                    sourceResultEventId: editingHistorical ? versionView.viewedResultEventId : undefined,
                    resultEventId: versionView.viewedResultEventId,
                    hypothesis: data.hypothesis,
                });
                if (editingHistorical) state.viewedRegenerationPositions[idx] = versionView.viewedPosition;
                render();
            } else {
                setComposerError((data && data.error) || 'Could not save your edit.');
                renderCards();
            }
        } catch (err) {
            state.busyIndex = null;
            setComposerError('Network error while saving your edit.');
            renderCards();
        } finally {
            setBusy(false);
        }
    }

    async function runRemove(h) {
        if (state.busy) return;
        if (hasAnyRegeneration()) {
            setComposerError('Wait for hypothesis regeneration to finish before removing a hypothesis.');
            return;
        }
        if (hasAnyPendingThread()) {
            setComposerError('Wait for the active hypothesis response to finish before removing a hypothesis.');
            return;
        }
        h = activeMutationHypothesis(h);
        const idx = Number(h.index);
        setBusy(true);
        state.busyIndex = idx;
        renderCards();
        try {
            const { resp, data } = await apiJson('/api/remove_hypothesis', 'POST', { index: idx });
            state.busyIndex = null;
            if (resp.ok && data && data.success && Array.isArray(data.hypotheses)) {
                state.events.push({
                    kind: 'removal', id: data.result_event_id || nextEventId(),
                    index: idx, hypotheses: data.hypotheses,
                });
                state.generationId = data.generation_id != null ? data.generation_id : state.generationId;
                state.focusIndex = null;
                state.expanded = {};
                state.manualEditIndex = null;
                state.manualEditDraft = '';
                state.viewedRegenerationPositions = {};
                if (!data.hypotheses.length) returnToGenerationWelcome();
                rememberStudioView();
                render();
                loadHistory();
            } else {
                setComposerError((data && data.error) || 'Could not remove that hypothesis.');
                renderCards();
            }
        } catch (err) {
            state.busyIndex = null;
            setComposerError('Network error while removing.');
            renderCards();
        } finally {
            setBusy(false);
        }
    }

    function returnToGenerationWelcome() {
        // The removal is already durable on the server. Clear the live
        // workspace projection so the page is exactly the original generation
        // state and no local card/thread snapshot can reappear.
        state.events = [];
        state.generationId = null;
        state.focusIndex = null;
        state.expanded = {};
        state.manualEditIndex = null;
        state.manualEditDraft = '';
        state.viewedRegenerationPositions = {};
        state.queuedThreadMessages = {};
        state.pausedThreadQueues = {};
        state.composerMode = 'chat';
        closeAllMenus();
        setComposerError(null);
        setWelcomeError(null);
        if (els.welcomeGuidance) els.welcomeGuidance.value = '';
        clearStudioSnapshot();
    }

    // Shared compact editor mounted under a card (guided regenerate).
    function openInlineEditor(h, opts) {
        const card = els.cards.querySelector('.hs-card[data-index="' + h.index + '"]');
        if (!card || card.querySelector('.hs-inline-editor')) return;
        const box = el('div', 'hs-inline-editor');
        box.appendChild(el('p', 'hs-inline-heading', esc(opts.heading)));
        const ta = el('textarea', 'hs-inline-input');
        if (opts.value) ta.value = opts.value;
        if (opts.placeholder) ta.placeholder = opts.placeholder;
        box.appendChild(ta);
        const bar = el('div', 'hs-inline-actions');
        const cancel = el('button', 'v2-btn v2-btn-secondary', 'Cancel'); cancel.type = 'button';
        const submit = el('button', 'v2-btn v2-btn-primary', opts.submitLabel); submit.type = 'button';
        cancel.addEventListener('click', () => box.remove());
        const go = () => {
            const text = ta.value.trim();
            if (opts.requireText && !text) return;
            box.remove();
            opts.onSubmit(text);
        };
        submit.addEventListener('click', go);
        ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); go(); } });
        bar.appendChild(cancel); bar.appendChild(submit);
        box.appendChild(bar);
        card.appendChild(box);
        ta.focus();
    }

    // ---- final review + selection --------------------------------------------

    function openReview(h, activeHypothesis, regenerationPosition) {
        if (hasAnyRegeneration() || hasAnyPendingThread()) return;
        activeHypothesis = activeMutationHypothesis(activeHypothesis || h);
        const versionView = regenerationViewFor(activeHypothesis || h);
        state.reviewTarget = {
            hypothesis: h,
            activeStateId: activeHypothesis && activeHypothesis.state_id,
            regenerationPosition: Number(regenerationPosition),
            regenerationResultId: versionView.viewedResultEventId,
        };
        els.reviewHypothesis.innerHTML = renderMarkdown(h.hypothesis || h.full_text || '');
        if (h.justification) {
            els.reviewJustification.innerHTML = renderMarkdown(h.justification);
            els.reviewJustBlock.hidden = false;
        } else {
            els.reviewJustBlock.hidden = true;
        }
        els.reviewError.hidden = true;
        els.review.hidden = false;
        document.body.classList.add('v2-modal-open');
        els.reviewConfirm.focus();
    }
    function closeReview() {
        els.review.hidden = true;
        document.body.classList.remove('v2-modal-open');
        state.reviewTarget = null;
    }
    async function confirmReview() {
        const target = state.reviewTarget;
        if (!target || !target.hypothesis || hasAnyRegeneration() || hasAnyPendingThread()) return;
        const h = target.hypothesis;
        els.reviewConfirm.disabled = true;
        els.reviewConfirm.textContent = 'Starting…';
        els.reviewError.hidden = true;
        try {
            const { resp, data } = await apiJson('/api/select', 'POST', {
                selection: h.index,
                // Historical previews finalize their exact displayed text;
                // arrow navigation itself remains read-only.
                selection_state_id: target.activeStateId,
                final_text: h.full_text || h.hypothesis,
                regeneration_result_id: target.regenerationResultId,
            });
            if (resp.ok && data && data.success) {
                closeReview();
                state.events.push({ kind: 'selection', id: nextEventId(), hypothesis: h });
                state.readOnly = true;
                render();
                setTimeout(() => { window.location.href = '/projects/' + encodeURIComponent(CFG.projectSlug); }, 650);
            } else {
                els.reviewConfirm.disabled = false;
                els.reviewConfirm.innerHTML = 'Use for next round <span class="v2-arrow" aria-hidden="true">&rarr;</span>';
                els.reviewError.textContent = (data && data.error) || 'Could not start the round. Please try again.';
                els.reviewError.hidden = false;
            }
        } catch (err) {
            els.reviewConfirm.disabled = false;
            els.reviewConfirm.innerHTML = 'Use for next round <span class="v2-arrow" aria-hidden="true">&rarr;</span>';
            els.reviewError.textContent = 'Network error. Please try again.';
            els.reviewError.hidden = false;
        }
    }

    // ---- shell visibility ----------------------------------------------------

    function showLoading() { els.loading.hidden = false; els.error.hidden = true; els.shell.hidden = true; }
    function showError(msg) { els.loading.hidden = true; els.shell.hidden = true; els.errorDetail.textContent = msg || ''; els.error.hidden = false; }
    function showShell() { els.loading.hidden = true; els.error.hidden = true; els.shell.hidden = false; }
    function setBusy(b) {
        state.busy = b;
        syncWelcomeGenerateButton();
        refreshSendEnabled();
    }
    function syncWelcomeGenerateButton() {
        if (!els.init) return;
        // Keep the native button enabled whenever it is visible; generate() owns
        // duplicate-click protection, so the browser never strands the CTA in a
        // disabled state after an interrupted busy transition.
        els.init.disabled = false;
        els.init.classList.toggle('is-busy', !!state.generating);
        els.init.setAttribute('aria-busy', state.generating ? 'true' : 'false');
    }
    function refreshSendEnabled() {
        const focused = state.focusIndex != null;
        const pendingHere = focused && isViewedThreadPending(state.focusIndex);
        const hasMessage = !!els.input.value.trim();
        const focusedMutationInProgress = state.generating ||
            (focused && isRegeneratingHypothesis(state.focusIndex)) ||
            (focused && state.busyIndex === state.focusIndex && state.busyKind !== null);
        if (pendingHere && !hasMessage) {
            els.send.disabled = false;
            els.send.classList.add('is-stop');
            els.send.setAttribute('aria-label', 'Cancel response');
            els.send.innerHTML = '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false"><rect x="5.25" y="5.25" width="9.5" height="9.5" rx="1.8" fill="currentColor"/></svg>';
            return;
        }
        els.send.classList.remove('is-stop');
        els.send.setAttribute('aria-label', pendingHere ? 'Queue message' : 'Send message');
        els.send.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 19V5M12 5l-6 6M12 5l6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        els.send.disabled = state.readOnly || !focused || !hasMessage ||
            (focusedMutationInProgress && !pendingHere);
    }

    // ---- wiring --------------------------------------------------------------

    function wire() {
        hsDebug('init start', { config: CFG, buttonFound: !!els.init });
        if (!els.init) {
            hsDebugError('init button missing');
            showError('Hypothesis Studio controls did not load. Please refresh and try again.');
            return;
        }
        els.init.addEventListener('click', (e) => {
            e.preventDefault();
            diagnostics.clicks += 1;
            hsDebug('init button clicked', { count: state.count, disabled: els.init.disabled });
            generate().catch((err) => {
                hsDebugError('init button handler failed', err);
                setWelcomeError('Could not generate hypotheses. Check the server logs and try again.');
                state.generating = false;
                setBusy(false);
                applyLayout();
            });
        });
        diagnostics.buttonBound = true;
        hsDebug('init button bound', els.init);
        if (els.activityCancel) els.activityCancel.addEventListener('click', cancelGeneration);
        if (els.modeTrigger) els.modeTrigger.addEventListener('click', (e) => {
            e.stopPropagation();
            if (els.modeMenu.hidden) openModeMenu(); else closeModeMenu();
        });
        if (els.modeToggle) els.modeToggle.addEventListener('click', (e) => e.stopPropagation());
        if (els.modeChat) els.modeChat.addEventListener('click', () => setComposerMode('chat'));
        if (els.modeEdit) els.modeEdit.addEventListener('click', () => setComposerMode('edit'));
        if (els.cards) els.cards.addEventListener('wheel', blockStaticHypothesisSurfaceWheel, { passive: false, capture: true });
        els.composer.addEventListener('submit', onSubmit);
        const syncComposerInput = () => { autoGrow(); refreshSendEnabled(); };
        els.input.addEventListener('input', syncComposerInput);
        els.input.addEventListener('change', syncComposerInput);
        els.input.addEventListener('keyup', refreshSendEnabled);
        els.input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitThreadMessage(); }
        });
        els.countTrigger.addEventListener('click', (e) => {
            e.stopPropagation();
            if (els.countList.hidden) openCountMenu(); else closeCountMenu();
        });
        els.returnCurrent.addEventListener('click', enterLiveRound);
        if (els.retry) els.retry.addEventListener('click', initialize);

        els.reviewBack.addEventListener('click', closeReview);
        els.reviewConfirm.addEventListener('click', confirmReview);
        els.review.addEventListener('mousedown', (e) => { if (e.target === els.review) closeReview(); });
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (!els.review.hidden) { closeReview(); return; }
            closeCountMenu();
            closeModeMenu();
            closeAllMenus();
        });
        document.addEventListener('click', () => { closeCountMenu(); closeModeMenu(); closeAllMenus(); });
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (CFG.initialExploration) {
            hsDebug('initial_exploration true: studio controls not bound');
            // Before the first exploratory round has ever started, the blocking
            // "Start your first round" overlay owns the screen. Keep the
            // generation welcome hidden and leave the inert shell noninteractive.
            const loading = document.getElementById('hs-loading');
            const shell = document.getElementById('hs-shell');
            const welcome = document.getElementById('hs-welcome');
            const workspaceMain = document.getElementById('hs-workspace-main');
            if (loading) loading.hidden = true;
            if (shell) shell.hidden = false;
            if (welcome) welcome.hidden = true;
            if (workspaceMain) workspaceMain.hidden = true;
            return;
        }
        buildCountList();
        wire();
        setCount(state.count);
        refreshSendEnabled();
        if (CFG.runningRoundIndex != null) initializeRunningRoundHistory();
        else initialize();
    });
})();
