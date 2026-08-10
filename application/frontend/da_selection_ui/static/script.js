// Wrap fetch calls to enforce redirect on unauthorized responses
(function enforceAuthFetch() {
    if (window.__authFetchWrapped) return;
    const nativeFetch = window.fetch;
    window.fetch = async (...args) => {
        const response = await nativeFetch(...args);
        if (response.status === 401) {
            console.error('Received 401 Unauthorized response, redirecting to login. URL:', args[0]);
            // Small delay to allow console log to be seen
            setTimeout(() => {
                window.location.href = '/';
            }, 100);
        }
        return response;
    };
    window.__authFetchWrapped = true;
})();

// Client-side event logging
function truncateLogValue(value, maxLength = 200) {
    if (typeof value !== 'string') return value;
    if (value.length <= maxLength) return value;
    return value.slice(0, maxLength) + '…';
}

function buildElementPayload(element) {
    if (!element) return {};
    const tag = element.tagName ? element.tagName.toLowerCase() : null;
    const payload = {
        tag,
        id: element.id || null,
        class: typeof element.className === 'string' ? element.className : null,
        name: element.name || null,
        type: element.type || null,
        role: element.getAttribute ? element.getAttribute('role') : null,
        href: element.href || null,
        action: element.getAttribute ? element.getAttribute('data-log-action') : null,
        label: element.getAttribute ? element.getAttribute('data-log-label') : null,
    };
    if (tag && tag !== 'input' && tag !== 'textarea') {
        payload.text = truncateLogValue((element.innerText || '').trim(), 120);
    } else if (element.type === 'password') {
        payload.text = '[redacted]';
    }
    return payload;
}

function sendLogEvent(eventType, payload = {}, useBeacon = false) {
    if (window.__disableClientLogging) return;
    const body = JSON.stringify({ event_type: eventType, payload });
    if (useBeacon && navigator.sendBeacon) {
        const blob = new Blob([body], { type: 'application/json' });
        navigator.sendBeacon('/api/log_event', blob);
        return;
    }
    fetch('/api/log_event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
        credentials: 'include',
    }).catch(() => {});
}

function sendCleanupBeacon() {
    if (!navigator.sendBeacon) return;
    const blob = new Blob([JSON.stringify({})], { type: 'application/json' });
    navigator.sendBeacon('/api/cleanup', blob);
}

function isCleanupArmed() {
    try {
        return localStorage.getItem('da_cleanup_armed') === 'true';
    } catch (err) {
        return false;
    }
}

function setCleanupArmed(enabled) {
    try {
        localStorage.setItem('da_cleanup_armed', enabled ? 'true' : 'false');
    } catch (err) {
        // Ignore storage errors (private mode, disabled storage, etc.)
    }
}

function logEvent(eventType, payload = {}) {
    sendLogEvent(eventType, payload);
}

const HYPOTHESIS_LANDING_HASH = '#hypotheses';
const HYPOTHESIS_LANDING_KEY = 'da_hypotheses_ready';
const HISTORY_GUARD_MARKER = 'da_hypothesis_v2';

function buildHistoryGuardState(view, extras = {}) {
    return {
        __da_guard: HISTORY_GUARD_MARKER,
        view,
        ...extras
    };
}

function isGuardedHistoryState(state) {
    return !!(state && state.__da_guard === HISTORY_GUARD_MARKER);
}

function markHypothesesLandingReady() {
    try {
        sessionStorage.setItem(HYPOTHESIS_LANDING_KEY, '1');
    } catch (err) {
        // Ignore storage errors.
    }
    const url = new URL(window.location.href);
    url.hash = HYPOTHESIS_LANDING_HASH;
    history.replaceState(buildHistoryGuardState('hypotheses'), '', url);
}

function shouldForceHypothesesLanding() {
    try {
        return sessionStorage.getItem(HYPOTHESIS_LANDING_KEY) === '1';
    } catch (err) {
        return false;
    }
}

function showHypothesesLanding() {
    if (editMode) {
        exitEditMode();
    }
    const hypothesesSection = document.getElementById('hypotheses-section');
    if (hypothesesSection && currentHypotheses.length > 0) {
        hypothesesSection.classList.remove('hidden');
        hypothesesSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

function pushEditHistoryState(hypothesis) {
    if (!shouldForceHypothesesLanding()) return;
    const state = history.state || {};
    if (
        isGuardedHistoryState(state)
        && state.view === 'edit'
        && state.hypothesisIndex === (hypothesis && hypothesis.index)
    ) {
        return;
    }
    const url = new URL(window.location.href);
    url.hash = '#edit';
    history.pushState(
        buildHistoryGuardState('edit', { hypothesisIndex: hypothesis && hypothesis.index }),
        '',
        url
    );
}

window.addEventListener('popstate', (event) => {
    if (!shouldForceHypothesesLanding()) return;
    if (!isGuardedHistoryState(event?.state)) return;
    const view = event?.state?.view || null;
    if (view !== 'edit' && editMode) {
        exitEditMode();
    }
    if (view === 'edit' || editMode) {
        showHypothesesLanding();
    }
    const url = new URL(window.location.href);
    url.hash = HYPOTHESIS_LANDING_HASH;
    history.replaceState(buildHistoryGuardState('hypotheses'), '', url);
});

window.addEventListener('hashchange', () => {
    if (!shouldForceHypothesesLanding()) return;
    if (!isGuardedHistoryState(history.state)) return;
    const hash = window.location.hash || '';
    if (hash !== HYPOTHESIS_LANDING_HASH && hash !== '#edit') {
        if (editMode) {
            showHypothesesLanding();
        }
        const url = new URL(window.location.href);
        url.hash = HYPOTHESIS_LANDING_HASH;
        history.replaceState(buildHistoryGuardState('hypotheses'), '', url);
    }
});

// Global state
let currentHypotheses = [];
let selectedHypothesisIndex = null;
let hasRoundReport = false;
let hasBackgroundLiterature = false;
let fullDatasetDescription = '';
let fullTaskDescription = '';
let readmeContent = '';
let readmeExpanded = false;
let githubUrl = '';
let codeChatAvailable = false;
let generatePlanEnabled = false;
let hasResearchPaper = false;
let auditOnly = false;
let expandedHypotheses = new Set(); // Track which hypotheses are expanded
let sessionInitialized = false;
let initializingSession = false;
let initPromise = null;
let pendingObjectiveContinuation = null;
let objectiveSaveInFlight = false;

// Citations state
let citationsEnabled = false;
let lastCitations = [];
let citationDocuments = [];
let citationDataMap = {}; // Global map of citation index -> citation data
let citationUsageByHypothesis = {}; // Map of citation index -> array of hypothesis indices

// Chat state
let chatMode = 'hypothesis';
let chatMessages = [];
let chatContextCache = null;
let chatInitializing = false;
let chatContextRefreshPending = false;
let chatContextRefreshPendingForce = false;
let chatPendingScrollTop = null;
let hypothesisChatPersistTimer = null;

const CHAT_STORAGE_VERSION = 1;
const HYPOTHESIS_CHAT_STORAGE_MAX_MESSAGES = 120;
const HYPOTHESIS_CHAT_STORAGE_MAX_CHARS = 250000;
const REPORT_CHAT_STORAGE_MAX_MESSAGES = 120;
const REPORT_CHAT_STORAGE_MAX_CHARS = 350000;
const chatStorageScope = (() => {
    const configured = window.serverConfig?.chatStorageScope || window.reportPageConfig?.chatStorageScope;
    if (typeof configured === 'string' && configured.trim()) {
        return configured.trim();
    }
    return 'anonymous';
})();
const hypothesisChatStorageKey = `da_hypothesis_chat_state_v${CHAT_STORAGE_VERSION}_${chatStorageScope}`;
window.__persistActiveReportChatState = null;

function normalizeReportTypeForStorage(reportType) {
    return String(reportType || '').toLowerCase() === 'round' ? 'round' : 'background';
}

function getReportChatStorageKey(reportType) {
    const normalizedType = normalizeReportTypeForStorage(reportType);
    return `da_report_chat_state_v${CHAT_STORAGE_VERSION}_${chatStorageScope}_${normalizedType}`;
}

function formatResponseTimeMs(responseTimeMs) {
    if (!Number.isFinite(responseTimeMs) || responseTimeMs < 0) {
        return null;
    }
    if (responseTimeMs >= 10000) {
        return `${(responseTimeMs / 1000).toFixed(1)}s`;
    }
    if (responseTimeMs >= 1000) {
        return `${(responseTimeMs / 1000).toFixed(2)}s`;
    }
    return `${Math.round(responseTimeMs)} ms`;
}

function normalizeStoredChatMessages(messages, maxMessages = 120, maxChars = 250000) {
    if (!Array.isArray(messages)) {
        return [];
    }
    const normalized = [];
    for (const message of messages) {
        if (!message || typeof message !== 'object') continue;
        const role = typeof message.role === 'string' ? message.role : '';
        if (!['user', 'assistant', 'system'].includes(role)) continue;
        const content = typeof message.content === 'string' ? message.content : '';
        if (!content.trim()) continue;
        const mode = message.mode === 'code'
            ? 'code'
            : (message.mode === 'edit' ? 'edit' : 'hypothesis');
        const thinking = (typeof message.thinking === 'string' && message.thinking.trim())
            ? message.thinking
            : null;
        const responseTimeMs = Number.isFinite(message.responseTimeMs)
            ? Math.max(0, Math.round(message.responseTimeMs))
            : null;
        normalized.push({ role, content, mode, thinking, responseTimeMs });
    }

    const bounded = normalized.length <= maxMessages
        ? normalized
        : normalized.slice(normalized.length - maxMessages);

    const compact = [];
    let usedChars = 0;
    for (let i = bounded.length - 1; i >= 0; i -= 1) {
        const message = bounded[i];
        const messageChars =
            message.content.length
            + (message.thinking ? message.thinking.length : 0);
        if (compact.length >= maxMessages) break;
        if (usedChars + messageChars > maxChars) break;
        compact.unshift(message);
        usedChars += messageChars;
    }
    return compact;
}

function readHypothesisChatStateFromStorage() {
    try {
        const raw = localStorage.getItem(hypothesisChatStorageKey);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return null;
        return {
            collapsed: parsed.collapsed !== false,
            splitPercent: Number.isFinite(parsed.splitPercent) ? parsed.splitPercent : 50,
            mode: parsed.mode === 'code' ? 'code' : 'hypothesis',
            scrollTop: Number.isFinite(parsed.scrollTop) ? parsed.scrollTop : null,
            updatedAt: Number.isFinite(parsed.updatedAt) ? parsed.updatedAt : null,
            messages: normalizeStoredChatMessages(
                parsed.messages,
                HYPOTHESIS_CHAT_STORAGE_MAX_MESSAGES,
                HYPOTHESIS_CHAT_STORAGE_MAX_CHARS
            )
        };
    } catch (error) {
        console.warn('[chat] failed to read hypothesis chat state', error);
        try {
            localStorage.removeItem(hypothesisChatStorageKey);
        } catch (removeError) {
            // Ignore cleanup errors.
        }
        return null;
    }
}

const HISTORY_STORAGE_PREFIX = 'da_hyp_history_';
let historySessionScope = 'session';
let historyGenerationScope = 0;

function getHistoryStorageKey(index) {
    if (index === null || index === undefined) return null;
    return `${HISTORY_STORAGE_PREFIX}${historySessionScope}_${historyGenerationScope}_${index}`;
}

function updateHistoryScope({ sessionScope = null, generationId = null } = {}) {
    if (sessionScope) {
        historySessionScope = String(sessionScope);
    }
    if (Number.isInteger(generationId) && generationId >= 0) {
        historyGenerationScope = generationId;
    }
}

function normalizeHypothesisChatStorageId(index) {
    if (index === null || index === undefined) return null;
    const normalized = String(index).trim();
    if (!normalized) return null;
    return normalized.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 64);
}

function getEditHypothesisChatStoragePrefix() {
    return `da_hypothesis_edit_chat_state_v${CHAT_STORAGE_VERSION}_${chatStorageScope}_`;
}

function getEditHypothesisChatStorageKey(index) {
    const normalized = normalizeHypothesisChatStorageId(index);
    if (!normalized) return null;
    return `${getEditHypothesisChatStoragePrefix()}${historyGenerationScope}_${normalized}`;
}

function clearEditHypothesisChatState(index) {
    const key = getEditHypothesisChatStorageKey(index);
    if (!key) return;
    try {
        localStorage.removeItem(key);
    } catch (error) {
        // Ignore storage cleanup failures.
    }
}

function clearAllEditHypothesisChatStates() {
    const prefix = getEditHypothesisChatStoragePrefix();
    try {
        const keys = [];
        for (let i = 0; i < localStorage.length; i += 1) {
            const key = localStorage.key(i);
            if (typeof key === 'string' && key.startsWith(prefix)) {
                keys.push(key);
            }
        }
        keys.forEach((key) => localStorage.removeItem(key));
    } catch (error) {
        // Ignore storage cleanup failures.
    }
}

function clearAllHypothesisChatStates() {
    try {
        localStorage.removeItem(hypothesisChatStorageKey);
    } catch (error) {
        // Ignore storage cleanup failures.
    }
    clearAllEditHypothesisChatStates();
}

function readEditHypothesisChatStateFromStorage(index) {
    const key = getEditHypothesisChatStorageKey(index);
    if (!key) return null;
    try {
        const raw = localStorage.getItem(key);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return null;
        const editType = parsed.editType === 'llm_edit' ? 'llm_edit' : 'llm_ask';
        return {
            editType,
            scrollTop: Number.isFinite(parsed.scrollTop) ? parsed.scrollTop : null,
            updatedAt: Number.isFinite(parsed.updatedAt) ? parsed.updatedAt : null,
            messages: normalizeStoredChatMessages(
                parsed.messages,
                HYPOTHESIS_CHAT_STORAGE_MAX_MESSAGES,
                HYPOTHESIS_CHAT_STORAGE_MAX_CHARS
            )
        };
    } catch (error) {
        try {
            localStorage.removeItem(key);
        } catch (removeError) {
            // Ignore cleanup errors.
        }
        return null;
    }
}

function saveHistoryToStorage(index) {
    const key = getHistoryStorageKey(index);
    if (!key) return;
    const canonicalHypothesis = currentHypotheses.find((hyp) => String(hyp.index) === String(index));
    const canonicalStateId = (
        canonicalHypothesis && typeof canonicalHypothesis.state_id === 'string'
            ? canonicalHypothesis.state_id.trim()
            : ''
    ) || null;
    try {
        sessionStorage.setItem(key, JSON.stringify({
            history: hypothesisHistory,
            index: historyIndex,
            canonical_state_id: canonicalStateId
        }));
    } catch (err) {
        // Ignore storage errors (private mode, disabled storage, etc.)
    }
}

function loadHistoryFromStorage(index) {
    const key = getHistoryStorageKey(index);
    if (!key) return null;
    try {
        const raw = sessionStorage.getItem(key);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || !Array.isArray(parsed.history) || parsed.history.length === 0) {
            return null;
        }
        const safeIndex = Number.isInteger(parsed.index) ? parsed.index : parsed.history.length - 1;
        return {
            history: parsed.history,
            index: Math.min(Math.max(safeIndex, 0), parsed.history.length - 1),
            canonicalStateId: typeof parsed.canonical_state_id === 'string' ? parsed.canonical_state_id.trim() : null
        };
    } catch (err) {
        return null;
    }
}

function isStoredHistoryCompatible(stored, hypothesis) {
    if (!stored || !hypothesis) return false;
    const hypothesisStateId = typeof hypothesis.state_id === 'string' ? hypothesis.state_id.trim() : '';
    const canonicalStateId = typeof stored.canonicalStateId === 'string' ? stored.canonicalStateId.trim() : '';
    if (!hypothesisStateId || !canonicalStateId) {
        return false;
    }
    return hypothesisStateId === canonicalStateId;
}
let chatCollapsed = true;
let chatPanelMounted = false;
// Snapshot of the chat panel's open/closed state taken when entering a
// hypothesis edit session (edit mode force-opens the panel), so exitEditMode
// can restore whatever the user had before instead of always collapsing.
let chatWasOpenBeforeEdit = false;
let currentSplitPercent = 50;
let isDraggingDivider = false;
let dragStartX = 0;
let dragStartLeftWidth = 0;
let workspaceWidth = 0;
const MIN_LEFT_PERCENT = 25;
const MAX_LEFT_PERCENT = 75;
let projectSubmissionCount = 0;
let projectRound = 1;
let generationAbortController = null;
let generationInProgress = false;
let generationOrigin = 'initial';
let generationCancelRequested = false;
let loadingCancelHandler = null;
let uiStateVersion = 0;
let editSessionToken = 0;

let chatRequestToken = 0;
let chatRequestController = null;
let chatRequestInFlight = false;

let editFormRequestToken = 0;
let editFormRequestController = null;
let editFormRequestInFlight = false;

let regenerateRequestToken = 0;
let regenerateRequestController = null;
let regenerateRequestInFlight = false;

// Edit mode state
let editMode = false;
let editingHypothesis = null;
let currentEditType = 'llm_ask'; // 'llm_edit', 'llm_ask', or 'manual'

// Hypothesis edit history
let hypothesisHistory = [];
let historyIndex = -1;
let confirmBaselineText = '';

function bumpUiStateVersion(reason = 'unknown') {
    uiStateVersion += 1;
    console.log(`[state] uiStateVersion=${uiStateVersion} reason=${reason}`);
    return uiStateVersion;
}

function setChatSubmitPending(pending) {
    const chatSendBtn = document.getElementById('chat-send-btn');
    const chatInput = document.getElementById('chat-input');
    if (chatSendBtn) {
        chatSendBtn.disabled = pending;
    }
    if (chatInput) {
        chatInput.disabled = pending;
    }
}

function cancelChatRequest(reason = 'context-change') {
    chatRequestToken += 1;
    if (chatRequestController) {
        try {
            chatRequestController.abort();
        } catch (err) {
            // Ignore abort errors.
        }
        chatRequestController = null;
    }
    chatRequestInFlight = false;
    setChatSubmitPending(false);
    console.log(`[chat] request cancelled reason=${reason}`);
}

function cancelEditFormRequest(reason = 'context-change') {
    editFormRequestToken += 1;
    if (editFormRequestController) {
        try {
            editFormRequestController.abort();
        } catch (err) {
            // Ignore abort errors.
        }
        editFormRequestController = null;
    }
    editFormRequestInFlight = false;
    console.log(`[edit-form] request cancelled reason=${reason}`);
}

function cancelRegenerateRequest(reason = 'context-change') {
    regenerateRequestToken += 1;
    if (regenerateRequestController) {
        try {
            regenerateRequestController.abort();
        } catch (err) {
            // Ignore abort errors.
        }
        regenerateRequestController = null;
    }
    regenerateRequestInFlight = false;
    console.log(`[regenerate] request cancelled reason=${reason}`);
}

function invalidateInteractiveRequests(reason = 'context-change') {
    cancelChatRequest(reason);
    cancelEditFormRequest(reason);
    cancelRegenerateRequest(reason);
}

// Utility functions
function showLoading(message = 'Processing...', options = null) {
    const overlay = document.getElementById('loading-overlay');
    const messageEl = document.getElementById('loading-message');
    const cancelBtn = document.getElementById('loading-cancel-btn');
    messageEl.textContent = message;
    overlay.classList.remove('hidden');

    if (cancelBtn) {
        cancelBtn.classList.add('hidden');
        cancelBtn.disabled = false;
        if (loadingCancelHandler) {
            cancelBtn.removeEventListener('click', loadingCancelHandler);
            loadingCancelHandler = null;
        }
        const cancelable = options && options.cancelable;
        if (cancelable && typeof options.onCancel === 'function') {
            cancelBtn.textContent = options.cancelLabel || 'Cancel';
            cancelBtn.classList.remove('hidden');
            loadingCancelHandler = () => {
                cancelBtn.disabled = true;
                options.onCancel();
            };
            cancelBtn.addEventListener('click', loadingCancelHandler);
        }
    }
}

function hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    const cancelBtn = document.getElementById('loading-cancel-btn');
    if (overlay) {
        overlay.classList.add('hidden');
    }
    if (cancelBtn) {
        cancelBtn.classList.add('hidden');
        cancelBtn.disabled = false;
        if (loadingCancelHandler) {
            cancelBtn.removeEventListener('click', loadingCancelHandler);
            loadingCancelHandler = null;
        }
    }
}

function showStatus(elementId, message, type) {
    const element = document.getElementById(elementId);
    element.textContent = message;
    element.className = `status-message ${type}`;
    element.style.display = 'block';

    if (type === 'success') {
        setTimeout(() => {
            element.style.display = 'none';
        }, 5000);
    }
}

function hideStatus(elementId) {
    const element = document.getElementById(elementId);
    element.style.display = 'none';
}

function showFormStatus(elementId, message, type = 'success', timeout = 5000) {
    const element = document.getElementById(elementId);
    if (!element) return;
    element.textContent = message;
    element.classList.remove('hidden', 'error', 'success');
    element.classList.add(type === 'error' ? 'error' : 'success');
    if (type === 'success' && timeout) {
        setTimeout(() => {
            element.classList.add('hidden');
        }, timeout);
    }
}

function showModal(modalId) {
    document.getElementById(modalId).classList.remove('hidden');
}

function hideModal(modalId) {
    document.getElementById(modalId).classList.add('hidden');
}

function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitForS3SyncIfNeeded() {
    try {
        let status = await apiCall('/api/s3_sync_status');
        if (!status || !status.success || !status.required) {
            return true;
        }

        if (status.state === 'success') {
            return true;
        }

        showLoading('Pulling checkpoints and code for your session.\nThis can take a few minutes.');

        while (true) {
            if (status.state === 'error') {
                const err = status.error || 'Unknown error';
                showLoading(`Pulling Checkpoints and Code failed: ${err}`);
                return false;
            }

            await delay(2000);
            status = await apiCall('/api/s3_sync_status');

            if (!status || !status.success) {
                showLoading('Pulling checkpoints and code for your session.\nThis can take a few minutes.');
                continue;
            }

            if (!status.required || status.state === 'success') {
                hideLoading();
                return true;
            }

            if (status.state === 'error') {
                const err = status.error || 'Unknown error';
                showLoading(`Pulling Checkpoints and Code failed: ${err}`);
                return false;
            }
        }
    } catch (error) {
        console.warn('Failed to poll S3 sync status:', error);
        return true;
    }
}

// Format markdown-like text to HTML
function formatMarkdown(text) {
    if (!text) return '';

    const codeBlocks = [];
    const inlineCodes = [];
    const tableBlocks = [];

    const normalizeCodeContent = (codeText) => {
        if (!codeText) return '';
        // Avoid double-escaping already-escaped content
        if (codeText.includes('&lt;') || codeText.includes('&gt;') || codeText.includes('&amp;')) {
            return codeText;
        }
        return escapeHtml(codeText);
    };

    // Extract fenced code blocks first to avoid markdown transforms inside them.
    // Image processing (below) runs after this so that image syntax inside
    // fenced code blocks is never converted to <img> tags.
    let formatted = text.replace(/```(\w+)?\n([\s\S]*?)```/g, (match, lang, code) => {
        const safe = normalizeCodeContent(code.replace(/\s+$/, ''));
        const langClass = lang ? ` language-${lang}` : '';
        const blockHtml = `<pre><code class="${langClass.trim()}">${safe}</code></pre>`;
        const token = `@@CODEBLOCK${codeBlocks.length}@@`;
        codeBlocks.push(blockHtml);
        return token;
    });

    // Convert markdown image syntax to <img> tags routed through /api/serve_asset.
    // Must run after code-block extraction (so ![...](path) inside ``` is never
    // converted) but before inline-code extraction.
    // escapeHtml (called by callers before formatMarkdown) does not touch !, [, ], (, )
    // so the syntax arrives here intact.
    // Regex: ![alt text](path/or/url) — stops at whitespace or ) to avoid over-capture.
    // An optional quoted title ("title") after the path is consumed but discarded.
    formatted = formatted.replace(/!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)/g, (match, alt, src) => {
        const safeSrc = (src.startsWith('http') || src.startsWith('data:') || src.startsWith('/api/serve_asset'))
            ? src
            : '/api/serve_asset?path=' + encodeURIComponent(src);
        return `<img src="${safeSrc}" alt="${alt}" style="max-width:100%;border-radius:4px;margin-top:8px;display:block;" onerror="this.style.display='none'">`;
    });

    // Extract inline code before other markdown replacements
    formatted = formatted.replace(/`([^`]+)`/g, (match, code) => {
        const safe = normalizeCodeContent(code);
        const inlineHtml = `<code>${safe}</code>`;
        const token = `@@INLINECODE${inlineCodes.length}@@`;
        inlineCodes.push(inlineHtml);
        return token;
    });

    // Convert **bold** to <strong>bold</strong>
    formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

    // Convert *italic* to <em>italic</em>
    formatted = formatted.replace(/\*(.*?)\*/g, '<em>$1</em>');

    // Convert ### headings
    formatted = formatted.replace(/^### (.*?)$/gm, '<h3>$1</h3>');
    formatted = formatted.replace(/^## (.*?)$/gm, '<h2>$1</h2>');
    formatted = formatted.replace(/^# (.*?)$/gm, '<h1>$1</h1>');

    const splitTableRow = (line) => {
        let trimmed = line.trim();
        if (trimmed.startsWith('|')) trimmed = trimmed.slice(1);
        if (trimmed.endsWith('|')) trimmed = trimmed.slice(0, -1);
        return trimmed.split('|').map(cell => cell.trim());
    };

    const isTableSeparator = (line) => {
        if (!line.includes('|')) return false;
        const cells = splitTableRow(line);
        if (cells.length < 2) return false;
        return cells.every(cell => /^:?-{3,}:?$/.test(cell));
    };

    const buildTableHtml = (headerLine, separatorLine, rowLines) => {
        const headers = splitTableRow(headerLine);
        const aligns = splitTableRow(separatorLine).map(cell => {
            const left = cell.startsWith(':');
            const right = cell.endsWith(':');
            if (left && right) return 'center';
            if (right) return 'right';
            if (left) return 'left';
            return null;
        });

        const headHtml = headers.map((cell, idx) => {
            const align = aligns[idx];
            const alignAttr = align ? ` style="text-align:${align};"` : '';
            return `<th${alignAttr}>${cell}</th>`;
        }).join('');

        const bodyHtml = rowLines.map(rowLine => {
            const cells = splitTableRow(rowLine);
            const rowCells = headers.map((_, idx) => {
                const cell = cells[idx] || '';
                const align = aligns[idx];
                const alignAttr = align ? ` style="text-align:${align};"` : '';
                return `<td${alignAttr}>${cell}</td>`;
            }).join('');
            return `<tr>${rowCells}</tr>`;
        }).join('');

        return `<table><thead><tr>${headHtml}</tr></thead>${bodyHtml ? `<tbody>${bodyHtml}</tbody>` : ''}</table>`;
    };

    const extractTables = (input) => {
        const lines = input.split('\n');
        const output = [];
        let i = 0;
        while (i < lines.length) {
            const line = lines[i];
            if (line && line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
                const headerLine = line;
                const separatorLine = lines[i + 1];
                const rowLines = [];
                i += 2;
                while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
                    rowLines.push(lines[i]);
                    i += 1;
                }
                const tableHtml = buildTableHtml(headerLine, separatorLine, rowLines);
                const token = `@@TABLE${tableBlocks.length}@@`;
                tableBlocks.push(tableHtml);
                output.push(token);
                continue;
            }
            output.push(line);
            i += 1;
        }
        return output.join('\n');
    };

    formatted = extractTables(formatted);

    // Convert line breaks to <br> (but not for already formatted HTML)
    formatted = formatted.split('\n\n').map(para => {
        para = para.trim();
        if (!para) return '';

        if (/^@@CODEBLOCK\d+@@$/.test(para)) return para;
        if (/^@@TABLE\d+@@$/.test(para)) return para;

        // Skip if it's a complete list block
        if (para.startsWith('<ul>') || para.startsWith('<ol>')) return para;

        // If it contains headings, convert single newlines to <br> but don't wrap in <p>
        // This handles plans with headings followed by content
        if (para.match(/<h[1-6]>/)) {
            // Split into lines and add <br> between content lines (but not after headings)
            let lines = para.split('\n');
            return lines.map((line, idx) => {
                // Don't add <br> after heading tags or at the end
                if (line.match(/<\/h[1-6]>/) || idx === lines.length - 1) {
                    return line;
                }
                // Don't add <br> if next line is a heading
                if (idx < lines.length - 1 && lines[idx + 1].match(/<h[1-6]>/)) {
                    return line;
                }
                return line + '<br>';
            }).join('');
        }

        // Regular paragraph
        return `<p>${para.replace(/\n/g, '<br>')}</p>`;
    }).join('');

    // Handle bullet points
    formatted = formatted.replace(/^- (.*?)$/gm, '<li>$1</li>');
    formatted = formatted.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

    // Restore code blocks and placeholders (tables first so inline code inside tables is replaced)
    formatted = formatted.replace(/@@CODEBLOCK(\d+)@@/g, (match, idx) => codeBlocks[Number(idx)] || '');
    formatted = formatted.replace(/@@TABLE(\d+)@@/g, (match, idx) => tableBlocks[Number(idx)] || '');
    formatted = formatted.replace(/@@INLINECODE(\d+)@@/g, (match, idx) => inlineCodes[Number(idx)] || '');

    return formatted;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function applyCodeHighlight(root = document) {
    if (!window.hljs || !root) return;
    const blocks = root.querySelectorAll('pre code');
    blocks.forEach((block) => {
        if (block.dataset.highlighted) return;
        window.hljs.highlightElement(block);
        block.dataset.highlighted = 'true';
    });
}

window.addEventListener('load', () => applyCodeHighlight(document));

// ============================================================================
// Citation Rendering Functions
// ============================================================================

/**
 * Build and cache citation data for consistent numbering.
 * Call this whenever citations are updated.
 */
function buildAndCacheCitations(citations) {
    citationDataMap = {};
    let citationIndex = 1;

    if (!citations || citations.length === 0) {
        return;
    }

    citations.forEach(block => {
        if (!block.citations || !Array.isArray(block.citations)) return;

        block.citations.forEach(cit => {
            citationDataMap[citationIndex] = {
                index: citationIndex,
                citation: cit,
                blockText: (block.text || '').replace(/\n+/g, ' ').trim(),
                docIndex: cit.document_index !== undefined ? cit.document_index : -1,
                startChar: cit.start_char_index !== undefined ? cit.start_char_index : -1,
                endChar: cit.end_char_index !== undefined ? cit.end_char_index : -1,
                citedText: cit.cited_text || ''
            };
            citationIndex++;
        });
    });
}

/**
 * Render citations as a collapsible section.
 * @param {Array} citations - Citations array
 * @param {Array} documents - Documents array
 * @param {boolean} startExpanded - Whether to start expanded
 * @param {Object} usageMap - Map of citation index to array of hypothesis indices
 */
function renderCitations(citations, documents = [], startExpanded = false, usageMap = {}) {
    if (!citations || citations.length === 0) {
        return '';
    }

    // Build citation cache
    buildAndCacheCitations(citations);

    const collapsedClass = startExpanded ? '' : 'collapsed';
    const toggleText = startExpanded ? 'Hide' : 'Show';
    const totalCitations = Object.keys(citationDataMap).length;

    if (totalCitations === 0) {
        return '';
    }

    let citationItems = '';
    for (let i = 1; i <= totalCitations; i++) {
        const citInfo = citationDataMap[i];
        if (!citInfo) continue;

        const sourceTitle = getSourceTitle(citInfo.citation, documents);
        const citedText = citInfo.citedText;
        const location = formatCitationLocation(citInfo.citation);
        const isClickable = citInfo.docIndex >= 0;

        // Build hypothesis usage text
        let usageText = '';
        if (usageMap[i] && usageMap[i].length > 0) {
            const hypIndices = usageMap[i].sort((a, b) => a - b);
            usageText = '<div style="margin-top: 4px; font-size: 0.85rem; color: var(--text-secondary);">Used in: Hypothesis ' + hypIndices.join(', ') + '</div>';
            console.log(`Citation [${i}] usage text:`, usageText);
        } else {
            console.log(`Citation [${i}] has no usage data. usageMap[${i}]:`, usageMap[i]);
        }

        // Build attributes on single line to avoid HTML issues
        const clickAttr = isClickable ? `data-cit-click="${i}" title="Click to view source document"` : '';
        const clickClass = isClickable ? 'citation-item-clickable' : '';

        citationItems += `<div class="citation-item ${clickClass}" ${clickAttr}><div class="citation-item-header"><span class="citation-source">[${i}] ${escapeHtml(sourceTitle)}</span>${location ? `<span class="citation-location">${escapeHtml(location)}</span>` : ''}</div>${citedText ? `<div class="citation-quote">"${escapeHtml(truncateText(citedText, 300))}"</div>` : ''}${usageText}</div>`;
    }

    return `<div class="citations-container"><div class="citations-header"><span class="citation-badge"><span class="citation-badge-icon">📚</span>${totalCitations} citation${totalCitations > 1 ? 's' : ''}</span><button type="button" class="citations-toggle" onclick="toggleCitations(this)" data-expanded="${startExpanded}">${toggleText}</button></div><div class="citations-list ${collapsedClass}">${citationItems}</div></div>`;
}

/**
 * Get the title of the source document for a citation.
 */
function getSourceTitle(citation, documents) {
    if (documents && documents.length > 0 && citation.document_index !== undefined) {
        const doc = documents[citation.document_index];
        if (doc && doc.title) {
            return doc.title;
        }
    }
    if (citation.type === 'char_location') {
        return 'Source Document';
    }
    return citation.source || 'Reference';
}

/**
 * Format the location within a citation.
 */
function formatCitationLocation(citation) {
    if (citation.type === 'char_location') {
        const start = citation.start_char_index;
        const end = citation.end_char_index;
        if (start !== undefined && end !== undefined) {
            return `chars ${start}-${end}`;
        }
    }
    return '';
}

/**
 * Toggle visibility of a citations list.
 */
function toggleCitations(button) {
    const container = button.closest('.citations-container');
    const list = container.querySelector('.citations-list');
    const isExpanded = button.dataset.expanded === 'true';

    if (isExpanded) {
        list.classList.add('collapsed');
        button.textContent = 'Show';
        button.dataset.expanded = 'false';
    } else {
        list.classList.remove('collapsed');
        button.textContent = 'Hide';
        button.dataset.expanded = 'true';
    }
}

/**
 * Update the global citation documents cache.
 */
function updateCitationDocuments(documents) {
    if (documents && Array.isArray(documents)) {
        citationDocuments = documents;
    }
}

/**
 * Handle citation click - opens document modal with highlighted text.
 */
async function handleCitationClick(citationIndex) {
    const citInfo = citationDataMap[citationIndex];
    if (!citInfo || citInfo.docIndex < 0) {
        alert('Citation source not available');
        return;
    }

    showLoading('Loading cited document...');

    try {
        const result = await apiCall('/api/get_citation_document', { document_index: citInfo.docIndex });

        if (!result.success) {
            hideLoading();
            alert('Error: ' + result.error);
            return;
        }

        const title = result.title || 'Document';
        let content = result.content || '';

        document.getElementById('modal-title').textContent = 'Citation [' + citationIndex + '] - ' + title;

        let formattedContent = '';
        const startChar = citInfo.startChar;
        const endChar = citInfo.endChar;

        if (startChar >= 0 && endChar > startChar && endChar <= content.length) {
            // Insert unique markers around the cited text, then format as markdown
            const HIGHLIGHT_START = '\u0000CITE_START\u0000';
            const HIGHLIGHT_END = '\u0000CITE_END\u0000';
            const contentWithMarkers = content.slice(0, startChar) + HIGHLIGHT_START + content.slice(startChar, endChar) + HIGHLIGHT_END + content.slice(endChar);
            formattedContent = formatMarkdown(contentWithMarkers);
            // Replace markers with highlight span
            formattedContent = formattedContent.replace(HIGHLIGHT_START, '<span id="citation-highlight" class="citation-highlight">').replace(HIGHLIGHT_END, '</span>');
        } else {
            formattedContent = formatMarkdown(content);
        }

        document.getElementById('modal-report-content').innerHTML = formattedContent;
        showModal('report-modal');

        setTimeout(function() {
            const highlightEl = document.getElementById('citation-highlight');
            if (highlightEl) {
                highlightEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }, 100);

    } catch (error) {
        alert('Error loading document: ' + error.message);
    } finally {
        hideLoading();
    }
}

// Legacy function name for compatibility
function openCitationDocument(citationIndex, docIndex, startChar, endChar) {
    handleCitationClick(citationIndex);
}

/**
 * Handle citation hover - highlights corresponding text in hypothesis.
 */
function handleCitationHover(citationIndex, highlight) {
    const citInfo = citationDataMap[citationIndex];
    if (!citInfo || !citInfo.blockText) return;

    // Find all hypothesis text containers and remove existing highlights
    document.querySelectorAll('.citation-text-highlight').forEach(function(el) {
        const text = el.textContent;
        el.replaceWith(document.createTextNode(text));
    });

    if (highlight && citInfo.blockText.length > 10) {
        // Try to find and highlight the block text
        document.querySelectorAll('.hypothesis-text').forEach(function(container) {
            highlightTextInContainer(container, citInfo.blockText);
        });
    }
}

/**
 * Highlight text within a container element.
 */
function highlightTextInContainer(container, searchText) {
    const text = container.textContent;
    const searchSnippet = searchText.slice(0, Math.min(80, searchText.length));
    const index = text.toLowerCase().indexOf(searchSnippet.toLowerCase());

    if (index === -1) return;

    // Use TreeWalker to find text nodes
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
    let currentPos = 0;
    let node;

    while (node = walker.nextNode()) {
        const nodeLen = node.textContent.length;
        if (currentPos + nodeLen > index) {
            const startInNode = index - currentPos;
            const endInNode = Math.min(startInNode + searchSnippet.length, nodeLen);

            if (endInNode - startInNode > 5) {
                const before = node.textContent.slice(0, startInNode);
                const match = node.textContent.slice(startInNode, endInNode);
                const after = node.textContent.slice(endInNode);

                const span = document.createElement('span');
                span.className = 'citation-text-highlight';
                span.textContent = match;

                const parent = node.parentNode;
                const frag = document.createDocumentFragment();
                if (before) frag.appendChild(document.createTextNode(before));
                frag.appendChild(span);
                if (after) frag.appendChild(document.createTextNode(after));
                parent.replaceChild(frag, node);
            }
            break;
        }
        currentPos += nodeLen;
    }
}

/**
 * Wrap cited text segments in the hypothesis with clickable citation spans.
 * Instead of inserting [#] markers, this highlights the actual text that has citations.
 * @param {string} text - The text to annotate
 * @param {Array} citations - The citations array
 * @param {number} hypothesisIndex - Optional hypothesis index for tracking usage
 * @returns {Object} { annotatedText, citationMap, usedCitations }
 */
function addInlineCitationMarkers(text, citations, hypothesisIndex = null) {
    if (!citations || citations.length === 0 || !text) {
        return { annotatedText: text, citationMap: [], usedCitations: [] };
    }

    // Make sure citation data is cached
    if (Object.keys(citationDataMap).length === 0) {
        buildAndCacheCitations(citations);
    }

    let annotatedText = text;
    const wrappedRanges = []; // Track what we've already wrapped to avoid overlaps
    const usedCitations = []; // Track which citations were actually used in this text

    // Process each citation - wrap the blockText with a citation span
    for (const idx in citationDataMap) {
        const citInfo = citationDataMap[idx];
        if (!citInfo.blockText || citInfo.blockText.length < 10) continue;

        // Find the blockText in the current annotatedText
        // Use a snippet to improve matching after HTML formatting
        const searchText = citInfo.blockText.slice(0, Math.min(80, citInfo.blockText.length));
        const searchTextClean = searchText.replace(/\*\*/g, '').replace(/\*/g, '').replace(/\s+/g, ' ').trim();

        if (searchTextClean.length < 10) continue;

        // Try to find this text, avoiding already-wrapped sections
        const matchIndex = findTextNotInSpan(annotatedText, searchTextClean, wrappedRanges);

        if (matchIndex !== -1) {
            // Find how much of the original blockText we can match
            const matchEnd = findMatchEnd(annotatedText, matchIndex, citInfo.blockText);
            const matchLength = matchEnd - matchIndex;

            if (matchLength >= 10) {
                let before = annotatedText.slice(0, matchIndex);
                const matched = annotatedText.slice(matchIndex, matchEnd);
                let after = annotatedText.slice(matchEnd);

                // Check if there's a <br> right before the match - if so, remove it
                // This prevents the visual issue of a line break appearing right before underlined citation text
                // The text should flow naturally inline
                if (before.endsWith('<br>')) {
                    before = before.slice(0, -4) + ' ';
                }

                // Check if there's a <br> right after the match
                // Only keep it if it's followed by another <br> (paragraph break) or </p> (end of paragraph)
                // Otherwise, replace with space to maintain inline flow
                if (after.startsWith('<br>')) {
                    const afterBr = after.slice(4);
                    if (!afterBr.startsWith('<br>') && !afterBr.startsWith('</p>') && !afterBr.startsWith('<p>')) {
                        after = ' ' + afterBr;
                    }
                }

                // Wrap with citation span including label for tooltip
                const wrapped = '<span class="citation-inline" data-cit="' + citInfo.index + '" data-cit-label="[' + citInfo.index + ']">' + matched + '</span>';
                annotatedText = before + wrapped + after;

                // Track this range (adjusted for the added HTML)
                wrappedRanges.push({ start: matchIndex, end: matchIndex + wrapped.length });

                // Track that this citation was used
                usedCitations.push(citInfo.index);

                // If we have a hypothesis index, track the usage globally
                if (hypothesisIndex !== null) {
                    if (!citationUsageByHypothesis[citInfo.index]) {
                        citationUsageByHypothesis[citInfo.index] = [];
                    }
                    if (!citationUsageByHypothesis[citInfo.index].includes(hypothesisIndex)) {
                        citationUsageByHypothesis[citInfo.index].push(hypothesisIndex);
                        console.log(`Citation [${citInfo.index}] matched in Hypothesis ${hypothesisIndex}`);
                    }
                }
            }
        }
    }

    return { annotatedText: annotatedText, citationMap: citationDataMap, usedCitations: usedCitations };
}

/**
 * Find text in the string that is not already inside a citation span.
 */
function findTextNotInSpan(html, searchText, wrappedRanges) {
    const lowerHtml = html.toLowerCase();
    const lowerSearch = searchText.toLowerCase();
    let startPos = 0;

    while (true) {
        const index = lowerHtml.indexOf(lowerSearch, startPos);
        if (index === -1) return -1;

        // Check if this position is inside an existing wrapped range
        const isInWrapped = wrappedRanges.some(r => index >= r.start && index < r.end);

        // Check if we're inside a tag
        const beforeText = html.slice(0, index);
        const lastOpenTag = beforeText.lastIndexOf('<');
        const lastCloseTag = beforeText.lastIndexOf('>');
        const insideTag = lastOpenTag > lastCloseTag;

        // Check if we're inside a citation-inline span
        const lastCitationOpen = beforeText.lastIndexOf('<span class="citation-inline"');
        const lastSpanClose = beforeText.lastIndexOf('</span>');
        const insideCitationSpan = lastCitationOpen > lastSpanClose;

        if (!isInWrapped && !insideTag && !insideCitationSpan) {
            return index;
        }

        startPos = index + 1;
    }
}

/**
 * Find where the match should end, trying to match as much of the blockText as possible.
 */
function findMatchEnd(html, startIndex, blockText) {
    // Start with a reasonable match length and try to extend it
    const maxLen = Math.min(blockText.length, 200);
    let bestEnd = startIndex + Math.min(40, html.length - startIndex);

    // Try to find sentence boundaries or natural break points
    const searchArea = html.slice(startIndex, startIndex + maxLen + 50);

    // Look for sentence endings
    const sentenceEnd = searchArea.search(/[.!?](?=\s|<|$)/);
    if (sentenceEnd !== -1 && sentenceEnd > 20) {
        bestEnd = startIndex + sentenceEnd + 1;
    }

    // Don't end inside an HTML tag
    let candidateEnd = bestEnd;
    while (candidateEnd < html.length) {
        const nextChar = html[candidateEnd];
        if (nextChar === '<') break; // Stop before a tag
        if (nextChar === '>' || nextChar === undefined) break;
        candidateEnd++;
    }

    // Make sure we don't cut off mid-word
    while (candidateEnd > startIndex && html[candidateEnd - 1] && html[candidateEnd - 1].match(/\w/)) {
        const nextChar = html[candidateEnd];
        if (!nextChar || !nextChar.match(/\w/)) break;
        candidateEnd++;
    }

    return Math.min(candidateEnd, html.length);
}

/**
 * Find the best position to insert a citation marker.
 * @param {string} fullText - The full text being annotated
 * @param {string} blockText - The response text block that has this citation
 * @param {string} citedText - The text that was cited from the source
 * @param {Set} usedPositions - Positions already used
 * @returns {number} Position to insert, or -1 if not found
 */
function findBestInsertionPoint(fullText, blockText, citedText, usedPositions) {
    // First, try to find where the block text appears in the full text
    const blockStart = fullText.indexOf(blockText);
    if (blockStart === -1) {
        // Block not found directly, try finding key phrases from cited text
        return findInsertionByKeyPhrases(fullText, citedText, usedPositions);
    }

    const blockEnd = blockStart + blockText.length;
    
    // Find the end of the sentence within or near the block
    // Look for sentence endings: . ! ? followed by space or end
    const sentenceEndPattern = /[.!?](?=\s|$|<)/g;
    let lastSentenceEnd = -1;
    let match;
    
    // Search within the block area
    const searchText = fullText.slice(blockStart, Math.min(blockEnd + 50, fullText.length));
    while ((match = sentenceEndPattern.exec(searchText)) !== null) {
        const absolutePos = blockStart + match.index + 1;
        if (!usedPositions.has(absolutePos)) {
            lastSentenceEnd = absolutePos;
        }
    }

    if (lastSentenceEnd !== -1) {
        return lastSentenceEnd;
    }

    // Fallback: end of the block text
    if (!usedPositions.has(blockEnd)) {
        return blockEnd;
    }

    return -1;
}

/**
 * Escape special regex characters in a string.
 */
function escapeRegexChars(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Find insertion point by matching key phrases from cited text.
 */
function findInsertionByKeyPhrases(fullText, citedText, usedPositions) {
    if (!citedText || citedText.length < 10) return -1;

    // Extract key phrases (first few words, last few words)
    // Also strip markdown formatting characters
    const cleanedText = citedText.replace(/\*\*/g, '').replace(/\*/g, '');
    const words = cleanedText.split(/\s+/).filter(w => w.length > 3);
    if (words.length < 2) return -1;

    // Try to find a sequence of 3-4 consecutive words from the cited text
    for (let windowSize = Math.min(4, words.length); windowSize >= 2; windowSize--) {
        for (let i = 0; i <= words.length - windowSize; i++) {
            // Escape each word for regex safety and join with flexible whitespace
            const phrase = words.slice(i, i + windowSize).map(escapeRegexChars).join('\\s+');
            try {
                const regex = new RegExp(phrase, 'i');
                const match = fullText.match(regex);
                if (match) {
                    const endPos = match.index + match[0].length;
                    // Find the next sentence end after this match
                    const afterMatch = fullText.slice(endPos);
                    const sentenceEnd = afterMatch.search(/[.!?](?=\s|$|<)/);
                    if (sentenceEnd !== -1) {
                        const insertPos = endPos + sentenceEnd + 1;
                        if (!usedPositions.has(insertPos)) {
                            return insertPos;
                        }
                    }
                }
            } catch (e) {
                // Skip invalid regex patterns
                continue;
            }
        }
    }

    return -1;
}

/**
 * Process hypothesis text to add citation markers.
 * @param {string} hypothesisText - The full hypothesis text
 * @param {Array} citations - Citations array from the API
 * @returns {string} Text with inline citation markers added
 */
function annotateTextWithCitations(hypothesisText, citations, hypothesisIndex = null) {
    if (!citations || citations.length === 0) {
        return hypothesisText;
    }

    const { annotatedText } = addInlineCitationMarkers(hypothesisText, citations, hypothesisIndex);
    return annotatedText;
}

function truncateText(text, maxLength = 280) {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength).trim()}...`;
}

function getHypothesisFullText(hypothesis) {
    if (!hypothesis) return '';
    return hypothesis.full_text || '';
}

function clampMainSplitPercent(percent) {
    const parsed = Number(percent);
    if (!Number.isFinite(parsed)) return 50;
    return Math.min(75, Math.max(25, parsed));
}

function queuePersistHypothesisChatState(delayMs = 120) {
    if (hypothesisChatPersistTimer) {
        clearTimeout(hypothesisChatPersistTimer);
    }
    hypothesisChatPersistTimer = window.setTimeout(() => {
        hypothesisChatPersistTimer = null;
        persistHypothesisChatState();
    }, delayMs);
}

function compactHypothesisMessagesForStorage(messages) {
    return normalizeStoredChatMessages(
        messages,
        HYPOTHESIS_CHAT_STORAGE_MAX_MESSAGES,
        HYPOTHESIS_CHAT_STORAGE_MAX_CHARS
    );
}

function persistHypothesisChatState() {
    try {
        const messagesEl = document.getElementById('chat-messages');
        const persistedMode = chatMode === 'code'
            ? 'code'
            : (chatMode === 'edit' ? 'edit' : 'hypothesis');
        const state = {
            version: CHAT_STORAGE_VERSION,
            collapsed: chatCollapsed,
            splitPercent: clampMainSplitPercent(currentSplitPercent),
            mode: persistedMode,
            scrollTop: messagesEl ? messagesEl.scrollTop : 0,
            messages: compactHypothesisMessagesForStorage(chatMessages),
            updatedAt: Date.now(),
        };
        if (editMode) {
            const editKey = getEditHypothesisChatStorageKey(editingHypothesis ? editingHypothesis.index : null);
            if (!editKey) {
                return;
            }
            state.editType = currentEditType === 'llm_edit' ? 'llm_edit' : 'llm_ask';
            localStorage.setItem(editKey, JSON.stringify(state));
            return;
        }
        localStorage.setItem(hypothesisChatStorageKey, JSON.stringify(state));
    } catch (error) {
        console.warn('[chat] failed to persist hypothesis chat state', error);
    }
}

function hydrateHypothesisChatStateFromStorage({ restorePanelState = true } = {}) {
    const stored = readHypothesisChatStateFromStorage();
    if (!stored) {
        return false;
    }

    chatMode = stored.mode;
    currentSplitPercent = clampMainSplitPercent(stored.splitPercent);
    chatPendingScrollTop = stored.scrollTop;

    clearChatMessagesUi({ persistState: false });
    for (const message of stored.messages) {
        appendChatMessage({
            role: message.role,
            content: message.content,
            mode: message.mode || 'hypothesis',
            thinking: message.thinking || null,
            responseTimeMs: message.responseTimeMs,
            persistState: false,
            autoScroll: false
        });
    }

    document.querySelectorAll('.chat-mode-button').forEach(btn => {
        if (btn.dataset.mode === chatMode) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });

    if (auditOnly || !restorePanelState) {
        return true;
    }

    if (!stored.collapsed && !auditOnly) {
        setChatPanelCollapsed(false, { persistState: false, restoreScroll: true, skipContextLoad: true });
    } else {
        chatCollapsed = true;
        applySplitWidths(currentSplitPercent, { persistState: false });
    }
    return true;
}

function hydrateEditHypothesisChatStateFromStorage(index) {
    const stored = readEditHypothesisChatStateFromStorage(index);
    if (!stored) {
        return false;
    }

    clearChatMessagesUi({ persistState: false });
    for (const message of stored.messages) {
        appendChatMessage({
            role: message.role,
            content: message.content,
            mode: message.mode || 'edit',
            thinking: message.thinking || null,
            responseTimeMs: message.responseTimeMs,
            persistState: false,
            autoScroll: false
        });
    }

    if (stored.editType === 'llm_edit' || stored.editType === 'llm_ask') {
        setEditType(stored.editType);
    }

    const messagesEl = document.getElementById('chat-messages');
    if (messagesEl && Number.isFinite(stored.scrollTop)) {
        window.requestAnimationFrame(() => {
            messagesEl.scrollTop = stored.scrollTop;
        });
    }

    return chatMessages.length > 0;
}

function resetChatPanel({ persistState = true } = {}) {
    chatMessages = [];
    chatContextCache = null;
    chatMode = 'hypothesis';
    chatPendingScrollTop = null;
    document.querySelectorAll('.chat-mode-button').forEach(btn => {
        if (btn.dataset.mode === 'hypothesis') {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
    const messagesEl = document.getElementById('chat-messages');
    if (messagesEl) {
        messagesEl.innerHTML = '';
    }
    const contextEl = document.getElementById('chat-context-content');
    if (contextEl) {
        contextEl.innerHTML = auditOnly
            ? '<p class="chat-context-empty">Initialize a session to load dataset, reports, research paper, and hypotheses.</p>'
            : '<p class="chat-context-empty">Initialize a session to load dataset, reports, and hypotheses.</p>';
    }
    chatPanelMounted = false;
    chatCollapsed = true;
    if (chatPanelEl) {
        chatPanelEl.classList.add('collapsed', 'hidden');
    }
    if (splitDividerEl) {
        splitDividerEl.classList.add('collapsed', 'hidden');
    }
    if (leftPanelEl) {
        leftPanelEl.style.flexBasis = '100%';
    }
    if (chatToggleBtn) {
        chatToggleBtn.classList.remove('active');
        chatToggleBtn.classList.add('hidden');
        chatToggleBtn.setAttribute('aria-pressed', 'false');
    }
    if (workspaceEl) {
        workspaceEl.classList.add('hidden');
    }
    if (persistState) {
        persistHypothesisChatState();
    }
}

function updateCodeModeAvailability() {
    const codeModeBtn = document.querySelector('#chat-mode-toggle .chat-mode-button[data-mode="code"]');
    if (!codeModeBtn) return;

    if (codeChatAvailable) {
        codeModeBtn.removeAttribute('disabled');
        codeModeBtn.classList.remove('disabled');
        codeModeBtn.title = 'Send questions to the Claude Code sub-agent';
    } else {
        codeModeBtn.setAttribute('disabled', 'disabled');
        codeModeBtn.classList.add('disabled');
        codeModeBtn.title = 'Provide a code directory when launching the server to enable code chat';
    }
}

function applyAuditOnlyLayout() {
    const hideIds = [
        'config-section',
        'generate-section',
        'editor-instructions-banner',
        'research-feedback-section',
        'final-accept-bar',
        'user-instructions',
        'reports-section',
    ];
    hideIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
    });

    const frontPageGrid = document.querySelector('.front-page-grid');
    if (frontPageGrid) {
        frontPageGrid.classList.add('hidden');
    }

    const generateBtn = document.getElementById('generate-btn');
    if (generateBtn) {
        generateBtn.disabled = true;
        generateBtn.setAttribute('aria-disabled', 'true');
    }

    const workspace = document.getElementById('workspace');
    if (workspace) {
        workspace.classList.remove('hidden');
        workspace.classList.add('audit-only-chat');
    }

    const contentPanel = document.getElementById('content-panel');
    if (contentPanel) {
        contentPanel.classList.add('hidden');
    }

    const chatPanel = document.getElementById('chat-panel');
    if (chatPanel) {
        chatPanel.classList.remove('hidden', 'collapsed');
        chatPanel.style.flexBasis = '100%';
    }

    const splitDivider = document.getElementById('split-divider');
    if (splitDivider) {
        splitDivider.classList.add('hidden');
    }

    const chatToggle = document.getElementById('chat-toggle-btn');
    if (chatToggle) {
        chatToggle.classList.add('hidden');
    }

    const chatModeToggle = document.getElementById('chat-mode-toggle');
    if (chatModeToggle) {
        chatModeToggle.classList.add('hidden');
    }
    chatMode = 'hypothesis';
    chatPanelMounted = true;
    chatCollapsed = false;
    ensureChatGreeting();
    if (sessionInitialized) {
        loadChatContext(true);
    }

    const bgLitBtn = document.getElementById('view-background-literature-btn');
    if (bgLitBtn) {
        bgLitBtn.classList.add('hidden');
    }

    const topbarLogoutBtn = document.getElementById('audit-topbar-logout');
    if (topbarLogoutBtn) {
        topbarLogoutBtn.addEventListener('click', async () => {
            topbarLogoutBtn.disabled = true;
            try {
                await fetch('/api/logout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({})
                });
            } catch (e) {}
            window.location.href = '/';
        });
    }
}

const workspaceEl = document.getElementById('workspace');
const leftPanelEl = document.getElementById('content-panel');
const chatPanelEl = document.getElementById('chat-panel');
const splitDividerEl = document.getElementById('split-divider');
const chatToggleBtn = document.getElementById('chat-toggle-btn');
const chatCloseBtn = document.getElementById('chat-close-btn');
const logoutButton = document.getElementById('logout-btn');

if (logoutButton) {
    logoutButton.addEventListener('click', async () => {
        logoutButton.disabled = true;
        try {
            // Next login's restored hypotheses should force the Hypothesis
            // Agent Chat open (plain refresh restores keep their state).
            sessionStorage.setItem(FORCE_CHAT_OPEN_ON_RESTORE_KEY, '1');
        } catch (err) {
            // Ignore storage errors.
        }
        try {
            await fetch('/api/logout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({})
            });
        } catch (error) {
            // Swallow network errors; we'll still force a redirect.
        } finally {
            window.location.href = '/';
        }
    });
}

function applySplitWidths(percent = currentSplitPercent, { persistState = false } = {}) {
    if (!leftPanelEl || !chatPanelEl) return;
    if (auditOnly && workspaceEl && workspaceEl.classList.contains('audit-only-chat')) {
        chatPanelEl.style.flexBasis = '100%';
        return;
    }
    currentSplitPercent = clampMainSplitPercent(percent);
    if (chatCollapsed) {
        leftPanelEl.style.flexBasis = '100%';
        if (persistState) {
            queuePersistHypothesisChatState();
        }
        return;
    }
    leftPanelEl.style.flexBasis = `calc(${currentSplitPercent}% - 1px)`;
    chatPanelEl.style.flexBasis = `calc(${100 - currentSplitPercent}% - 1px)`;
    if (persistState) {
        queuePersistHypothesisChatState();
    }
}

function mountChatPanelIfNeeded() {
    if (chatPanelMounted) return;
    if (!chatPanelEl || !splitDividerEl) return;
    chatPanelEl.classList.remove('hidden');
    splitDividerEl.classList.remove('hidden');
    chatPanelMounted = true;
}

function setChatPanelCollapsed(
    shouldCollapse,
    { persistState = true, restoreScroll = false, skipContextLoad = false } = {}
) {
    if (!chatPanelEl || !leftPanelEl || !splitDividerEl) return;
    chatCollapsed = shouldCollapse;

    if (shouldCollapse) {
        chatPanelEl.classList.add('collapsed');
        splitDividerEl.classList.add('collapsed');
        leftPanelEl.style.flexBasis = '100%';
    } else {
        mountChatPanelIfNeeded();
        chatPanelEl.classList.remove('collapsed');
        splitDividerEl.classList.remove('collapsed');
        applySplitWidths(currentSplitPercent || 50);
        ensureChatGreeting();
        if (!skipContextLoad) {
            loadChatContext(chatContextCache === null);
        }
        const messagesEl = document.getElementById('chat-messages');
        if (restoreScroll && messagesEl) {
            const scrollTarget = Number.isFinite(chatPendingScrollTop)
                ? chatPendingScrollTop
                : messagesEl.scrollHeight;
            window.requestAnimationFrame(() => {
                messagesEl.scrollTop = scrollTarget;
            });
            chatPendingScrollTop = null;
        }
    }

    if (chatToggleBtn) {
        chatToggleBtn.classList.toggle('active', !shouldCollapse);
        chatToggleBtn.setAttribute('aria-pressed', (!shouldCollapse).toString());
    }
    if (persistState) {
        persistHypothesisChatState();
    }
}

function toggleChatPanelState() {
    setChatPanelCollapsed(!chatCollapsed);
}

// Forces the Hypothesis Agent Chat split-view open after hypotheses render.
// Called only where an explicit open is wanted: fresh generation /
// regenerate-all success, and restored hypotheses right after a
// logout -> login transition (see the sessionStorage flag below). Ordinary
// refresh restores intentionally do NOT call this --
// hydrateHypothesisChatStateFromStorage() already reinstates the user's
// persisted open/closed choice there. Reuses setChatPanelCollapsed(false)
// (the exact path the toggle/close buttons use), which also persists the
// opened state to localStorage; deferred one animation frame so it runs
// after the synchronous reset/hydrate/render churn in the same tick and has
// the last word for that frame. The user can still close the panel after.
function forceOpenHypothesisAgentChatAfterRender() {
    if (auditOnly) return;
    if (!currentHypotheses || currentHypotheses.length === 0) return;
    requestAnimationFrame(() => {
        if (auditOnly || !currentHypotheses || currentHypotheses.length === 0) return;
        setChatPanelCollapsed(false);
    });
}

// Set at logout so the NEXT login's restored hypotheses force the chat open.
// A plain browser refresh never sets it, which is exactly what distinguishes
// the two restore flavors: refresh keeps the persisted panel state,
// logout -> login forces split view. sessionStorage is per-tab and survives
// the logout -> login -> /app navigations; consumed (removed) the first time
// a successful initialization checks it so it can never linger.
const FORCE_CHAT_OPEN_ON_RESTORE_KEY = 'da_force_hypothesis_chat_open_on_restore';

function consumeForceChatOpenOnRestoreFlag() {
    try {
        if (sessionStorage.getItem(FORCE_CHAT_OPEN_ON_RESTORE_KEY) === '1') {
            sessionStorage.removeItem(FORCE_CHAT_OPEN_ON_RESTORE_KEY);
            return true;
        }
    } catch (err) {
        // Ignore storage errors.
    }
    return false;
}

function beginDividerDrag(event) {
    if (!splitDividerEl || !leftPanelEl || !workspaceEl || chatCollapsed) {
        return;
    }
    isDraggingDivider = true;
    dragStartX = event.clientX;
    dragStartLeftWidth = leftPanelEl.getBoundingClientRect().width;
    workspaceWidth = workspaceEl.getBoundingClientRect().width;
    splitDividerEl.classList.add('dragging');
    document.body.classList.add('no-select');
}

function handleDividerDrag(event) {
    if (!isDraggingDivider || !workspaceWidth) return;
    const delta = event.clientX - dragStartX;
    let newLeftPercent = ((dragStartLeftWidth + delta) / workspaceWidth) * 100;
    newLeftPercent = Math.max(MIN_LEFT_PERCENT, Math.min(MAX_LEFT_PERCENT, newLeftPercent));
    applySplitWidths(newLeftPercent);
}

function stopDividerDrag() {
    if (!isDraggingDivider) return;
    isDraggingDivider = false;
    document.body.classList.remove('no-select');
    if (splitDividerEl) {
        splitDividerEl.classList.remove('dragging');
    }
    queuePersistHypothesisChatState(0);
}

if (splitDividerEl) {
    splitDividerEl.addEventListener('mousedown', beginDividerDrag);
}
window.addEventListener('mousemove', handleDividerDrag);
window.addEventListener('mouseup', stopDividerDrag);

// Show full dataset or task description
function showFullDescription(type) {
    const content = type === 'dataset' ? fullDatasetDescription : fullTaskDescription;
    const title = type === 'dataset' ? 'Dataset Description' : 'Task Description';

    document.getElementById('modal-title').textContent = title;
    const reportContent = document.getElementById('modal-report-content');
    reportContent.innerHTML = formatMarkdown(escapeHtml(content));
    reportContent.style.whiteSpace = 'normal';
    applyCodeHighlight(reportContent);
    showModal('report-modal');
}

// README preview handling
function renderReadmePreview() {
    const preview = document.getElementById('readme-preview');
    const toggleBtn = document.getElementById('readme-toggle-btn');
    const fullBtn = document.getElementById('readme-full-btn');
    if (!preview) return;

    const maxLength = 600;
    const isLong = readmeContent && readmeContent.length > maxLength;
    const text = (!isLong || readmeExpanded)
        ? readmeContent
        : `${readmeContent.substring(0, maxLength)}...`;

    preview.innerHTML = formatMarkdown(escapeHtml(text));
    applyCodeHighlight(preview);
    preview.classList.toggle('collapsed', isLong && !readmeExpanded);

    if (toggleBtn) {
        toggleBtn.classList.toggle('hidden', !isLong);
        toggleBtn.textContent = readmeExpanded ? 'Collapse' : 'Expand';
    }
    if (fullBtn) {
        fullBtn.classList.toggle('hidden', !readmeContent);
    }
}

async function loadReadmePreview() {
    const preview = document.getElementById('readme-preview');
    const toggleBtn = document.getElementById('readme-toggle-btn');
    const fullBtn = document.getElementById('readme-full-btn');
    if (!preview) return;

    preview.textContent = 'Loading dataset README...';

    try {
        const config = window.serverConfig || {};
        const readmePath = config.readmePath || getInputValue('readme-path') || null;
        const readmeUrl = readmePath ? `/api/readme?path=${encodeURIComponent(readmePath)}` : '/api/readme';
        const result = await apiCall(readmeUrl);
        if (!result.success || !result.content) {
            preview.textContent = result.error || 'README unavailable.';
            if (toggleBtn) toggleBtn.classList.add('hidden');
            if (fullBtn) fullBtn.classList.add('hidden');
            return;
        }

        readmeContent = result.content;
        readmeExpanded = false;
        renderReadmePreview();
    } catch (err) {
        console.error('Failed to load README', err);
        preview.textContent = 'Failed to load README.';
        if (toggleBtn) toggleBtn.classList.add('hidden');
        if (fullBtn) fullBtn.classList.add('hidden');
    }
}

function initGithubButton(urlFromConfig) {
    const btn = document.getElementById('github-button');
    githubUrl = urlFromConfig || '';
    if (!btn) return;

    const hasUrl = !!githubUrl;
    btn.disabled = !hasUrl;
    btn.title = hasUrl ? 'Open project codebase' : 'Codebase unavailable';
    btn.setAttribute('aria-disabled', hasUrl ? 'false' : 'true');

    if (!btn.dataset.bound) {
        btn.addEventListener('click', () => {
            if (!githubUrl) return;
            window.open(githubUrl, '_blank', 'noopener');
        });
        btn.dataset.bound = 'true';
    }
}

// API calls
async function apiCall(endpoint, data = null) {
    const options = {
        method: data ? 'POST' : 'GET',
        headers: {
            'Content-Type': 'application/json',
        }
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    const response = await fetch(endpoint, options);
    return await response.json();
}

async function apiCallWithSignal(endpoint, data = null, signal = null) {
    const options = {
        method: data ? 'POST' : 'GET',
        headers: {
            'Content-Type': 'application/json',
        },
        signal
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    const response = await fetch(endpoint, options);
    return await response.json();
}

function isMissingTaskDescription(result) {
    return result && (result.code === 'missing_task_description' || result.error === 'missing_task_description');
}

function currentTaskDescription() {
    const configured = window.serverConfig && typeof window.serverConfig.taskDescription === 'string'
        ? window.serverConfig.taskDescription
        : '';
    return (fullTaskDescription || configured || '').trim();
}

function openObjectiveDialog(continuation) {
    pendingObjectiveContinuation = typeof continuation === 'function' ? continuation : null;
    const modal = document.getElementById('objective-modal');
    const input = document.getElementById('objective-input');
    const status = document.getElementById('objective-status');
    if (!modal || !input) {
        showStatus('generate-status', 'Define a research objective before generating hypotheses.', 'error');
        return;
    }
    if (status) {
        status.classList.add('hidden');
        status.textContent = '';
    }
    input.value = currentTaskDescription();
    modal.classList.remove('hidden');
    setTimeout(() => input.focus(), 30);
}

function closeObjectiveDialog(clearContinuation = true) {
    const modal = document.getElementById('objective-modal');
    if (modal) {
        modal.classList.add('hidden');
    }
    if (clearContinuation) {
        pendingObjectiveContinuation = null;
    }
}

function showObjectiveStatus(message, type = 'error') {
    const status = document.getElementById('objective-status');
    if (!status) return;
    status.textContent = message;
    status.className = `status-message ${type}`;
    status.classList.remove('hidden');
}

async function saveObjectiveAndContinue(event) {
    if (event) event.preventDefault();
    if (objectiveSaveInFlight) return;
    const input = document.getElementById('objective-input');
    const submit = document.getElementById('objective-submit-btn');
    const objective = input ? input.value.trim().replace(/\s+/g, ' ') : '';
    if (!objective) {
        showObjectiveStatus('Enter a research objective before continuing.');
        if (input) input.focus();
        return;
    }

    objectiveSaveInFlight = true;
    if (submit) submit.disabled = true;
    try {
        const result = await apiCall('/api/project_objective', { task_description: objective });
        if (!result.success) {
            showObjectiveStatus(result.message || result.error || 'Could not save the research objective.');
            return;
        }
        fullTaskDescription = result.task_description || objective;
        if (window.serverConfig) {
            window.serverConfig.taskDescription = fullTaskDescription;
        }
        chatContextCache = null;
        const continuation = pendingObjectiveContinuation;
        closeObjectiveDialog(false);
        showStatus('generate-status', 'Research objective saved. Continuing...', 'success');
        pendingObjectiveContinuation = null;
        if (continuation) {
            setTimeout(() => continuation(), 0);
        }
    } catch (error) {
        showObjectiveStatus(`Error: ${error.message}`);
    } finally {
        objectiveSaveInFlight = false;
        if (submit) submit.disabled = false;
    }
}

function getInputValue(id) {
    const el = document.getElementById(id);
    if (!el) return '';
    if (el.type === 'checkbox') {
        return el.checked;
    }
    return (el.value || '').trim();
}

function collectInitPayload() {
    const config = window.serverConfig || {};
    const checkpointDir = config.checkpointDir || getInputValue('checkpoint-dir');
    // Always respect the user’s current selection; fall back to server defaults only if empty
    const hypothesisModel = getInputValue('hypothesis-model') || config.hypothesisModel || null;
    const codeDir = config.codeDir || null;
    const readmePath = config.readmePath || getInputValue('readme-path') || null;
    const includeBackgroundLiterature = (typeof config.includeBackgroundLiterature === 'boolean')
        ? config.includeBackgroundLiterature
        : (() => {
            const el = document.getElementById('include-background-literature');
            if (!el) return !config.devMode;
            return el.type === 'hidden' ? el.value === 'true' : el.checked;
        })();
    const generatePlan = (typeof config.generatePlan === 'boolean')
        ? config.generatePlan
        : (() => {
            const el = document.getElementById('generate-plan');
            if (!el) return !config.devMode;
            return el.type === 'hidden' ? el.value === 'true' : el.checked;
        })();
    const enableCitations = (typeof config.enableCitations === 'boolean')
        ? config.enableCitations
        : (() => {
            const el = document.getElementById('enable-citations');
            if (!el) return false;
            return el.type === 'hidden' ? el.value === 'true' : el.checked;
        })();
    const cacheDocuments = (typeof config.cacheDocuments === 'boolean') ? config.cacheDocuments : false;

    let numHypotheses = null;
    if (config.numHypotheses) {
        numHypotheses = config.numHypotheses;
    } else {
        const numInput = document.getElementById('num-hypotheses');
        if (numInput && numInput.value) {
            const parsed = parseInt(numInput.value, 10);
            if (!isNaN(parsed)) {
                numHypotheses = Math.min(10, Math.max(1, parsed));
                numInput.value = numHypotheses;
            }
        }
    }

    return {
        checkpoint_dir: checkpointDir,
        hypothesis_model: hypothesisModel || null,
        code_dir: codeDir,
        include_background_literature: includeBackgroundLiterature,
        generate_plan: generatePlan,
        num_hypotheses: numHypotheses,
        readme_path: readmePath || null,
        enable_citations: enableCitations,
        cache_documents: cacheDocuments,
    };
}

function handleInitializationSuccess(result) {
    generatePlanEnabled = !!result.generate_plan;
    citationsEnabled = !!result.enable_citations;
    updateHistoryScope({
        sessionScope: result.session_debug_id || null,
        generationId: Number.isInteger(result.generation_id) ? result.generation_id : null
    });
    projectSubmissionCount = Number.isInteger(result.project_submission_count) ? result.project_submission_count : 0;
    projectRound = Number.isInteger(result.project_round) ? result.project_round : 1;
    // Store full descriptions
    fullDatasetDescription = result.dataset_description;
    fullTaskDescription = result.task_description;

    const roundBanner = document.getElementById('research-round-banner');
    if (roundBanner) {
        roundBanner.textContent = `Research Round ${projectRound}`;
        roundBanner.classList.remove('hidden');
    }

    // Store round report availability
    hasRoundReport = result.has_round_report;
    if (!auditOnly && hasRoundReport) {
        const roundBtn = document.getElementById('view-round-context-btn');
        if (roundBtn) {
            roundBtn.classList.remove('hidden');
        }
    }

    // Background literature availability
    hasBackgroundLiterature = !!(result.include_background_literature && result.has_background_literature);
    if (auditOnly) {
        hasBackgroundLiterature = false;
    }
    const bgLitBtn = document.getElementById('view-background-literature-btn');
    if (bgLitBtn) {
        bgLitBtn.classList.toggle('hidden', !hasBackgroundLiterature);
    }

    // Research paper availability (renders on dedicated page)
    hasResearchPaper = !!result.has_research_paper;

    // Code chat availability
    codeChatAvailable = !!result.code_chat_available;
    updateCodeModeAvailability();

    const userInstructions = document.getElementById('user-instructions');
    if (userInstructions) {
        if (!auditOnly) {
            userInstructions.classList.remove('hidden');
        }
    }
    const reportsSection = document.getElementById('reports-section');
    if (reportsSection) {
        if (!auditOnly) {
            reportsSection.classList.remove('hidden');
        }
    }

    if (auditOnly) {
        ensureChatGreeting();
        loadChatContext(true);
    }

    resetChatPanel({ persistState: false });
    hydrateHypothesisChatStateFromStorage();

    // Consumed on every successful init so it can't linger: it is only ever
    // truthy on the first init after a logout -> login transition.
    const forceChatOpenAfterLogin = consumeForceChatOpenOnRestoreFlag();
    if (Array.isArray(result.restored_hypotheses) && result.restored_hypotheses.length > 0) {
        applyRestoredHypotheses(result.restored_hypotheses, { forceChatOpen: forceChatOpenAfterLogin });
    }
}

// Restore a previously generated (but not yet selected) hypothesis set after
// login/session restart. Reuses the same rendering path as a fresh /api/generate
// success so there is exactly one place that knows how to draw hypothesis cards.
function applyRestoredHypotheses(hypotheses, { forceChatOpen = false } = {}) {
    currentHypotheses = hypotheses;
    lastCitations = [];
    updateCitationDocuments([]);
    displayHypotheses(hypotheses, lastCitations, citationDocuments);
    updateGenerateButtonState(true);
    if (!shouldForceHypothesesLanding()) {
        markHypothesesLandingReady();
    }

    const hypothesesSection = document.getElementById('hypotheses-section');
    if (hypothesesSection) hypothesesSection.classList.remove('hidden');
    if (workspaceEl) workspaceEl.classList.remove('hidden');
    applySplitWidths();
    if (chatToggleBtn) chatToggleBtn.classList.remove('hidden');
    const githubBtn = document.getElementById('github-button');
    if (githubBtn) githubBtn.classList.remove('hidden');
    // Force split view only for the login-restore flavor; a plain refresh
    // keeps whatever open/closed state hydrate...FromStorage() reinstated.
    if (forceChatOpen) {
        forceOpenHypothesisAgentChatAfterRender();
    }

    chatContextCache = null;
    loadChatContext(true);

    const initialGuidanceCard = document.getElementById('initial-guidance-card');
    if (initialGuidanceCard) {
        initialGuidanceCard.classList.add('hidden');
    }

    const countBadge = document.getElementById('generation-count-badge');
    if (countBadge) {
        countBadge.textContent = `Using ${hypotheses.length} hypotheses`;
        countBadge.classList.remove('hidden');
    }
}

async function initializeSession(statusTarget = 'init-status') {
    if (sessionInitialized) return true;
    if (initPromise) {
        await initPromise;
        return sessionInitialized;
    }

    const revealStatusTarget = () => {
        if (statusTarget === 'init-status') {
            const userInstructions = document.getElementById('user-instructions');
            if (userInstructions) {
                if (!auditOnly) {
                    userInstructions.classList.remove('hidden');
                }
            }
            const reportsSection = document.getElementById('reports-section');
            if (reportsSection) {
                if (!auditOnly) {
                    reportsSection.classList.remove('hidden');
                }
            }
        }
    };

    const payload = collectInitPayload();
    if (!payload.checkpoint_dir) {
        revealStatusTarget();
        showStatus(statusTarget, 'Checkpoint directory is required', 'error');
        return false;
    }

    initializingSession = true;
    revealStatusTarget();
    hideStatus(statusTarget);
    showLoading('Initializing session...');
    hasResearchPaper = false;

    initPromise = (async () => {
        try {
            const result = await apiCall('/api/initialize', payload);
            if (result.success) {
                if (result.redirect_to_round_monitor) {
                    // Canonical /projects/<slug>/rounds/<n> when a V2 project
                    // is active; falls back to the generic route otherwise.
                    window.location.href = result.round_workspace_url || '/round_monitor';
                    return true;
                }
                handleInitializationSuccess(result);
                sessionInitialized = true;
                showStatus(statusTarget, 'Session initialized successfully!', 'success');
                return true;
            }
            showStatus(statusTarget, `Error: ${result.error}`, 'error');
            return false;
        } catch (error) {
            showStatus(statusTarget, `Error: ${error.message}`, 'error');
            return false;
        } finally {
            initializingSession = false;
            initPromise = null;
            hideLoading();
        }
    })();

    const success = await initPromise;
    return success;
}

// Generate hypotheses
const generateBtn = document.getElementById('generate-btn');
const regenConfirmOverlay = document.getElementById('regen-confirm-overlay');
const regenConfirmBtn = document.getElementById('regen-confirm-btn');
const regenCancelBtn = document.getElementById('regen-cancel-btn');

function updateGenerateButtonState(hasHypotheses) {
    if (!generateBtn) return;
    generateBtn.textContent = hasHypotheses ? 'Regenerate All Hypotheses' : 'Initialize Workspace';
}

function showRegenerateConfirm() {
    if (!regenConfirmOverlay) return;
    regenConfirmOverlay.classList.remove('hidden');
}

function hideRegenerateConfirm() {
    if (!regenConfirmOverlay) return;
    regenConfirmOverlay.classList.add('hidden');
}

if (generateBtn) {
    updateGenerateButtonState(false);
    generateBtn.addEventListener('click', async () => {
        if (auditOnly) {
            showStatus('generate-status', 'Audit-only mode does not allow hypothesis generation.', 'error');
            return;
        }
        if (currentHypotheses.length > 0) {
            showRegenerateConfirm();
            return;
        }
        await generateHypotheses();
    });
}
if (regenCancelBtn) {
    regenCancelBtn.addEventListener('click', () => {
        const guidanceEl = document.getElementById('regen-guidance');
        if (guidanceEl) {
            guidanceEl.value = '';
        }
        hideRegenerateConfirm();
    });
}
if (regenConfirmBtn) {
    regenConfirmBtn.addEventListener('click', async () => {
        const guidanceEl = document.getElementById('regen-guidance');
        const guidance = guidanceEl ? guidanceEl.value : '';
        if (guidanceEl) {
            guidanceEl.value = '';
        }
        hideRegenerateConfirm();
        await generateHypotheses({ guidance });
    });
}

async function cancelHypothesisGeneration() {
    if (!generationInProgress) return;
    generationCancelRequested = true;
    if (generationAbortController) {
        generationAbortController.abort();
    }
    try {
        await apiCall('/api/cancel_generation', {});
    } catch (error) {
        // Ignore network errors during cancellation.
    }
    generationInProgress = false;
    generationAbortController = null;
    hideLoading();

    // Hide the section if there are no hypotheses to show (covers both initial
    // generation and regeneration, since regeneration clears currentHypotheses
    // before the API call so stale hypotheses cannot be restored).
    if (currentHypotheses.length === 0) {
        const hypothesesSection = document.getElementById('hypotheses-section');
        if (hypothesesSection) {
            hypothesesSection.classList.add('hidden');
        }
        if (workspaceEl) {
            workspaceEl.classList.add('hidden');
        }
        if (chatToggleBtn) {
            chatToggleBtn.classList.add('hidden');
        }
        const githubBtn = document.getElementById('github-button');
        if (githubBtn) {
            githubBtn.classList.add('hidden');
        }
    } else {
        const hypothesesSection = document.getElementById('hypotheses-section');
        if (hypothesesSection) {
            hypothesesSection.classList.remove('hidden');
        }
        if (workspaceEl) {
            workspaceEl.classList.remove('hidden');
        }
        if (chatToggleBtn) {
            chatToggleBtn.classList.remove('hidden');
        }
        const githubBtn = document.getElementById('github-button');
        if (githubBtn) {
            githubBtn.classList.remove('hidden');
        }
    }

    applySplitWidths();
    showStatus('generate-status', 'Generation cancelled.', 'error');
}

async function generateHypotheses(options = {}) {
    if (auditOnly) {
        showStatus('generate-status', 'Audit-only mode does not allow hypothesis generation.', 'error');
        return;
    }
    const ready = await initializeSession('generate-status');
    if (!ready) return;

    if (!currentTaskDescription()) {
        openObjectiveDialog(() => generateHypotheses(options));
        return;
    }

    invalidateInteractiveRequests('generate_hypotheses_start');
    bumpUiStateVersion('generate_hypotheses_start');
    generationOrigin = currentHypotheses.length > 0 ? 'regenerate' : 'initial';
    generationCancelRequested = false;
    generationAbortController = new AbortController();
    generationInProgress = true;

    // On regeneration, clear all frontend state before the API call so the old
    // hypotheses cannot reappear after a failed generation or page refresh.
    if (generationOrigin === 'regenerate') {
        currentHypotheses = [];
        selectedHypothesisIndex = null;
        editingHypothesis = null;
        clearAllHypothesisChatStates();
        clearChatMessagesUi({ persistState: false });
        updateGenerateButtonState(false);
        const hypothesesSection = document.getElementById('hypotheses-section');
        if (hypothesesSection) hypothesesSection.classList.add('hidden');
        if (workspaceEl) workspaceEl.classList.add('hidden');
        if (chatToggleBtn) chatToggleBtn.classList.add('hidden');
        const githubBtnRegen = document.getElementById('github-button');
        if (githubBtnRegen) githubBtnRegen.classList.add('hidden');
    }

    showLoading('Generating hypotheses... This may take a few minutes.', {
        cancelable: true,
        cancelLabel: 'Cancel',
        onCancel: cancelHypothesisGeneration
    });
    hideStatus('generate-status');

    let requestedNumHypotheses = null;
    let guidance = typeof options.guidance === 'string' ? options.guidance.trim() : '';
    if (!guidance && generationOrigin === 'initial') {
        const initialGuidanceEl = document.getElementById('initial-guidance');
        if (initialGuidanceEl) {
            guidance = initialGuidanceEl.value.trim();
        }
    }
    const numInput = document.getElementById('num-hypotheses');
    if (numInput && numInput.value) {
        const parsed = parseInt(numInput.value, 10);
        if (!isNaN(parsed)) {
            requestedNumHypotheses = Math.min(10, Math.max(1, parsed));
            numInput.value = requestedNumHypotheses;
        }
    }

    try {
        const payload = requestedNumHypotheses ? { num_hypotheses: requestedNumHypotheses } : {};
        if (guidance) {
            payload.guidance = guidance;
        }

        const result = await apiCallWithSignal('/api/generate', payload, generationAbortController.signal);

        if (result.cancelled) {
            cancelHypothesisGeneration();
            return;
        }

        if (result.success) {
            updateHistoryScope({
                sessionScope: result.session_debug_id || null,
                generationId: Number.isInteger(result.generation_id) ? result.generation_id : null
            });
            currentHypotheses = result.hypotheses;
            bumpUiStateVersion('generate_hypotheses_success');
            cancelChatRequest('generate_hypotheses_success');

            // Store citations from generation
            lastCitations = result.citations || [];
            updateCitationDocuments(result.documents);

            displayHypotheses(result.hypotheses, lastCitations, citationDocuments);
            updateGenerateButtonState(true);
            if (!shouldForceHypothesesLanding()) {
                markHypothesesLandingReady();
            }

            // Show hypotheses section
            document.getElementById('hypotheses-section').classList.remove('hidden');
            if (workspaceEl) {
                workspaceEl.classList.remove('hidden');
            }
            // Ensure correct panel widths (full width when chat is collapsed)
            applySplitWidths();
            if (chatToggleBtn) {
                chatToggleBtn.classList.remove('hidden');
            }
            const githubBtn = document.getElementById('github-button');
            if (githubBtn) {
                githubBtn.classList.remove('hidden');
            }
            forceOpenHypothesisAgentChatAfterRender();

            if (generationOrigin === 'initial') {
                const initialGuidanceEl = document.getElementById('initial-guidance');
                if (initialGuidanceEl) {
                    initialGuidanceEl.value = '';
                }
                const initialGuidanceCard = document.getElementById('initial-guidance-card');
                if (initialGuidanceCard) {
                    initialGuidanceCard.classList.add('hidden');
                }
            }

            // Reset chat context so the sidebar refreshes with the new hypotheses
            chatContextCache = null;
            loadChatContext(true);

            if (generationOrigin === 'regenerate') {
                clearAllHypothesisChatStates();
                clearChatMessagesUi({ persistState: false });
                ensureChatGreeting();
                persistHypothesisChatState();
            }

            const citationNote = lastCitations.length > 0 ? ` with citations` : '';
            showStatus('generate-status', `Generated ${result.count} hypotheses successfully${citationNote}!`, 'success');

            const countBadge = document.getElementById('generation-count-badge');
            if (countBadge) {
                const count = Number.isInteger(result.count) ? result.count : currentHypotheses.length;
                countBadge.textContent = `Using ${count} hypotheses`;
                countBadge.classList.remove('hidden');
            }
        } else if (isMissingTaskDescription(result)) {
            openObjectiveDialog(() => generateHypotheses(options));
        } else {
            showStatus('generate-status', `Error: ${result.error}`, 'error');
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            if (!generationCancelRequested) {
                showStatus('generate-status', 'Generation cancelled.', 'error');
            }
            return;
        }
        showStatus('generate-status', `Error: ${error.message}`, 'error');
    } finally {
        generationInProgress = false;
        generationAbortController = null;
        hideLoading();
    }
}

// Build hypothesis display text without Title section
function buildHypothesisDisplayText(hyp) {
    // Start with full text and remove the Title section
    let displayText = hyp.full_text || '';

    // Remove Title section using regex - matches "**Title**:" or "Title:" followed by content until next section
    displayText = displayText.replace(/^\s*\*?\*?Title\*?\*?:.*?(?=\n\s*\*?\*?(?:Hypothesis|Justification|Interpretation|Plan)\*?\*?:|$)/is, '');

    // Clean up any leading whitespace
    displayText = displayText.trim();

    return displayText;
}

// ============================================================================
// Hypothesis Edit History Management
// ============================================================================

function initializeHistory(hypothesis) {
    const stored = loadHistoryFromStorage(hypothesis.index);
    if (stored && isStoredHistoryCompatible(stored, hypothesis)) {
        hypothesisHistory = stored.history.map(item => JSON.parse(JSON.stringify(item)));
        historyIndex = stored.index;
    } else {
        hypothesisHistory = [JSON.parse(JSON.stringify(hypothesis))];
        historyIndex = 0;
        saveHistoryToStorage(hypothesis.index);
    }
    updateHistoryUI();
    return JSON.parse(JSON.stringify(hypothesisHistory[historyIndex]));
}

function resetHistoryToHypothesis(hypothesis) {
    if (!hypothesis || hypothesis.index === null || hypothesis.index === undefined) {
        return;
    }
    hypothesisHistory = [JSON.parse(JSON.stringify(hypothesis))];
    historyIndex = 0;
    saveHistoryToStorage(hypothesis.index);
    updateHistoryUI();
}

function addToHistory(hypothesis) {
    // If we're not at the end of history, truncate future history
    if (historyIndex < hypothesisHistory.length - 1) {
        hypothesisHistory = hypothesisHistory.slice(0, historyIndex + 1);
    }
    // Add new version
    hypothesisHistory.push(JSON.parse(JSON.stringify(hypothesis)));
    historyIndex = hypothesisHistory.length - 1;
    saveHistoryToStorage(hypothesis.index);
    updateHistoryUI();
}

function navigateHistory(direction) {
    const newIndex = historyIndex + direction;
    if (newIndex < 0 || newIndex >= hypothesisHistory.length) {
        return;
    }
    historyIndex = newIndex;
    saveHistoryToStorage(editingHypothesis ? editingHypothesis.index : null);

    // Update editingHypothesis to the historical version
    editingHypothesis = JSON.parse(JSON.stringify(hypothesisHistory[historyIndex]));

    // Update the display
    updateEditorDisplay();
    updateHistoryUI();

    // If in manual edit mode, update the textarea
    if (currentEditType === 'manual') {
        const textarea = document.getElementById('manual-edit-textarea');
        if (textarea) {
            textarea.value = editingHypothesis.full_text || '';
        }
    }
}

function updateHistoryUI() {
    const backBtn = document.getElementById('history-back-btn');
    const forwardBtn = document.getElementById('history-forward-btn');
    const indicator = document.getElementById('history-indicator');

    if (backBtn) {
        backBtn.disabled = historyIndex <= 0;
    }
    if (forwardBtn) {
        forwardBtn.disabled = historyIndex >= hypothesisHistory.length - 1;
    }
    if (indicator) {
        indicator.textContent = `${historyIndex + 1} / ${hypothesisHistory.length}`;
    }
}

function updateEditorDisplay() {
    if (!editingHypothesis) return;

    // Update title
    const titleSuffix = editingHypothesis.title ? `: ${escapeHtml(editingHypothesis.title)}` : '';
    const titleEl = document.getElementById('editor-hypothesis-title');
    if (titleEl) {
        titleEl.textContent = `Hypothesis ${editingHypothesis.index}${titleSuffix}`;
    }

    // Update content with citation markers if available
    const contentEl = document.getElementById('editor-hypothesis-text');
    if (contentEl) {
        let formattedText = formatMarkdown(editingHypothesis.full_text || 'N/A');
        if (lastCitations && lastCitations.length > 0) {
            formattedText = annotateTextWithCitations(formattedText, lastCitations);
        }
        contentEl.innerHTML = formattedText;
    }
}

function getCanonicalHypothesisByIndex(index) {
    if (index === null || index === undefined) {
        return null;
    }
    return currentHypotheses.find((hyp) => String(hyp.index) === String(index)) || null;
}

function getEffectiveEditingHypothesis() {
    if (!editingHypothesis) {
        return null;
    }
    if (editingHypothesis.localEdits === true) {
        return editingHypothesis;
    }
    return getCanonicalHypothesisByIndex(editingHypothesis.index) || editingHypothesis;
}

// ============================================================================
// Edit Mode Toggle (LLM Chat vs Manual Edit)
// ============================================================================

function getEditModeLabel(type) {
    if (type === 'llm_edit') return 'Edit with LLM Chat';
    if (type === 'llm_ask') return 'Ask with LLM Chat';
    return 'Manual Edit';
}

function wrapEditModeMessage(type, message) {
    return `Mode: ${getEditModeLabel(type)}\n\nUser query: ${message}`;
}

function updateEditChatUi(type) {
    const chatPanelTitle = document.getElementById('chat-panel-title');
    const chatPanelSubtitle = document.getElementById('chat-panel-subtitle');
    const chatInput = document.getElementById('chat-input');

    if (chatPanelTitle) {
        chatPanelTitle.textContent = getEditModeLabel(type);
    }

    if (type === 'llm_ask') {
        if (chatPanelSubtitle) {
            chatPanelSubtitle.textContent = 'Ask questions about this hypothesis. No edits will be applied.';
        }
        if (chatInput) {
            chatInput.placeholder = 'Ask a question about this hypothesis...';
        }
    } else {
        if (chatPanelSubtitle) {
            chatPanelSubtitle.textContent = 'Refine your hypothesis through conversation. Changes will be reflected in real-time.';
        }
        if (chatInput) {
            chatInput.placeholder = 'Ask for changes to the hypothesis...';
        }
    }
}

function setEditType(type) {
    currentEditType = type;

    const llmEditBtn = document.getElementById('edit-mode-llm-edit-btn');
    const llmAskBtn = document.getElementById('edit-mode-llm-ask-btn');
    const manualBtn = document.getElementById('edit-mode-manual-btn');
    const manualContainer = document.getElementById('manual-edit-container');
    const manualStatus = document.getElementById('manual-edit-status');
    const chatPanel = document.getElementById('chat-panel');
    const splitDivider = document.getElementById('split-divider');

    if (type === 'llm_edit' || type === 'llm_ask') {
        // Show LLM chat mode
        if (llmEditBtn) llmEditBtn.classList.toggle('active', type === 'llm_edit');
        if (llmAskBtn) llmAskBtn.classList.toggle('active', type === 'llm_ask');
        if (manualBtn) manualBtn.classList.remove('active');
        if (manualContainer) manualContainer.classList.add('hidden');

        // Show chat panel
        if (chatPanel) {
            chatPanel.classList.remove('hidden');
            chatPanelMounted = true;
            chatCollapsed = false;
            chatPanel.classList.remove('collapsed');
        }
        if (splitDivider) {
            splitDivider.classList.remove('hidden', 'collapsed');
        }
        applySplitWidths(currentSplitPercent || 50);
        updateEditChatUi(type);
    } else {
        // Show manual edit mode
        if (llmEditBtn) llmEditBtn.classList.remove('active');
        if (llmAskBtn) llmAskBtn.classList.remove('active');
        if (manualBtn) manualBtn.classList.add('active');
        if (manualContainer) manualContainer.classList.remove('hidden');
        if (manualStatus) manualStatus.classList.add('hidden');

        // Populate textarea with current hypothesis
        const textarea = document.getElementById('manual-edit-textarea');
        if (textarea && editingHypothesis) {
            textarea.value = editingHypothesis.full_text || '';
        }

        // Hide chat panel
        if (chatPanel) {
            chatPanel.classList.add('hidden', 'collapsed');
        }
        if (splitDivider) {
            splitDivider.classList.add('hidden', 'collapsed');
        }
        // Give full width to left panel
        const leftPanel = document.getElementById('content-panel');
        if (leftPanel) {
            leftPanel.style.flexBasis = '100%';
        }
    }
}

function saveManualEdit() {
    const textarea = document.getElementById('manual-edit-textarea');
    if (!textarea || !editingHypothesis) return;

    const newText = textarea.value.trim();
    if (!newText) {
        alert('Hypothesis text cannot be empty.');
        return;
    }

    // Check if there are actual changes
    if (newText === editingHypothesis.full_text) {
        alert('No changes detected.');
        return;
    }

    // Re-parse sections from the updated text
    const sections = parseHypothesisSections(newText);
    const updatedHypothesis = {
        ...editingHypothesis,
        full_text: newText,
        localEdits: true,
        title: sections.title,
        hypothesis: sections.hypothesis,
        justification: sections.justification,
        interpretation: sections.interpretation,
        plan: sections.plan
    };

    // Update the hypothesis
    editingHypothesis = updatedHypothesis;

    // Update display
    updateEditorDisplay();

    // Add to history
    addToHistory(updatedHypothesis);

    // Update in currentHypotheses array
    const hypIndex = currentHypotheses.findIndex(h => h.index === editingHypothesis.index);
    if (hypIndex !== -1) {
        currentHypotheses[hypIndex] = { ...updatedHypothesis };
    }

    // Show confirmation
    showFormStatus('manual-edit-status', 'Your edits have been applied.', 'success');
}

function revertManualEdit() {
    const textarea = document.getElementById('manual-edit-textarea');
    if (!textarea || !editingHypothesis) return;

    // Revert to current version
    textarea.value = editingHypothesis.full_text || '';
}

// Parse hypothesis sections from text (client-side version)
function parseHypothesisSections(text) {
    const sections = {
        title: null,
        hypothesis: null,
        justification: null,
        interpretation: null,
        plan: null
    };

    if (!text) return sections;

    // Extract Title
    const titleMatch = text.match(/\*?\*?Title\*?\*?:\s*(.+?)(?=\n\s*\*?\*?(?:Hypothesis|Justification|Interpretation|Plan)\*?\*?:|$)/is);
    if (titleMatch) sections.title = titleMatch[1].trim();

    // Extract Hypothesis
    const hypMatch = text.match(/\*?\*?Hypothesis\*?\*?:\s*(.+?)(?=\n\s*\*?\*?(?:Title|Justification|Interpretation|Plan)\*?\*?:|$)/is);
    if (hypMatch) sections.hypothesis = hypMatch[1].trim();

    // Extract Justification
    const justMatch = text.match(/\*?\*?Justification\*?\*?:\s*(.+?)(?=\n\s*\*?\*?(?:Title|Hypothesis|Interpretation|Plan)\*?\*?:|$)/is);
    if (justMatch) sections.justification = justMatch[1].trim();

    // Extract Interpretation
    const interpMatch = text.match(/\*?\*?Interpretation\*?\*?:\s*(.+?)(?=\n\s*\*?\*?(?:Title|Hypothesis|Justification|Plan)\*?\*?:|$)/is);
    if (interpMatch) sections.interpretation = interpMatch[1].trim();

    // Extract Plan
    const planMatch = text.match(/\*?\*?Plan\*?\*?:\s*(.+?)$/is);
    if (planMatch) sections.plan = planMatch[1].trim();

    return sections;
}

// Display hypotheses
function displayHypotheses(hypotheses, citations = [], documents = []) {
    const container = document.getElementById('hypotheses-container');
    container.innerHTML = '';

    // Reset citation usage tracking
    citationUsageByHypothesis = {};

    console.log('displayHypotheses called with:', {
        numHypotheses: hypotheses.length,
        numCitations: citations.length,
        citations: citations
    });

    // Check if we have citations to annotate with
    const hasCitations = citations && citations.length > 0;

    // Pre-analyze which citations belong to which hypothesis based on raw text matching
    if (hasCitations) {
        buildAndCacheCitations(citations);
        console.log('Pre-analyzing citation-hypothesis associations...');

        for (const idx in citationDataMap) {
            const citInfo = citationDataMap[idx];
            if (!citInfo.blockText || citInfo.blockText.length < 10) continue;

            // Check which hypothesis contains this blockText
            hypotheses.forEach(hyp => {
                const rawText = buildHypothesisDisplayText(hyp);
                // Try to find the blockText in the raw hypothesis text
                // Use a cleaned version for matching
                const cleanBlockText = citInfo.blockText.replace(/\*\*/g, '').replace(/\*/g, '').trim();
                const cleanRawText = rawText.replace(/\*\*/g, '').replace(/\*/g, '');

                // Try different matching strategies
                const directMatch = cleanRawText.includes(cleanBlockText);
                const snippetMatch = cleanBlockText.length > 40 && cleanRawText.includes(cleanBlockText.slice(0, 40));

                if (directMatch || snippetMatch) {
                    if (!citationUsageByHypothesis[citInfo.index]) {
                        citationUsageByHypothesis[citInfo.index] = [];
                    }
                    if (!citationUsageByHypothesis[citInfo.index].includes(hyp.index)) {
                        citationUsageByHypothesis[citInfo.index].push(hyp.index);
                        console.log(`PRE-ANALYSIS: Citation [${citInfo.index}] found in Hypothesis ${hyp.index}`);
                    }
                }
            });
        }

        console.log('Pre-analysis complete. Usage map:', citationUsageByHypothesis);
    }

    hypotheses.forEach(hyp => {
        const card = document.createElement('div');
        card.className = 'hypothesis-card';
        card.id = `hypothesis-${hyp.index}`;

        const titleSuffix = hyp.title ? `: ${escapeHtml(hyp.title)}` : '';

        // Build display text without Title section
        const rawDisplayText = buildHypothesisDisplayText(hyp);

        // Check if this hypothesis is expanded
        const isExpanded = expandedHypotheses.has(hyp.index);
        const shouldTruncate = !isExpanded && rawDisplayText.length > 400;
        const textToShow = shouldTruncate ? truncateText(rawDisplayText, 400) : rawDisplayText;
        const buttonText = isExpanded ? 'Show Less' : 'View Full';

        // First format markdown, then add citation markers (so markers don't get mangled)
        let formattedText = formatMarkdown(textToShow);
        if (hasCitations) {
            formattedText = annotateTextWithCitations(formattedText, citations, hyp.index);
        }

        card.innerHTML = `
            <div class="hypothesis-main">
                <div class="hypothesis-title">Hypothesis ${hyp.index}${titleSuffix}</div>
                <div class="hypothesis-content">
                    <div class="hypothesis-section">
                        <div class="hypothesis-text" id="hypothesis-text-${hyp.index}">${formattedText}</div>
                    </div>
                </div>
            </div>
            <div class="hypothesis-actions-column">
                <button class="btn btn-small btn-primary" onclick="toggleHypothesisExpansion(${hyp.index})" id="expand-btn-${hyp.index}">
                    ${buttonText}
                </button>
                <button class="btn btn-small btn-secondary" onclick="openRegenerateModal(${hyp.index})">
                    Regenerate
                </button>
                <button class="btn btn-small btn-success" onclick="selectHypothesis(${hyp.index})">
                    Select or Edit
                </button>
            </div>
        `;

        container.appendChild(card);
    });

    // Render citations section after all hypotheses have been processed
    // (so citationUsageByHypothesis is fully populated)
    if (hasCitations) {
        console.log('Citation usage by hypothesis:', citationUsageByHypothesis);
        const citationsHtml = renderCitations(citations, documents, false, citationUsageByHypothesis);
        const citationsSection = document.createElement('div');
        citationsSection.className = 'card';
        citationsSection.style.marginTop = '20px';
        citationsSection.innerHTML = `
            <h3 style="margin-bottom: 12px;">Sources Referenced</h3>
            <p style="color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 12px;">
                These sources were cited by the AI when generating the hypotheses above. Underlined text indicates citations.
            </p>
            ${citationsHtml}
        `;
        container.appendChild(citationsSection);
    }

    // Add custom hypothesis button
    const customButton = document.createElement('button');
    customButton.className = 'btn btn-secondary btn-large';
    customButton.textContent = 'Enter Custom Hypothesis';
    customButton.style.marginTop = '20px';
    customButton.onclick = showCustomHypothesisModal;
    container.appendChild(customButton);
}

// Toggle hypothesis expansion
function toggleHypothesisExpansion(index) {
    const hypothesis = currentHypotheses.find(h => h.index === index);
    if (!hypothesis) return;

    const textElement = document.getElementById(`hypothesis-text-${index}`);
    const buttonElement = document.getElementById(`expand-btn-${index}`);

    // Check if we have citations to annotate
    const hasCitations = lastCitations && lastCitations.length > 0;

    if (expandedHypotheses.has(index)) {
        // Collapse
        expandedHypotheses.delete(index);
        const rawText = buildHypothesisDisplayText(hypothesis);
        const previewText = truncateText(rawText, 400);
        let formattedText = formatMarkdown(previewText);
        if (hasCitations) {
            formattedText = annotateTextWithCitations(formattedText, lastCitations, index);
        }
        textElement.innerHTML = formattedText;
        buttonElement.textContent = 'View Full';
    } else {
        // Expand
        expandedHypotheses.add(index);
        const rawText = buildHypothesisDisplayText(hypothesis);
        let formattedText = formatMarkdown(rawText);
        if (hasCitations) {
            formattedText = annotateTextWithCitations(formattedText, lastCitations, index);
        }
        textElement.innerHTML = formattedText;
        buttonElement.textContent = 'Show Less';
    }
}


// Edit hypothesis
function editHypothesis(index) {
    const hypothesis = currentHypotheses.find(h => h.index === index);
    const fullText = getHypothesisFullText(hypothesis);

    document.getElementById('edit-modal-title').textContent = 'Edit Hypothesis';
    document.getElementById('edit-hypothesis-index').value = index;
    document.getElementById('edit-action-type').value = 'edit';
    document.getElementById('edit-hypothesis-text').value = fullText;
    document.getElementById('edit-form').dataset.originalText = fullText;
    document.getElementById('edit-feedback').value = '';
    hideStatus('edit-status');
    showModal('edit-modal');
}

function openRegenerateModal(index) {
    const hypothesis = currentHypotheses.find(h => h.index === index);
    if (!hypothesis) return;

    document.getElementById('regenerate-hypothesis-index').value = index;
    const guidanceEl = document.getElementById('regenerate-guidance');
    if (guidanceEl) {
        guidanceEl.value = '';
    }
    hideStatus('regenerate-status');
    showModal('regenerate-modal');
}

async function submitRegenerateWithGuidance(index, guidanceValue) {
    if (regenerateRequestInFlight) {
        return;
    }
    if (guidanceValue && guidanceValue.trim() && !currentTaskDescription()) {
        openObjectiveDialog(() => submitRegenerateWithGuidance(index, guidanceValue));
        return;
    }
    const requestToken = ++regenerateRequestToken;
    const requestStateVersion = uiStateVersion;
    regenerateRequestController = new AbortController();
    regenerateRequestInFlight = true;
    const submitBtn = document.querySelector('#regenerate-form button[type="submit"]');
    if (submitBtn) {
        submitBtn.disabled = true;
    }
    showLoading('Regenerating hypothesis...');

    try {
        const payload = { index: index };
        if (Number.isInteger(historyGenerationScope) && historyGenerationScope >= 0) {
            payload.generation_id = historyGenerationScope;
        }
        const canonicalHypothesis = currentHypotheses.find((hyp) => String(hyp.index) === String(index));
        const canonicalStateId = (
            canonicalHypothesis && typeof canonicalHypothesis.state_id === 'string'
                ? canonicalHypothesis.state_id.trim()
                : ''
        ) || null;
        if (canonicalStateId) {
            payload.state_id = canonicalStateId;
        }
        if (canonicalHypothesis && canonicalHypothesis.full_text) {
            payload.manual_hypothesis = canonicalHypothesis.full_text;
        }
        if (guidanceValue && guidanceValue.trim()) {
            payload.guidance = guidanceValue;
        }
        const result = await apiCallWithSignal('/api/regenerate_guidance', payload, regenerateRequestController.signal);

        if (requestToken !== regenerateRequestToken || requestStateVersion !== uiStateVersion) {
            return;
        }

        if (result.success) {
            const resolvedIndexRaw = result.hypothesis ? result.hypothesis.index : null;
            const resolvedIndexParsed = Number.isInteger(resolvedIndexRaw)
                ? resolvedIndexRaw
                : parseInt(resolvedIndexRaw, 10);
            const targetIndex = Number.isInteger(resolvedIndexParsed) ? resolvedIndexParsed : index;

            bumpUiStateVersion('single_regenerate_success');
            cancelChatRequest('single_regenerate_success');
            clearEditHypothesisChatState(targetIndex);
            let hypIndex = currentHypotheses.findIndex(h => String(h.index) === String(targetIndex));
            if (hypIndex === -1 && String(targetIndex) !== String(index)) {
                hypIndex = currentHypotheses.findIndex(h => String(h.index) === String(index));
            }
            if (hypIndex !== -1) {
                currentHypotheses[hypIndex] = result.hypothesis;
            }

            if (editMode && editingHypothesis && String(editingHypothesis.index) === String(targetIndex)) {
                editingHypothesis = { ...result.hypothesis, localEdits: false };
                resetHistoryToHypothesis(editingHypothesis);
                updateEditorDisplay();
                clearChatMessagesUi({ persistState: false });
                appendChatMessage({
                    role: 'assistant',
                    mode: 'edit',
                    content: `I'll help you refine this hypothesis. Use "Edit with LLM Chat" to apply changes, or "Ask with LLM Chat" to ask questions without editing. Manual edits are available if you prefer to type directly.`
                });
            }

            if (result.citations && result.citations.length > 0) {
                lastCitations = result.citations;
            }
            if (result.documents) {
                updateCitationDocuments(result.documents);
            }

            displayHypotheses(currentHypotheses, lastCitations, citationDocuments);
            chatContextCache = null;
            loadChatContext(true);

            hideModal('regenerate-modal');
            const citationNote = result.citations && result.citations.length > 0 ? ' with citations' : '';
            showStatus('generate-status', `Hypothesis ${targetIndex} regenerated successfully${citationNote}!`, 'success');
        } else if (isMissingTaskDescription(result)) {
            openObjectiveDialog(() => submitRegenerateWithGuidance(index, guidanceValue));
        } else {
            showStatus('regenerate-status', `Error: ${result.error}`, 'error');
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            return;
        }
        showStatus('regenerate-status', `Error: ${error.message}`, 'error');
    } finally {
        if (requestToken === regenerateRequestToken) {
            regenerateRequestController = null;
            regenerateRequestInFlight = false;
        }
        if (submitBtn) {
            submitBtn.disabled = false;
        }
        hideLoading();
    }
}

// Handle edit/regenerate form submission
const editForm = document.getElementById('edit-form');
if (editForm) {
    editForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (editFormRequestInFlight) {
            return;
        }

        const index = parseInt(document.getElementById('edit-hypothesis-index').value, 10);
        const action = document.getElementById('edit-action-type').value;
        const manualText = document.getElementById('edit-hypothesis-text').value.trim();
        const feedback = document.getElementById('edit-feedback').value.trim();
        const originalText = document.getElementById('edit-form').dataset.originalText || '';

        if (!manualText) {
            showStatus('edit-status', 'Please keep or enter a hypothesis text.', 'error');
            return;
        }

        if (manualText === originalText && !feedback) {
            showStatus('edit-status', 'Make a direct edit or add instructions for the agent.', 'error');
            return;
        }

        if (feedback && !currentTaskDescription()) {
            openObjectiveDialog(() => editForm.requestSubmit());
            return;
        }

        showLoading(`${action === 'edit' ? 'Updating' : 'Regenerating'} hypothesis...`);
        hideStatus('edit-status');
        const requestToken = ++editFormRequestToken;
        const requestStateVersion = uiStateVersion;
        editFormRequestController = new AbortController();
        editFormRequestInFlight = true;
        const submitBtn = editForm.querySelector('button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = true;
        }

        try {
            const endpoint = action === 'edit' ? '/api/edit' : '/api/regenerate';
            const canonicalHypothesis = currentHypotheses.find((hyp) => String(hyp.index) === String(index));
            const canonicalStateId = (
                canonicalHypothesis && typeof canonicalHypothesis.state_id === 'string'
                    ? canonicalHypothesis.state_id.trim()
                    : ''
            ) || null;
            const payload = {
                index: index,
                state_id: canonicalStateId,
                feedback: feedback || null,
                manual_hypothesis: manualText
            };
            if (Number.isInteger(historyGenerationScope) && historyGenerationScope >= 0) {
                payload.generation_id = historyGenerationScope;
            }
            const result = await apiCallWithSignal(endpoint, payload, editFormRequestController.signal);

            if (requestToken !== editFormRequestToken || requestStateVersion !== uiStateVersion) {
                return;
            }

            if (result.success) {
                const resolvedIndexRaw = result.hypothesis ? result.hypothesis.index : null;
                const resolvedIndexParsed = Number.isInteger(resolvedIndexRaw)
                    ? resolvedIndexRaw
                    : parseInt(resolvedIndexRaw, 10);
                const targetIndex = Number.isInteger(resolvedIndexParsed) ? resolvedIndexParsed : index;

                bumpUiStateVersion('edit_form_success');
                cancelChatRequest('edit_form_success');
                if (action === 'regenerate') {
                    clearEditHypothesisChatState(targetIndex);
                }
                // Update hypothesis in current list
                let hypIndex = currentHypotheses.findIndex(h => String(h.index) === String(targetIndex));
                if (hypIndex === -1 && String(targetIndex) !== String(index)) {
                    hypIndex = currentHypotheses.findIndex(h => String(h.index) === String(index));
                }
                if (hypIndex !== -1) {
                    currentHypotheses[hypIndex] = result.hypothesis;
                }

                // Update citations and documents cache
                if (result.citations && result.citations.length > 0) {
                    lastCitations = result.citations;
                }
                if (result.documents) {
                    updateCitationDocuments(result.documents);
                }

                // Update display with current citations
                displayHypotheses(currentHypotheses, lastCitations, citationDocuments);
                chatContextCache = null;
                loadChatContext(true);

                hideModal('edit-modal');
                const citationNote = result.citations && result.citations.length > 0 ? ' with citations' : '';
                showStatus('generate-status', `Hypothesis ${targetIndex} ${action === 'edit' ? 'edited' : 'regenerated'} successfully${citationNote}!`, 'success');
            } else if (isMissingTaskDescription(result)) {
                openObjectiveDialog(() => editForm.requestSubmit());
            } else {
                showStatus('edit-status', `Error: ${result.error}`, 'error');
            }
        } catch (error) {
            if (error.name === 'AbortError') {
                return;
            }
            showStatus('edit-status', `Error: ${error.message}`, 'error');
        } finally {
            if (requestToken === editFormRequestToken) {
                editFormRequestController = null;
                editFormRequestInFlight = false;
            }
            if (submitBtn) {
                submitBtn.disabled = false;
            }
            hideLoading();
        }
    });
}

const regenerateForm = document.getElementById('regenerate-form');
if (regenerateForm) {
    regenerateForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const index = parseInt(document.getElementById('regenerate-hypothesis-index').value, 10);
        if (!index) {
            showStatus('regenerate-status', 'Missing hypothesis index.', 'error');
            return;
        }

        const guidanceValue = (document.getElementById('regenerate-guidance').value || '');
        hideStatus('regenerate-status');
        await submitRegenerateWithGuidance(index, guidanceValue);
    });
}

// Select hypothesis - Enter edit mode
function selectHypothesis(index) {
    selectedHypothesisIndex = index;
    const hyp = currentHypotheses.find(h => h.index === index);

    if (!hyp) return;

    // Enter edit mode
    enterEditMode(hyp);
}

function enterEditMode(hypothesis) {
    console.log('Entering edit mode with hypothesis:', hypothesis);
    invalidateInteractiveRequests('enter_edit_mode');
    bumpUiStateVersion('enter_edit_mode');
    // Remember whether the chat was open before editing (edit mode force-opens
    // it below via setEditType), so exitEditMode can restore this exact state.
    chatWasOpenBeforeEdit = !chatCollapsed;
    // Persist the non-edit chat before switching contexts.
    persistHypothesisChatState();
    editSessionToken += 1;
    editMode = true;
    editingHypothesis = { ...hypothesis };
    editingHypothesis.localEdits = false;
    console.log('editingHypothesis set to:', editingHypothesis);
    pushEditHistoryState(hypothesis);

    // Initialize edit history
    const restoredHypothesis = initializeHistory(hypothesis);
    if (restoredHypothesis) {
        editingHypothesis = { ...restoredHypothesis };
    }

    // Reset edit type to LLM ask by default
    currentEditType = 'llm_ask';

    // Show the instructions banner
    const banner = document.getElementById('editor-instructions-banner');
    if (banner) {
        banner.classList.remove('hidden');
    }

    // Ensure workspace is visible
    const workspace = document.getElementById('workspace');
    if (workspace) {
        workspace.classList.remove('hidden');
    }

    // Hide hypotheses section, show editor section
    const hypothesesSection = document.getElementById('hypotheses-section');
    const editorSection = document.getElementById('editor-section');
    const researchFeedbackSection = document.getElementById('research-feedback-section');
    if (hypothesesSection) {
        hypothesesSection.classList.add('hidden');
    }
    if (editorSection) {
        editorSection.classList.remove('hidden');
    }
    if (researchFeedbackSection) {
        researchFeedbackSection.classList.remove('hidden');
    }

    // Update editor content
    updateEditorDisplay();

    const researchFeedbackInput = document.getElementById('research-feedback-input');
    if (researchFeedbackInput) {
        researchFeedbackInput.value = '';
    }

    // Show the final accept bar at the bottom
    const finalAcceptBar = document.getElementById('final-accept-bar');
    if (finalAcceptBar) {
        finalAcceptBar.classList.remove('hidden');
    }

    // Set up edit type (defaults to LLM ask mode unless restored from storage).
    setEditType('llm_ask');

    // Configure chat panel for editing
    setChatPanelEditMode(true);

    const restored = hydrateEditHypothesisChatStateFromStorage(editingHypothesis ? editingHypothesis.index : null);
    if (!restored) {
        clearChatMessagesUi({ persistState: false });
        appendChatMessage({
            role: 'assistant',
            mode: 'edit',
            content: `I'll help you refine this hypothesis. Use "Edit with LLM Chat" to apply changes, or "Ask with LLM Chat" to ask questions without editing. Manual edits are available if you prefer to type directly.`
        });
    }
}

function exitEditMode() {
    console.log('Exiting edit mode');
    invalidateInteractiveRequests('exit_edit_mode');
    bumpUiStateVersion('exit_edit_mode');
    // Persist edit chat before leaving this hypothesis.
    persistHypothesisChatState();
    editSessionToken += 1;
    editMode = false;

    if (editingHypothesis) {
        saveHistoryToStorage(editingHypothesis.index);
    }
    editingHypothesis = null;

    // Clear edit history
    hypothesisHistory = [];
    historyIndex = -1;

    // Reset edit type
    currentEditType = 'llm_ask';

    // Hide the instructions banner
    const banner = document.getElementById('editor-instructions-banner');
    if (banner) {
        banner.classList.add('hidden');
    }

    // Hide the final accept bar
    const finalAcceptBar = document.getElementById('final-accept-bar');
    if (finalAcceptBar) {
        finalAcceptBar.classList.add('hidden');
    }

    // Hide manual edit container
    const manualContainer = document.getElementById('manual-edit-container');
    if (manualContainer) {
        manualContainer.classList.add('hidden');
    }

    // Show hypotheses section, hide editor section
    const editorSection = document.getElementById('editor-section');
    const hypothesesSection = document.getElementById('hypotheses-section');
    const researchFeedbackSection = document.getElementById('research-feedback-section');
    if (editorSection) {
        editorSection.classList.add('hidden');
    }
    if (hypothesesSection) {
        hypothesesSection.classList.remove('hidden');
    }
    if (researchFeedbackSection) {
        researchFeedbackSection.classList.add('hidden');
    }

    // Re-render hypotheses to show any edits made
    displayHypotheses(currentHypotheses, lastCitations, citationDocuments);

    // Reset chat panel
    setChatPanelEditMode(false);

    // Restore the pre-edit panel state: reopen if the chat was open before
    // editing, otherwise collapse. (hydrate below uses restorePanelState:false
    // so it won't override this.)
    setChatPanelCollapsed(!chatWasOpenBeforeEdit);

    // Restore regular chat history (without forcing panel open state).
    clearChatMessagesUi({ persistState: false });
    const restoredMainChat = hydrateHypothesisChatStateFromStorage({ restorePanelState: false });
    if (!restoredMainChat) {
        ensureChatGreeting();
    }

    if (shouldForceHypothesesLanding()) {
        const url = new URL(window.location.href);
        url.hash = HYPOTHESIS_LANDING_HASH;
        history.replaceState(buildHistoryGuardState('hypotheses'), '', url);
    }
}

function setChatPanelEditMode(isEditMode) {
    const chatPanelTitle = document.getElementById('chat-panel-title');
    const chatPanelSubtitle = document.getElementById('chat-panel-subtitle');
    const chatContextPanel = document.getElementById('chat-context-panel');
    const chatModeToggle = document.getElementById('chat-mode-toggle');
    const chatInput = document.getElementById('chat-input');
    const chatResetBtn = document.getElementById('chat-reset-btn');

    if (isEditMode) {
        if (chatPanelTitle) chatPanelTitle.textContent = 'Hypothesis Editor';
        if (chatContextPanel) chatContextPanel.classList.add('hidden');
        if (chatModeToggle) chatModeToggle.classList.add('hidden');
        if (chatResetBtn) chatResetBtn.classList.remove('hidden');
        chatMode = 'edit';
        updateEditChatUi(currentEditType);
    } else {
        if (chatPanelTitle) {
            chatPanelTitle.textContent = auditOnly ? 'Audit Chat' : 'Hypothesis Agent Chat';
        }
        if (chatPanelSubtitle) {
            chatPanelSubtitle.textContent = auditOnly
                ? 'Ask about hypotheses, the research paper, background reports, or switch to the code assistant.'
                : 'Ask about hypotheses, background reports, or switch to the code assistant.';
        }
        if (chatContextPanel) chatContextPanel.classList.remove('hidden');
        if (chatModeToggle) chatModeToggle.classList.remove('hidden');
        if (chatResetBtn) chatResetBtn.classList.remove('hidden');
        if (chatInput) {
            chatInput.placeholder = auditOnly
                ? 'Ask anything about the hypotheses, research paper, reports, or code...'
                : 'Ask anything about the hypotheses, reports, or code...';
        }
        chatMode = 'hypothesis';
    }
}

// Custom hypothesis
function showCustomHypothesisModal() {
    document.getElementById('custom-hypothesis').value = '';
    const customFeedback = document.getElementById('custom-feedback');
    if (customFeedback) {
        customFeedback.value = '';
    }
    showModal('custom-modal');
}

const customForm = document.getElementById('custom-form');
if (customForm) {
    customForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const customHypothesis = document.getElementById('custom-hypothesis').value.trim();
        const customFeedback = document.getElementById('custom-feedback')?.value.trim();

        if (!customHypothesis) {
            alert('Please enter a custom hypothesis');
            return;
        }

        selectedHypothesisIndex = 'custom';

        const parsed = parseHypothesisSections(customHypothesis);
        editingHypothesis = {
            index: 'custom',
            selectionType: 'custom',
            full_text: customHypothesis,
            title: parsed.title,
            hypothesis: parsed.hypothesis,
            justification: parsed.justification,
            interpretation: parsed.interpretation,
            plan: parsed.plan,
            customFeedback: customFeedback || null
        };

        if (customFeedback) {
            const researchFeedbackInput = document.getElementById('research-feedback-input');
            if (researchFeedbackInput) {
                const existing = researchFeedbackInput.value.trim();
                researchFeedbackInput.value = existing
                    ? `${existing}\n\n${customFeedback}`
                    : customFeedback;
            }
        }

        hideModal('custom-modal');
        showConfirmationModal();
    });
}

// Legacy selection controls (only present in older templates)
const confirmSelectionBtn = document.getElementById('confirm-selection-btn');
const cancelSelectionBtn = document.getElementById('cancel-selection-btn');
const selectionFeedbackInput = document.getElementById('selection-feedback');
const selectionSection = document.getElementById('selection-section');
const selectionStatusEl = document.getElementById('selection-status');

if (confirmSelectionBtn && selectionFeedbackInput && selectionStatusEl) {
    confirmSelectionBtn.addEventListener('click', async () => {
        if (selectedHypothesisIndex === null) {
            showStatus('selection-status', 'Please select a hypothesis first', 'error');
            return;
        }

        const feedback = selectionFeedbackInput.value.trim();

        if (!await window.V2Dialog.confirm({
            title: 'Save this selection?',
            message: 'This will write the hypothesis to the checkpoint directory.',
            confirmLabel: 'Save selection',
        })) {
            return;
        }

        showLoading('Saving hypothesis selection to server, do not close browser');
        hideStatus('selection-status');

        try {
            const data = {
                selection: selectedHypothesisIndex,
                feedback: feedback || null
            };

            if (selectedHypothesisIndex === 'custom') {
                data.custom_hypothesis = window.customHypothesisText;
                data.custom_source = 'direct_custom';
                if (window.customHypothesisText && String(window.customHypothesisText).trim()) {
                    data.final_text = String(window.customHypothesisText).trim();
                }
            } else {
                const selectedHypothesis = currentHypotheses.find(
                    (hyp) => String(hyp.index) === String(selectedHypothesisIndex)
                );
                if (selectedHypothesis && selectedHypothesis.full_text) {
                    data.final_text = String(selectedHypothesis.full_text).trim();
                }
                const selectionStateId = (
                    selectedHypothesis && typeof selectedHypothesis.state_id === 'string'
                        ? selectedHypothesis.state_id.trim()
                        : ''
                ) || null;
                if (!selectionStateId) {
                    showStatus('selection-status', 'Hypothesis state is missing. Re-open and try again.', 'error');
                    return;
                }
                data.selection_state_id = selectionStateId;
            }

            const result = await apiCall('/api/select', data);

            if (result.success) {
                showStatus('selection-status', `Selection saved successfully to: ${result.output_path}`, 'success');
                if (result.cleanup_ready) {
                    setCleanupArmed(true);
                }

                const displayEl = document.getElementById('selected-hypothesis-display');
                if (displayEl) {
                    displayEl.innerHTML = `
                        <div class="hypothesis-section">
                            <h4>Final Selected Hypothesis</h4>
                            <div class="hypothesis-text">${formatMarkdown(result.hypothesis)}</div>
                            <p style="margin-top: 20px;"><strong>Saved to:</strong> ${result.output_path}</p>
                            ${result.log_path ? `<p><strong>Session log:</strong> ${result.log_path}</p>` : ''}
                        </div>
                    `;
                }

                confirmSelectionBtn.disabled = true;
                selectionFeedbackInput.disabled = true;
            } else {
                showStatus('selection-status', `Error: ${result.error}`, 'error');
            }
        } catch (error) {
            showStatus('selection-status', `Error: ${error.message}`, 'error');
        } finally {
            hideLoading();
        }
    });
}

if (cancelSelectionBtn && selectionSection) {
    cancelSelectionBtn.addEventListener('click', () => {
        selectedHypothesisIndex = null;
        document.querySelectorAll('.hypothesis-card').forEach(card => {
            card.classList.remove('selected');
        });
        selectionSection.classList.add('hidden');
    });
}

// View context/reports (formatted)
// Navigate to the unified report page with summary view by default (opens in new tab)
const viewContextBtn = document.getElementById('view-context-btn');
if (viewContextBtn) {
    viewContextBtn.addEventListener('click', async () => {
        const ready = await initializeSession('init-status');
        if (!ready) return;
        if (hasResearchPaper) {
            window.open('/background_report', '_blank');
            return;
        }

        window.open('/report?type=background&view=summary', '_blank');
    });
}

const viewRoundContextBtn = document.getElementById('view-round-context-btn');
if (viewRoundContextBtn) {
    viewRoundContextBtn.addEventListener('click', async () => {
        const ready = await initializeSession('init-status');
        if (!ready) return;
        window.open('/report?type=round&view=summary', '_blank');
    });
}

const viewBackgroundLiteratureBtn = document.getElementById('view-background-literature-btn');
if (viewBackgroundLiteratureBtn) {
    viewBackgroundLiteratureBtn.addEventListener('click', async () => {
        if (!hasBackgroundLiterature) {
            alert('No background literature available.');
            return;
        }

        showLoading('Loading background literature...');

        try {
            const result = await apiCall('/api/get_report', { type: 'background_literature' });

            if (result.success) {
                document.getElementById('modal-title').textContent = 'Background Literature';
                const reportContent = document.getElementById('modal-report-content');
                reportContent.innerHTML = result.report_html || formatMarkdown(result.report);
                reportContent.style.whiteSpace = 'normal';
                applyCodeHighlight(reportContent);
                showModal('report-modal');
            } else {
                alert(`Error: ${result.error}`);
            }
        } catch (error) {
            alert(`Error: ${error.message}`);
        } finally {
            hideLoading();
        }
    });
}

function getChatGreetingForMode(mode) {
    if (mode === 'code') {
        return 'Hi! I can help inspect the repository and explain implementation details. Ask any code or debugging questions here.';
    }
    if (mode === 'edit') {
        return 'I can help refine this hypothesis. Use the edit/ask modes to guide what changes I apply.';
    }
    if (auditOnly) {
        return 'Hi! I can answer questions about the research paper, background report, and any related context for this session.';
    }
    return 'Hi! I can answer questions about the dataset, reports, and any hypotheses we have generated. Switch to the code tab whenever you need to inspect the repository with Claude Code.';
}

function ensureChatGreeting() {
    if (chatMessages.length > 0) {
        return;
    }
    appendChatMessage({
        role: 'assistant',
        mode: chatMode || 'hypothesis',
        content: getChatGreetingForMode(chatMode)
    });
}

function appendChatExpandableSection(
    bubble,
    {
        content,
        collapsedLabel = 'Show details',
        expandedLabel = 'Hide details',
        markdown = false
    } = {}
) {
    if (!bubble || typeof content !== 'string' || !content.trim()) {
        return;
    }

    const section = document.createElement('div');
    section.className = 'chat-thinking';

    const toggleBtn = document.createElement('button');
    toggleBtn.type = 'button';
    toggleBtn.className = 'chat-thinking-toggle';
    toggleBtn.textContent = collapsedLabel;
    toggleBtn.setAttribute('aria-expanded', 'false');

    const details = document.createElement(markdown ? 'div' : 'pre');
    details.className = 'chat-thinking-content hidden';
    if (markdown) {
        details.innerHTML = formatMarkdown(escapeHtml(content));
        applyCodeHighlight(details);
    } else {
        details.textContent = content;
    }

    toggleBtn.addEventListener('click', () => {
        const isHidden = details.classList.toggle('hidden');
        toggleBtn.textContent = isHidden ? collapsedLabel : expandedLabel;
        toggleBtn.setAttribute('aria-expanded', (!isHidden).toString());
    });

    section.appendChild(toggleBtn);
    section.appendChild(details);
    bubble.appendChild(section);
}

function appendChatMessage({
    role,
    content,
    mode = 'hypothesis',
    thinking = null,
    responseTimeMs = null,
    isPlaceholder = false,
    citations = [],
    documents = [],
    persistState = true,
    autoScroll = true
}) {
    const container = document.getElementById('chat-messages');
    if (!container) return null;

    const bubble = document.createElement('div');
    bubble.classList.add('chat-bubble', `chat-${role}`);
    if (mode === 'code') {
        bubble.classList.add('chat-code');
    }
    if (role === 'system') {
        bubble.classList.add('chat-system');
    }
    if (isPlaceholder) {
        bubble.dataset.placeholder = 'true';
    }

    const label = document.createElement('div');
    label.className = 'chat-bubble-label';
    if (role === 'user') {
        label.textContent = 'You';
    } else if (role === 'system') {
        label.textContent = 'System';
    } else if (mode === 'code') {
        label.textContent = 'Code Assistant';
    } else {
        label.textContent = 'Hypothesis Agent';
    }

    const body = document.createElement('div');
    body.className = 'chat-bubble-content';
    
    // First escape and format markdown, then add citation markers (so markers don't get escaped)
    let displayContent = formatMarkdown(escapeHtml(content));
    if (!isPlaceholder && citations && citations.length > 0) {
        displayContent = annotateTextWithCitations(displayContent, citations);
    }
    body.innerHTML = displayContent;
    if (!isPlaceholder) {
        applyCodeHighlight(body);
    }

    if (isPlaceholder) {
        body.innerHTML = `
            <div class="chat-spinner" aria-label="Assistant is thinking">
                <span></span><span></span><span></span>
            </div>
            <div class="chat-spinner-text">${escapeHtml(content)}</div>
        `;
    }

    bubble.appendChild(label);
    bubble.appendChild(body);

    const responseTimeLabel = formatResponseTimeMs(responseTimeMs);
    if (!isPlaceholder && role === 'assistant' && responseTimeLabel) {
        const timing = document.createElement('div');
        timing.className = 'chat-response-time';
        timing.textContent = `Response time: ${responseTimeLabel}`;
        bubble.appendChild(timing);
    }

    // Add citations reference section if present
    if (!isPlaceholder && citations && citations.length > 0) {
        const citationsHtml = renderCitations(citations, documents, false);
        if (citationsHtml) {
            const citationsDiv = document.createElement('div');
            citationsDiv.innerHTML = citationsHtml;
            bubble.appendChild(citationsDiv.firstElementChild);
        }
    }

    if (!isPlaceholder && thinking && mode === 'code') {
        appendChatExpandableSection(bubble, {
            content: thinking,
            collapsedLabel: 'Show thinking',
            expandedLabel: 'Hide thinking',
            markdown: false
        });
    }

    container.appendChild(bubble);
    if (autoScroll) {
        container.scrollTop = container.scrollHeight;
    }

    if (!isPlaceholder) {
        chatMessages.push({
            role,
            mode,
            content,
            thinking: thinking || null,
            responseTimeMs: Number.isFinite(responseTimeMs) ? responseTimeMs : null
        });
        if (persistState) {
            persistHypothesisChatState();
        }
    }

    return bubble;
}

// ---------------------------------------------------------------------------
// Background Report Chat (split view similar to main workspace)
// ---------------------------------------------------------------------------
const reportWorkspace = document.getElementById('report-workspace');
if (reportWorkspace) {
    const reportContentPanel = document.getElementById('report-content-panel');
    const reportChatPanel = document.getElementById('report-chat-panel');
    const reportSplitDivider = document.getElementById('report-split-divider');
    const reportChatToggle = document.getElementById('report-chat-toggle');
    const reportChatClose = document.getElementById('report-chat-close-btn');
    const reportChatResetBtn = document.getElementById('report-chat-reset-btn');
    const reportChatMessagesEl = document.getElementById('report-chat-messages');
    const reportChatInput = document.getElementById('report-chat-input');
    const reportChatSendBtn = document.getElementById('report-chat-send-btn');
    const reportChatCancelBtn = document.getElementById('report-chat-cancel-btn');
    const reportTypeForContext = (() => {
        const configured = String(window.reportPageConfig?.reportType || 'background').toLowerCase();
        return configured === 'round' ? 'round' : 'background';
    })();
    const reportViewForTelemetry = (() => {
        const configured = String(window.reportPageConfig?.currentView || 'summary').toLowerCase();
        return configured === 'full' ? 'full' : 'summary';
    })();
    const reportChatStorageKey = getReportChatStorageKey(reportTypeForContext);

    let reportChatCollapsed = true;
    let reportSplitPercent = 60;
    let reportDragging = false;
    let reportDragStartX = 0;
    let reportDragStartLeftWidth = 0;
    let reportChatMode = 'code';
    let reportChatMessages = [];
    let reportChatRequestToken = 0;
    let reportChatRequestController = null;
    let reportChatRequestInFlight = false;
    let reportChatClientRequestId = null;
    let reportChatPendingScrollTop = null;
    let reportChatPersistTimer = null;
    let durableReportPersistTimer = null;
    let durableReportPersistChain = Promise.resolve();
    let reportChatDirtySinceHydration = false;

    function clampReportSplitPercent(percent) {
        const parsed = Number(percent);
        if (!Number.isFinite(parsed)) return 60;
        return Math.min(75, Math.max(25, parsed));
    }

    function queuePersistReportChatState(delayMs = 120) {
        if (reportChatPersistTimer) {
            clearTimeout(reportChatPersistTimer);
        }
        reportChatPersistTimer = window.setTimeout(() => {
            reportChatPersistTimer = null;
            persistReportChatState();
        }, delayMs);
    }

    function normalizeStoredReportChatMessages(messages) {
        const normalized = normalizeStoredChatMessages(
            messages,
            REPORT_CHAT_STORAGE_MAX_MESSAGES,
            REPORT_CHAT_STORAGE_MAX_CHARS
        );
        return normalized.map((message) => ({
            role: message.role,
            content: message.content,
            mode: 'code',
            thinking: message.thinking || null,
            responseTimeMs: message.responseTimeMs
        }));
    }

    function compactReportChatMessagesForStorage(messages) {
        const normalized = normalizeStoredReportChatMessages(messages);
        if (normalized.length === 0) {
            return [];
        }
        const compact = [];
        let charBudget = 0;
        for (let i = normalized.length - 1; i >= 0; i -= 1) {
            const message = normalized[i];
            const messageChars = message.content.length
                + (message.thinking ? message.thinking.length : 0);
            if (compact.length >= REPORT_CHAT_STORAGE_MAX_MESSAGES) break;
            if (charBudget + messageChars > REPORT_CHAT_STORAGE_MAX_CHARS) break;
            compact.unshift(message);
            charBudget += messageChars;
        }
        return compact;
    }

    function readReportChatStateFromStorage() {
        try {
            const raw = localStorage.getItem(reportChatStorageKey);
            if (!raw) {
                return null;
            }
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object') {
                return null;
            }
            return {
                collapsed: parsed.collapsed !== false,
                splitPercent: clampReportSplitPercent(parsed.splitPercent),
                mode: parsed.mode === 'code' ? 'code' : 'code',
                scrollTop: Number.isFinite(parsed.scrollTop) ? parsed.scrollTop : null,
                messages: normalizeStoredReportChatMessages(parsed.messages),
            };
        } catch (error) {
            console.warn('[report-chat] failed to read stored state', error);
            try {
                localStorage.removeItem(reportChatStorageKey);
            } catch (removeError) {
                // Ignore storage cleanup errors.
            }
            return null;
        }
    }

    function persistReportChatState() {
        try {
            const state = {
                version: CHAT_STORAGE_VERSION,
                reportType: reportTypeForContext,
                collapsed: reportChatCollapsed,
                splitPercent: clampReportSplitPercent(reportSplitPercent),
                mode: reportChatMode,
                scrollTop: reportChatMessagesEl ? reportChatMessagesEl.scrollTop : 0,
                messages: compactReportChatMessagesForStorage(reportChatMessages),
                updatedAt: Date.now(),
            };
            localStorage.setItem(reportChatStorageKey, JSON.stringify(state));
            reportChatDirtySinceHydration = true;
            queueDurableReportChatState(state);
        } catch (error) {
            // Ignore persistence failures (private mode, disabled storage, quota exceeded).
            console.warn('[report-chat] failed to persist state', error);
        }
    }

    function queueDurableReportChatState(state) {
        const durableScope = window.reportPageConfig?.durableChatScope;
        if (typeof durableScope !== 'string' || !durableScope || !state) return;
        if (durableReportPersistTimer) window.clearTimeout(durableReportPersistTimer);
        const snapshot = JSON.parse(JSON.stringify(state));
        durableReportPersistTimer = window.setTimeout(() => {
            durableReportPersistTimer = null;
            durableReportPersistChain = durableReportPersistChain
                .catch(() => {})
                .then(() => fetch('/api/conversation_state', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({
                        surface: 'report_chat',
                        scope: durableScope,
                        state: snapshot,
                    }),
                }))
                .then((response) => {
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                })
                .catch((error) => console.warn('[report-chat] durable conversation save failed', error));
        }, 120);
    }

    function requestReportChatServerCancel(requestId) {
        if (!requestId) return;
        fetch('/api/cancel_report_chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            keepalive: true,
            body: JSON.stringify({ request_id: requestId })
        }).catch(() => {
            // Ignore cancellation network errors.
        });
    }

    function setReportChatPending(pending) {
        if (reportChatSendBtn) {
            reportChatSendBtn.disabled = pending;
        }
        if (reportChatInput) {
            reportChatInput.disabled = pending;
        }
        if (reportChatCancelBtn) {
            reportChatCancelBtn.disabled = !pending;
            reportChatCancelBtn.classList.toggle('hidden', !pending);
        }
    }

    function cancelReportChatRequest(reason = 'context-change', { notifyServer = true } = {}) {
        const requestId = reportChatClientRequestId;
        reportChatRequestToken += 1;
        if (reportChatRequestController) {
            try {
                reportChatRequestController.abort();
            } catch (err) {
                // Ignore abort errors.
            }
            reportChatRequestController = null;
        }
        reportChatClientRequestId = null;
        reportChatRequestInFlight = false;
        setReportChatPending(false);
        if (notifyServer && requestId) {
            requestReportChatServerCancel(requestId);
        }
        console.log(`[report-chat] request cancelled reason=${reason}`);
    }

    function applyReportSplit(percent = reportSplitPercent, { persistState = false } = {}) {
        reportSplitPercent = clampReportSplitPercent(percent);
        if (reportChatCollapsed) {
            reportContentPanel.style.flexBasis = '100%';
            if (persistState) {
                queuePersistReportChatState();
            }
            return;
        }
        reportContentPanel.style.flexBasis = `calc(${reportSplitPercent}% - 1px)`;
        reportChatPanel.style.flexBasis = `calc(${100 - reportSplitPercent}% - 1px)`;
        if (persistState) {
            queuePersistReportChatState();
        }
    }

    function mountReportChat({ persistState = true, restoreScroll = false } = {}) {
        if (!reportChatPanel || !reportSplitDivider) return;
        reportChatPanel.classList.remove('hidden');
        reportSplitDivider.classList.remove('hidden');
        reportChatCollapsed = false;
        applyReportSplit(reportSplitPercent, { persistState: false });
        ensureReportGreeting();
        if (restoreScroll && reportChatMessagesEl) {
            const scrollTarget = Number.isFinite(reportChatPendingScrollTop)
                ? reportChatPendingScrollTop
                : reportChatMessagesEl.scrollHeight;
            window.requestAnimationFrame(() => {
                reportChatMessagesEl.scrollTop = scrollTarget;
            });
            reportChatPendingScrollTop = null;
        }
        if (persistState) {
            persistReportChatState();
        }
    }

    function unmountReportChat({ persistState = true } = {}) {
        cancelReportChatRequest('panel_closed');
        if (!reportChatPanel || !reportSplitDivider) return;
        reportChatPanel.classList.add('hidden');
        reportSplitDivider.classList.add('hidden');
        reportChatCollapsed = true;
        applyReportSplit(reportSplitPercent, { persistState: false });
        if (persistState) {
            persistReportChatState();
        }
    }

    function ensureReportGreeting() {
        if (reportChatMessages.length > 0) return;
        appendReportChatMessage({
            role: 'assistant',
            content: 'Hi! Ask me about the report or the codebase. I will use the report context plus the repository to answer.',
            mode: 'code'
        });
    }

    function clearReportChatMessagesUi({ persistState = true } = {}) {
        reportChatMessages = [];
        if (reportChatMessagesEl) {
            reportChatMessagesEl.innerHTML = '';
        }
        if (persistState) {
            persistReportChatState();
        }
    }

    async function resetReportChatContext() {
        const confirmed = await window.V2Dialog.confirm({
            title: 'Reset conversation?',
            message: 'Resetting context will erase the current conversation you have with the agent.',
            confirmLabel: 'Reset',
        });
        if (!confirmed) {
            return;
        }
        cancelReportChatRequest('reset_report_chat_context');
        clearReportChatMessagesUi();
        ensureReportGreeting();
        try {
            await apiCall('/api/reset_chat_context', {
                scope: 'report',
                report_type: reportTypeForContext,
                report_view: reportViewForTelemetry
            });
        } catch (error) {
            console.warn('Failed to reset report chat context', error);
        }
    }

    function appendReportChatMessage({
        role,
        content,
        mode = 'code',
        isPlaceholder = false,
        thinking = null,
        responseTimeMs = null,
        persistState = true,
        autoScroll = true
    }) {
        if (!reportChatMessagesEl) return null;

        const bubble = document.createElement('div');
        bubble.classList.add('chat-bubble', `chat-${role}`);
        if (mode === 'code') bubble.classList.add('chat-code');
        if (role === 'system') bubble.classList.add('chat-system');
        if (isPlaceholder) bubble.dataset.placeholder = 'true';

        const label = document.createElement('div');
        label.className = 'chat-bubble-label';
        if (role === 'user') {
            label.textContent = 'You';
        } else if (role === 'system') {
            label.textContent = 'System';
        } else if (mode === 'code') {
            label.textContent = 'Code Assistant';
        } else {
            label.textContent = 'Assistant';
        }

        const body = document.createElement('div');
        body.className = 'chat-bubble-content';
        if (isPlaceholder) {
            body.innerHTML = `
                <div class="chat-spinner" aria-label="Assistant is thinking">
                    <span></span><span></span><span></span>
                </div>
                <div class="chat-spinner-text">${escapeHtml(content)}</div>
            `;
        } else {
            body.innerHTML = formatMarkdown(escapeHtml(content));
            applyCodeHighlight(body);
        }

        bubble.appendChild(label);
        bubble.appendChild(body);
        const responseTimeLabel = formatResponseTimeMs(responseTimeMs);
        if (!isPlaceholder && role === 'assistant' && responseTimeLabel) {
            const timing = document.createElement('div');
            timing.className = 'chat-response-time';
            timing.textContent = `Response time: ${responseTimeLabel}`;
            bubble.appendChild(timing);
        }
        if (!isPlaceholder && thinking && mode === 'code') {
            appendChatExpandableSection(bubble, {
                content: thinking,
                collapsedLabel: 'Show thinking',
                expandedLabel: 'Hide thinking',
                markdown: false
            });
        }
        reportChatMessagesEl.appendChild(bubble);
        if (autoScroll) {
            reportChatMessagesEl.scrollTop = reportChatMessagesEl.scrollHeight;
        }
        if (!isPlaceholder) {
            reportChatMessages.push({
                role,
                content,
                mode,
                thinking: thinking || null,
                responseTimeMs: Number.isFinite(responseTimeMs) ? responseTimeMs : null
            });
            if (persistState) {
                persistReportChatState();
            }
        }
        return bubble;
    }

    function applyStoredReportChatState(stored) {
        if (!stored) return;
        reportChatMode = stored.mode;
        reportSplitPercent = clampReportSplitPercent(stored.splitPercent);
        reportChatPendingScrollTop = stored.scrollTop;

        clearReportChatMessagesUi({ persistState: false });
        for (const message of stored.messages) {
            appendReportChatMessage({
                role: message.role,
                content: message.content,
                mode: message.mode || 'code',
                thinking: message.thinking || null,
                responseTimeMs: message.responseTimeMs,
                persistState: false,
                autoScroll: false
            });
        }

        if (stored.collapsed) {
            reportChatCollapsed = true;
            applyReportSplit(reportSplitPercent, { persistState: false });
        } else {
            mountReportChat({ persistState: false, restoreScroll: true });
        }
    }

    function hydrateReportChatStateFromStorage() {
        const cached = readReportChatStateFromStorage();
        if (cached) applyStoredReportChatState(cached);
        else applyReportSplit(reportSplitPercent, { persistState: false });

        const durableScope = window.reportPageConfig?.durableChatScope;
        if (typeof durableScope !== 'string' || !durableScope) return;
        const url = new URL('/api/conversation_state', window.location.origin);
        url.searchParams.set('surface', 'report_chat');
        url.searchParams.set('scope', durableScope);
        fetch(url.toString(), { credentials: 'include' })
            .then((response) => response.ok ? response.json() : null)
            .then((payload) => {
                if (!payload || reportChatDirtySinceHydration) return;
                if (payload.state && typeof payload.state === 'object') {
                    const durable = {
                        collapsed: payload.state.collapsed !== false,
                        splitPercent: clampReportSplitPercent(payload.state.splitPercent),
                        mode: 'code',
                        scrollTop: Number.isFinite(payload.state.scrollTop) ? payload.state.scrollTop : null,
                        messages: normalizeStoredReportChatMessages(payload.state.messages),
                    };
                    applyStoredReportChatState(durable);
                    try { localStorage.setItem(reportChatStorageKey, JSON.stringify(payload.state)); } catch (_) {}
                } else if (cached) {
                    // One-time migration of the old browser-only snapshot.
                    queueDurableReportChatState({
                        version: CHAT_STORAGE_VERSION,
                        reportType: reportTypeForContext,
                        collapsed: cached.collapsed,
                        splitPercent: cached.splitPercent,
                        mode: cached.mode,
                        scrollTop: cached.scrollTop,
                        messages: cached.messages,
                        updatedAt: Date.now(),
                    });
                }
            })
            .catch((error) => console.warn('[report-chat] durable conversation load failed', error));
    }

    async function sendReportChat() {
        const message = reportChatInput.value.trim();
        if (!message) return;
        if (reportChatRequestInFlight) return;

        const requestToken = ++reportChatRequestToken;
        const clientRequestId = `report_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
        reportChatClientRequestId = clientRequestId;
        reportChatRequestController = new AbortController();
        reportChatRequestInFlight = true;
        setReportChatPending(true);

        appendReportChatMessage({ role: 'user', content: message, mode: reportChatMode });
        reportChatInput.value = '';

        const placeholder = appendReportChatMessage({
            role: 'assistant',
            content: 'Thinking...',
            mode: reportChatMode,
            isPlaceholder: true
        });

        try {
            const response = await fetch('/api/report_chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                signal: reportChatRequestController.signal,
                body: JSON.stringify({
                    message,
                    mode: reportChatMode,
                    report_type: reportTypeForContext,
                    report_view: reportViewForTelemetry,
                    request_id: clientRequestId,
                    messages: reportChatMessages
                        .slice(0, -1)
                        .filter((entry) => entry.role === 'user' || entry.role === 'assistant')
                        .map((entry) => ({ role: entry.role, content: entry.content }))
                })
            });
            if (requestToken !== reportChatRequestToken) {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                return;
            }
            const contentType = response.headers.get('content-type') || '';
            if (!response.ok || !contentType.includes('application/json')) {
                const text = await response.text();
                const snippet = text ? text.slice(0, 200).trim() : '';
                throw new Error(`HTTP ${response.status} ${response.statusText}${snippet ? ` - ${snippet}` : ''}`);
            }
            const result = await response.json();
            if (requestToken !== reportChatRequestToken) {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                return;
            }
            if (result.success) {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                const thinkingText = typeof result.thinking === 'string' && result.thinking.trim()
                    ? result.thinking.trim()
                    : null;
                appendReportChatMessage({
                    role: 'assistant',
                    content: result.reply,
                    mode: result.mode || reportChatMode,
                    thinking: thinkingText,
                    responseTimeMs: result.response_time_ms
                });
            } else if (result.cancelled) {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                appendReportChatMessage({
                    role: 'system',
                    content: 'Cancelled current response.',
                    mode: 'code'
                });
            } else {
                const resetHint = result.reset_hint ? ` ${result.reset_hint}` : '';
                if (placeholder && placeholder.querySelector('.chat-bubble-content')) {
                    placeholder.querySelector('.chat-bubble-content').textContent = `Error: ${result.error}${resetHint}`;
                }
            }
        } catch (error) {
            if (error.name === 'AbortError') {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                return;
            }
            if (requestToken !== reportChatRequestToken) {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                return;
            }
            if (placeholder && placeholder.querySelector('.chat-bubble-content')) {
                placeholder.querySelector('.chat-bubble-content').textContent = `Error: ${error.message}`;
            }
        } finally {
            if (requestToken === reportChatRequestToken) {
                reportChatRequestController = null;
                reportChatClientRequestId = null;
                reportChatRequestInFlight = false;
                setReportChatPending(false);
            }
            if (reportChatMessagesEl) {
                reportChatMessagesEl.scrollTop = reportChatMessagesEl.scrollHeight;
            }
            queuePersistReportChatState();
        }
    }

    hydrateReportChatStateFromStorage();
    window.__persistActiveReportChatState = () => persistReportChatState();

    if (reportChatMessagesEl) {
        reportChatMessagesEl.addEventListener('scroll', () => {
            if (!reportChatCollapsed) {
                queuePersistReportChatState();
            }
        });
    }

    window.addEventListener('beforeunload', () => {
        persistReportChatState();
    });

    // Event bindings
    if (reportChatToggle) {
        reportChatToggle.addEventListener('click', () => {
            mountReportChat();
        });
    }

    if (reportChatClose) {
        reportChatClose.addEventListener('click', () => {
            unmountReportChat();
        });
    }

    if (reportChatResetBtn) {
        reportChatResetBtn.addEventListener('click', resetReportChatContext);
    }
    if (reportChatCancelBtn) {
        reportChatCancelBtn.addEventListener('click', () => {
            if (!reportChatRequestInFlight) return;
            cancelReportChatRequest('user_cancelled');
            appendReportChatMessage({
                role: 'system',
                content: 'Cancelled current response.',
                mode: 'code'
            });
        });
    }

    if (reportSplitDivider) {
        reportSplitDivider.addEventListener('mousedown', (event) => {
            reportDragging = true;
            reportDragStartX = event.clientX;
            reportDragStartLeftWidth = reportContentPanel.getBoundingClientRect().width;
            document.body.classList.add('resizing');
        });

        window.addEventListener('mousemove', (event) => {
            if (!reportDragging) return;
            const totalWidth = reportWorkspace.getBoundingClientRect().width;
            const delta = event.clientX - reportDragStartX;
            const newLeftWidth = reportDragStartLeftWidth + delta;
            const newPercent = Math.min(75, Math.max(25, (newLeftWidth / totalWidth) * 100));
            applyReportSplit(newPercent, { persistState: true });
        });

        window.addEventListener('mouseup', () => {
            reportDragging = false;
            document.body.classList.remove('resizing');
            queuePersistReportChatState(0);
        });
    }

    if (reportChatSendBtn) {
        reportChatSendBtn.addEventListener('click', sendReportChat);
    }
    if (reportChatInput) {
        reportChatInput.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                sendReportChat();
            }
        });
    }
}

function setChatMode(mode) {
    if (mode === 'code' && !codeChatAvailable) {
        appendChatMessage({
            role: 'system',
            mode: 'hypothesis',
            content: 'Code chat is unavailable because no code directory was provided when the server was started.'
        });
        return;
    }

    chatMode = mode;
    document.querySelectorAll('#chat-mode-toggle .chat-mode-button').forEach(btn => {
        if (btn.dataset.mode === mode) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
    queuePersistHypothesisChatState();
}

async function loadChatContext(forceRefresh = false) {
    const contentEl = document.getElementById('chat-context-content');
    if (!contentEl) return;

    if (chatInitializing) {
        chatContextRefreshPending = true;
        if (forceRefresh) {
            chatContextRefreshPendingForce = true;
        }
        return;
    }
    if (chatContextCache && !forceRefresh) {
        renderChatContext(chatContextCache);
        return;
    }

    chatInitializing = true;
    contentEl.innerHTML = '<p class="chat-context-empty">Loading session context...</p>';

    try {
        const result = await apiCall('/api/chat_context');
        if (result.success) {
            chatContextCache = result;
            renderChatContext(chatContextCache);
        } else {
            contentEl.innerHTML = `<p class="chat-context-empty">${escapeHtml(result.error || 'Unable to load context')}</p>`;
        }
    } catch (error) {
        contentEl.innerHTML = `<p class="chat-context-empty">Error: ${escapeHtml(error.message)}</p>`;
    } finally {
        chatInitializing = false;
        if (chatContextRefreshPending) {
            const pendingForce = chatContextRefreshPendingForce;
            chatContextRefreshPending = false;
            chatContextRefreshPendingForce = false;
            loadChatContext(pendingForce);
        }
    }
}

function renderChatContext(context) {
    const contentEl = document.getElementById('chat-context-content');
    if (!contentEl) return;

    const hasResearchPaper = auditOnly && context && context.research_paper;

    if (!context || (
        !context.dataset_description &&
        !context.task_description &&
        !context.background_report &&
        !hasResearchPaper &&
        !(context.include_background_literature && context.background_literature) &&
        (!context.hypotheses || context.hypotheses.length === 0)
    )) {
        contentEl.innerHTML = auditOnly
            ? '<p class="chat-context-empty">Initialize a session to load report context.</p>'
            : '<p class="chat-context-empty">Generate hypotheses to unlock rich session context.</p>';
        return;
    }

    const sections = [];

    if (context.dataset_description) {
        sections.push(`
            <div class="chat-context-block">
                <div class="chat-context-label">Dataset</div>
                <div class="chat-context-text">${formatMarkdown(escapeHtml(truncateText(context.dataset_description, 600)))}</div>
            </div>
        `);
    }

    if (context.task_description) {
        sections.push(`
            <div class="chat-context-block">
                <div class="chat-context-label">Task</div>
                <div class="chat-context-text">${formatMarkdown(escapeHtml(truncateText(context.task_description, 500)))}</div>
            </div>
        `);
    }

    if (context.background_report) {
        sections.push(`
            <div class="chat-context-block">
                <div class="chat-context-label">Background Report</div>
                <div class="chat-context-text">${formatMarkdown(escapeHtml(truncateText(context.background_report, 500)))}</div>
            </div>
        `);
    }

    if (hasResearchPaper) {
        sections.push(`
            <div class="chat-context-block">
                <div class="chat-context-label">Research Paper</div>
                <div class="chat-context-text">${formatMarkdown(escapeHtml(truncateText(context.research_paper, 500)))}</div>
            </div>
        `);
    }

    if (context.include_background_literature && context.background_literature) {
        sections.push(`
            <div class="chat-context-block">
                <div class="chat-context-label">Background Literature</div>
                <div class="chat-context-text">${formatMarkdown(escapeHtml(truncateText(context.background_literature, 500)))}</div>
            </div>
        `);
    }

    if (context.round_report) {
        sections.push(`
            <div class="chat-context-block">
                <div class="chat-context-label">Latest Round Report</div>
                <div class="chat-context-text">${formatMarkdown(escapeHtml(truncateText(context.round_report, 400)))}</div>
            </div>
        `);
    }

    if (context.hypotheses && context.hypotheses.length > 0) {
        const hypothesesHtml = context.hypotheses.map(h => {
            const title = h.title ? `: ${escapeHtml(h.title)}` : '';
            const summarySource = h.plan || h.hypothesis || h.full_text || '';
            return `
                <div class="chat-context-chip">
                    <div class="chat-context-chip-title">H${h.index}${title}</div>
                    <div class="chat-context-chip-body">${escapeHtml(truncateText(summarySource, 220))}</div>
                </div>
            `;
        }).join('');

        sections.push(`
            <div class="chat-context-block">
                <div class="chat-context-label">Candidate Hypotheses (${context.hypotheses.length})</div>
                <div class="chat-context-hypotheses">${hypothesesHtml}</div>
            </div>
        `);
    }

    contentEl.innerHTML = sections.join('');
}

async function handleChatSubmit(event) {
    // Prevent any default behavior if called from a form/event
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }

    console.log('handleChatSubmit called, chatMode:', chatMode, 'editMode:', editMode);

    const input = document.getElementById('chat-input');
    if (!input) {
        console.error('Chat input not found');
        return;
    }

    const message = input.value.trim();
    if (!message) {
        console.log('Empty message, returning');
        return;
    }
    if (chatRequestInFlight) {
        return;
    }

    const requestToken = ++chatRequestToken;
    const requestStateVersion = uiStateVersion;
    const requestEditSession = editSessionToken;
    const requestEditHypothesisIndex = editingHypothesis ? editingHypothesis.index : null;
    chatRequestController = new AbortController();
    chatRequestInFlight = true;
    setChatSubmitPending(true);

    appendChatMessage({
        role: 'user',
        content: message,
        mode: chatMode
    });
    input.value = '';

    const placeholder = appendChatMessage({
        role: 'assistant',
        content: 'Thinking...',
        mode: chatMode,
        isPlaceholder: true
    });

    try {
        let result;

        console.log('Making API call, chatMode:', chatMode, 'editingHypothesis:', editingHypothesis ? editingHypothesis.index : null);

        const isEditChat = chatMode === 'edit' && editingHypothesis;
        const isLlmEdit = isEditChat && currentEditType === 'llm_edit';
        const isLlmAsk = isEditChat && currentEditType === 'llm_ask';
        const wrappedMessage = (isLlmEdit || isLlmAsk)
            ? wrapEditModeMessage(currentEditType, message)
            : message;
        const currentDraftText = isEditChat && editingHypothesis && editingHypothesis.full_text
            ? editingHypothesis.full_text
            : '';
        const askMessage = isLlmAsk && currentDraftText
            ? `${wrappedMessage}\n\nCurrent hypothesis draft:\n${currentDraftText}`
            : wrappedMessage;

        // Handle edit mode differently
        if (isLlmEdit) {
            const requestHypothesis = getEffectiveEditingHypothesis() || editingHypothesis;
            console.log('Calling /api/edit with index:', requestHypothesis.index);
            const editStateId = (
                typeof requestHypothesis.state_id === 'string'
                    ? requestHypothesis.state_id.trim()
                    : ''
            ) || null;
            result = await apiCallWithSignal('/api/edit', {
                index: requestHypothesis.index,
                state_id: editStateId,
                feedback: wrappedMessage,
                manual_hypothesis: requestHypothesis.full_text
            }, chatRequestController.signal);
            console.log('API edit result:', result);

            if (requestToken !== chatRequestToken) {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                return;
            }
            const editContextShifted = (
                requestStateVersion !== uiStateVersion
                || requestEditSession !== editSessionToken
                || !editMode
                || !editingHypothesis
                || editingHypothesis.index !== requestEditHypothesisIndex
            );
            if (editContextShifted) {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                return;
            }

            if (result.success) {
                // Update the editing hypothesis with the new version
                editingHypothesis = { ...result.hypothesis, localEdits: false };
                bumpUiStateVersion('chat_llm_edit_success');

                // Update citations if returned
                if (result.citations && result.citations.length > 0) {
                    lastCitations = result.citations;
                }
                if (result.documents) {
                    updateCitationDocuments(result.documents);
                }

                // Update the displayed hypothesis in real-time
                updateEditorDisplay();

                // Add to history
                addToHistory(editingHypothesis);

                // Also update in the currentHypotheses array
                const hypIndex = currentHypotheses.findIndex(h => h.index === result.hypothesis.index);
                if (hypIndex !== -1) {
                    currentHypotheses[hypIndex] = result.hypothesis;
                }

                // Show success message in chat
                placeholder.dataset.placeholder = 'false';
                placeholder.querySelector('.chat-bubble-label').textContent = 'Editor';
                placeholder.querySelector('.chat-bubble-content').innerHTML = formatMarkdown(escapeHtml('Hypothesis updated! The changes are now reflected on the left. You can use the arrows to navigate through edit history.'));
                applyCodeHighlight(placeholder);

                chatMessages.push({
                    role: 'assistant',
                    mode: 'edit',
                    content: 'Hypothesis updated! The changes are now reflected on the left. You can use the arrows to navigate through edit history.',
                    timestamp: Date.now()
                });
            } else {
                placeholder.classList.add('chat-error');
                placeholder.querySelector('.chat-bubble-label').textContent = 'System';
                const resetHint = result.reset_hint ? ` ${result.reset_hint}` : '';
                placeholder.querySelector('.chat-bubble-content').innerHTML = formatMarkdown(escapeHtml(`Error: ${result.error}${resetHint}`));
                applyCodeHighlight(placeholder);
            }
        } else {
            // Normal chat mode
            const chatPayload = {
                message: askMessage,
                mode: chatMode
            };
            if (isLlmAsk && editingHypothesis && Number.isInteger(editingHypothesis.index)) {
                chatPayload.hypothesis_index = editingHypothesis.index;
            }
            result = await apiCallWithSignal('/api/chat', chatPayload, chatRequestController.signal);

            if (requestToken !== chatRequestToken || requestStateVersion !== uiStateVersion) {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                return;
            }

            if (result.success) {
                if (placeholder && placeholder.parentNode) {
                    placeholder.parentNode.removeChild(placeholder);
                }
                const thinkingText = typeof result.thinking === 'string' && result.thinking.trim()
                    ? result.thinking.trim()
                    : null;
                // Update citation documents cache
                if (result.documents) {
                    updateCitationDocuments(result.documents);
                }
                appendChatMessage({
                    role: 'assistant',
                    mode: result.mode,
                    content: result.reply,
                    thinking: thinkingText,
                    responseTimeMs: result.response_time_ms,
                    citations: result.citations || [],
                    documents: result.documents || citationDocuments
                });
            } else {
                placeholder.classList.add('chat-error');
                placeholder.querySelector('.chat-bubble-label').textContent = 'System';
                const resetHint = result.reset_hint ? ` ${result.reset_hint}` : '';
                placeholder.querySelector('.chat-bubble-content').innerHTML = formatMarkdown(escapeHtml(`Error: ${result.error}${resetHint}`));
                applyCodeHighlight(placeholder);
            }
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            if (placeholder && placeholder.parentNode) {
                placeholder.parentNode.removeChild(placeholder);
            }
            return;
        }
        if (requestToken !== chatRequestToken) {
            if (placeholder && placeholder.parentNode) {
                placeholder.parentNode.removeChild(placeholder);
            }
            return;
        }
        placeholder.classList.add('chat-error');
        placeholder.querySelector('.chat-bubble-label').textContent = 'System';
        placeholder.querySelector('.chat-bubble-content').innerHTML = formatMarkdown(escapeHtml(`Error: ${error.message}`));
        applyCodeHighlight(placeholder);
    } finally {
        if (requestToken === chatRequestToken) {
            chatRequestController = null;
            chatRequestInFlight = false;
            setChatSubmitPending(false);
        }
        queuePersistHypothesisChatState();
    }
}

function clearChatMessagesUi({ persistState = true } = {}) {
    chatMessages = [];
    const chatMessagesEl = document.getElementById('chat-messages');
    if (chatMessagesEl) {
        chatMessagesEl.innerHTML = '';
    }
    if (persistState) {
        persistHypothesisChatState();
    }
}

async function resetChatContext() {
    const confirmed = await window.V2Dialog.confirm({
        title: 'Reset conversation?',
        message: 'Resetting context will erase the current conversation you have with the agent.',
        confirmLabel: 'Reset',
    });
    if (!confirmed) {
        return;
    }
    cancelChatRequest('reset_chat_context');
    if (editMode) {
        clearEditHypothesisChatState(editingHypothesis ? editingHypothesis.index : null);
        clearChatMessagesUi({ persistState: false });
        appendChatMessage({
            role: 'assistant',
            mode: 'edit',
            content: `I'll help you refine this hypothesis. Use "Edit with LLM Chat" to apply changes, or "Ask with LLM Chat" to ask questions without editing. Manual edits are available if you prefer to type directly.`
        });
    } else {
        clearChatMessagesUi();
        ensureChatGreeting();
    }
    try {
        const payload = auditOnly
            ? { scope: 'report', report_type: 'background', report_view: 'summary' }
            : { scope: 'hypothesis' };
        await apiCall('/api/reset_chat_context', payload);
    } catch (error) {
        console.warn('Failed to reset chat context', error);
    }
}

if (chatToggleBtn) {
    chatToggleBtn.addEventListener('click', toggleChatPanelState);
    chatToggleBtn.setAttribute('aria-pressed', 'true');
}

if (chatCloseBtn) {
    chatCloseBtn.addEventListener('click', () => setChatPanelCollapsed(true));
}

const chatResetBtn = document.getElementById('chat-reset-btn');
if (chatResetBtn) {
    chatResetBtn.addEventListener('click', resetChatContext);
}
// Chat send button click handler
const chatSendBtn = document.getElementById('chat-send-btn');
if (chatSendBtn) {
    chatSendBtn.addEventListener('click', handleChatSubmit);
}

// Also handle Enter key in textarea (Shift+Enter for newline)
const chatInput = document.getElementById('chat-input');
if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleChatSubmit();
        }
    });
}

document.querySelectorAll('#chat-mode-toggle .chat-mode-button').forEach(btn => {
    btn.addEventListener('click', () => setChatMode(btn.dataset.mode));
});

const chatContextToggle = document.getElementById('chat-context-toggle');
if (chatContextToggle) {
    chatContextToggle.addEventListener('click', () => {
        const panel = document.getElementById('chat-context-content');
        if (!panel) return;
        panel.classList.toggle('collapsed');
        chatContextToggle.textContent = panel.classList.contains('collapsed') ? 'Expand' : 'Collapse';
    });
}

const chatMessagesEl = document.getElementById('chat-messages');
if (chatMessagesEl) {
    chatMessagesEl.addEventListener('scroll', () => {
        if (!chatCollapsed) {
            queuePersistHypothesisChatState();
        }
    });
}

// Edit mode button handlers
const cancelEditBtn = document.getElementById('cancel-edit-btn');
if (cancelEditBtn) {
    cancelEditBtn.addEventListener('click', () => {
        exitEditMode();
    });
}

// Edit type toggle buttons
const editModeLlmEditBtn = document.getElementById('edit-mode-llm-edit-btn');
const editModeLlmAskBtn = document.getElementById('edit-mode-llm-ask-btn');
const editModeManualBtn = document.getElementById('edit-mode-manual-btn');

if (editModeLlmEditBtn) {
    editModeLlmEditBtn.addEventListener('click', () => {
        if (currentEditType !== 'llm_edit') {
            setEditType('llm_edit');
        }
    });
}

if (editModeLlmAskBtn) {
    editModeLlmAskBtn.addEventListener('click', () => {
        if (currentEditType !== 'llm_ask') {
            setEditType('llm_ask');
        }
    });
}

if (editModeManualBtn) {
    editModeManualBtn.addEventListener('click', () => {
        if (currentEditType !== 'manual') {
            setEditType('manual');
        }
    });
}

// History navigation buttons
const historyBackBtn = document.getElementById('history-back-btn');
const historyForwardBtn = document.getElementById('history-forward-btn');

if (historyBackBtn) {
    historyBackBtn.addEventListener('click', () => {
        navigateHistory(-1);
    });
}

if (historyForwardBtn) {
    historyForwardBtn.addEventListener('click', () => {
        navigateHistory(1);
    });
}

// Manual edit buttons
const manualSaveBtn = document.getElementById('manual-save-btn');
const manualRevertBtn = document.getElementById('manual-revert-btn');

if (manualSaveBtn) {
    manualSaveBtn.addEventListener('click', () => {
        saveManualEdit();
    });
}

if (manualRevertBtn) {
    manualRevertBtn.addEventListener('click', () => {
        revertManualEdit();
    });
}

// Global handler for Final Accept button (called via onclick)
window.handleFinalAcceptClick = function() {
    console.log('=== handleFinalAcceptClick called ===');
    console.log('editingHypothesis:', editingHypothesis);
    console.log('editMode:', editMode);
    console.log('currentEditType:', currentEditType);

    if (!editingHypothesis) {
        console.error('No editingHypothesis set!');
        alert('Error: No hypothesis is currently being edited. Please try selecting a hypothesis again.');
        return;
    }

    // Show confirmation modal with hypothesis preview
    console.log('Calling showConfirmationModal...');
    showConfirmationModal();
};

// Also attach via addEventListener as backup
const finalAcceptBtn = document.getElementById('final-accept-btn');
if (finalAcceptBtn) {
    console.log('Final Accept button found, attaching event listener as backup');
    finalAcceptBtn.addEventListener('click', () => {
        console.log('Final Accept button clicked via addEventListener!');
        window.handleFinalAcceptClick();
    });
} else {
    console.error('Final Accept button not found in DOM!');
}

// Confirmation modal functions
function buildFullTextForConfirmation(hypothesis = {}) {
    const parts = [];
    if (hypothesis.title) {
        parts.push(`**Title**: ${hypothesis.title}`);
    }
    if (hypothesis.hypothesis) {
        parts.push(`**Hypothesis**: ${hypothesis.hypothesis}`);
    }
    if (hypothesis.justification) {
        parts.push(`**Justification**: ${hypothesis.justification}`);
    }
    if (hypothesis.plan) {
        parts.push(`**Plan**: ${hypothesis.plan}`);
    }
    return parts.join('\n\n');
}

function showConfirmationModal() {
    console.log('=== showConfirmationModal called ===');
    const overlay = document.getElementById('confirm-selection-overlay');
    const preview = document.getElementById('confirm-selection-preview');
    const statusEl = document.getElementById('confirm-selection-status');
    const confirmBtn = document.getElementById('confirm-selection-confirm-btn');

    console.log('overlay element:', overlay);
    console.log('preview element:', preview);
    console.log('editingHypothesis:', editingHypothesis);

    if (!overlay) {
        console.error('Confirmation overlay element not found!');
        alert('Error: Confirmation overlay not found. Please refresh the page.');
        return;
    }
    if (!preview) {
        console.error('Confirmation preview element not found!');
        alert('Error: Preview element not found. Please refresh the page.');
        return;
    }
    if (!editingHypothesis) {
        console.error('No editing hypothesis!');
        alert('Error: No hypothesis is being edited.');
        return;
    }

    const activeHypothesis = getEffectiveEditingHypothesis() || editingHypothesis;
    const baselineText = activeHypothesis.full_text || buildFullTextForConfirmation(activeHypothesis);
    confirmBaselineText = baselineText || '';
    const researchFeedbackInput = document.getElementById('research-feedback-input');
    const feedbackText = researchFeedbackInput ? researchFeedbackInput.value.trim() : '';
    const previewText = feedbackText
        ? `${baselineText || 'No content'}\n\n**Research Feedback**:\n${feedbackText}`
        : (baselineText || 'No content');

    if (preview) {
        let previewHtml = formatMarkdown(previewText);
        if (lastCitations && lastCitations.length > 0) {
            previewHtml = annotateTextWithCitations(previewHtml, lastCitations);
        }
        preview.innerHTML = previewHtml;
    }

    if (statusEl) {
        statusEl.textContent = '';
        statusEl.style.display = 'none';
    }
    if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Confirm Selection';
    }

    overlay.classList.remove('hidden');
}

// Make showConfirmationModal global for debugging
window.showConfirmationModal = showConfirmationModal;

function hideConfirmationModal() {
    const overlay = document.getElementById('confirm-selection-overlay');
    if (overlay) {
        overlay.classList.add('hidden');
    }

    hideStatus('confirm-selection-status');
    const confirmBtn = document.getElementById('confirm-selection-confirm-btn');
    if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Confirm Selection';
    }
}

function returnToEditing() {
    hideConfirmationModal();
    const editorPanel = document.querySelector('.editor-hypothesis-panel');
    if (editorPanel) {
        editorPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

async function proceedWithSelection() {
    const activeHypothesis = getEffectiveEditingHypothesis();
    if (!activeHypothesis) {
        alert('Error: No hypothesis selected.');
        return;
    }

    hideStatus('confirm-selection-status');

    const confirmBtn = document.getElementById('confirm-selection-confirm-btn');
    const researchFeedbackInput = document.getElementById('research-feedback-input');
    const agentGuidance = researchFeedbackInput ? researchFeedbackInput.value.trim() : '';
    const baselineText = (confirmBaselineText || '').trim();
    const finalizedText = (activeHypothesis.full_text || baselineText || '').trim();
    const selectionIndex = Number.isInteger(activeHypothesis.index)
        ? activeHypothesis.index
        : parseInt(activeHypothesis.index, 10);
    const selectionStateId = (
        typeof activeHypothesis.state_id === 'string'
            ? activeHypothesis.state_id.trim()
            : ''
    ) || null;
    const isCustomSelection = activeHypothesis.selectionType === 'custom'
        || activeHypothesis.index === 'custom'
        || activeHypothesis.localEdits === true;

    if (!selectionStateId) {
        console.warn('[selection] proceeding without state_id; relying on final_text payload');
    }
    if (!finalizedText) {
        showStatus('confirm-selection-status', 'Hypothesis text cannot be empty.', 'error');
        return;
    }

    const payload = {
        selection: isCustomSelection ? 'custom' : selectionIndex,
        agent_guidance: agentGuidance || null,
        final_text: finalizedText
    };
    if (!isCustomSelection && selectionStateId) {
        payload.selection_state_id = selectionStateId;
    }

    if (isCustomSelection) {
        payload.selection = 'custom';
        payload.custom_hypothesis = finalizedText;
        if (Number.isInteger(selectionIndex)) {
            payload.custom_source = 'base_edit';
            payload.base_hypothesis_index = selectionIndex;
            if (selectionStateId) {
                payload.base_hypothesis_state_id = selectionStateId;
            }
        } else {
            payload.custom_source = 'direct_custom';
        }
    }

    showLoading('Saving hypothesis selection to server, do not close browser');
    try {
        const result = await apiCall('/api/select', payload);

        if (result.success) {
            lastCitations = result.citations || [];
            updateCitationDocuments(result.documents);
            if (result.cleanup_ready) {
                setCleanupArmed(true);
            }

            const updatedText = result.full_text || activeHypothesis.full_text || baselineText;
            if (updatedText) {
                editingHypothesis.full_text = updatedText;
                const updatedSections = parseHypothesisSections(updatedText);
                editingHypothesis.title = updatedSections.title;
                editingHypothesis.hypothesis = updatedSections.hypothesis;
                editingHypothesis.justification = updatedSections.justification;
                editingHypothesis.interpretation = updatedSections.interpretation;
                editingHypothesis.plan = updatedSections.plan;
            }
            updateEditorDisplay();

            const preview = document.getElementById('confirm-selection-preview');
            if (preview) {
                let hypothesisHtml = formatMarkdown(result.hypothesis || result.full_text || editingHypothesis.full_text || baselineText);
                if (lastCitations.length > 0) {
                    hypothesisHtml = annotateTextWithCitations(hypothesisHtml, lastCitations);
                }
                const citationsHtml = renderCitations(lastCitations, citationDocuments, false);
                preview.innerHTML = `
                    <div class="hypothesis-text">${hypothesisHtml}</div>
                    ${citationsHtml ? `<div class="citations-section">${citationsHtml}</div>` : ''}
                    <p class="saved-path"><strong>Saved to:</strong> ${result.output_path}</p>
                    ${result.guidance_path ? `<p class="saved-path"><strong>Next-round guidance saved to:</strong> ${result.guidance_path}</p>` : ''}
                    ${result.log_path ? `<p class="saved-path"><strong>Session log:</strong> ${result.log_path}</p>` : ''}
                `;
            }

            clearAllHypothesisChatStates();
            window.location.href = '/activity_log';
        } else {
            showStatus('confirm-selection-status', `Error: ${result.error}`, 'error');
        }
    } catch (error) {
        showStatus('confirm-selection-status', `Error: ${error.message}`, 'error');
    } finally {
        hideLoading();
    }
}

// Confirmation modal button handlers
const confirmCancelBtn = document.getElementById('confirm-selection-cancel-btn');
if (confirmCancelBtn) {
    confirmCancelBtn.addEventListener('click', returnToEditing);
}

const confirmSelectionConfirmBtn = document.getElementById('confirm-selection-confirm-btn');
if (confirmSelectionConfirmBtn) {
    confirmSelectionConfirmBtn.addEventListener('click', () => {
        proceedWithSelection();
    });
}

const objectiveForm = document.getElementById('objective-form');
if (objectiveForm) {
    objectiveForm.addEventListener('submit', saveObjectiveAndContinue);
}
const objectiveCancelBtn = document.getElementById('objective-cancel-btn');
if (objectiveCancelBtn) {
    objectiveCancelBtn.addEventListener('click', () => closeObjectiveDialog());
}

// Modal close handlers
document.querySelectorAll('.modal-close').forEach(btn => {
    btn.addEventListener('click', (e) => {
        const modal = e.target.closest('.modal');
        if (!modal) return;
        if (modal.id === 'objective-modal') {
            closeObjectiveDialog();
            return;
        }
        modal.classList.add('hidden');
    });
});

// Close modals on background click
document.querySelectorAll('.modal').forEach(modal => {
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            if (modal.id === 'objective-modal') {
                closeObjectiveDialog();
                return;
            }
            modal.classList.add('hidden');
        }
    });
});

updateCodeModeAvailability();

// Auto-initialize if config provided via CLI
document.addEventListener('DOMContentLoaded', async () => {
    const config = window.serverConfig || {};
    auditOnly = !!config.auditOnly;
    if (auditOnly) {
        applyAuditOnlyLayout();
    }
    if (chatPanelEl) {
        hydrateHypothesisChatStateFromStorage();
    }

    // Debug: log the config to console
    console.log('Server config loaded:', config);

    logEvent('page_view', {
        path: window.location.pathname,
        search: window.location.search,
        referrer: document.referrer || null,
    });

    document.addEventListener('click', (event) => {
        const target = event.target.closest('[data-log-action], button, a, input, select, textarea');
        if (!target) return;
        logEvent('ui_click', {
            target: buildElementPayload(target),
        });
    }, { capture: true });

    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        logEvent('form_submit', {
            id: form.id || null,
            name: form.name || null,
            action: form.action || null,
            method: form.method || null,
        });
    }, { capture: true });

    window.addEventListener('error', (event) => {
        logEvent('client_error', {
            message: event.message || 'Unknown error',
            filename: event.filename || null,
            lineno: event.lineno || null,
            colno: event.colno || null,
        });
    });

    window.addEventListener('unhandledrejection', (event) => {
        logEvent('client_error', {
            message: event.reason ? String(event.reason) : 'Unhandled promise rejection',
        });
    });

    document.addEventListener('visibilitychange', () => {
        logEvent('visibility_change', { state: document.visibilityState });
    });

    window.addEventListener('beforeunload', () => {
        persistHypothesisChatState();
        if (typeof window.__persistActiveReportChatState === 'function') {
            window.__persistActiveReportChatState();
        }
        sendLogEvent('page_unload', { path: window.location.pathname }, true);
        if (isCleanupArmed()) {
            sendCleanupBeacon();
        }
    });

    const s3Ready = await waitForS3SyncIfNeeded();
    if (!s3Ready) {
        return;
    }

    // Set up event delegation for citation clicks and hovers
    document.body.addEventListener('click', function(e) {
        // Handle inline citation text clicks (new style - highlighted text)
        const inlineCit = e.target.closest('.citation-inline');
        if (inlineCit && inlineCit.dataset.cit) {
            e.preventDefault();
            e.stopPropagation();
            handleCitationClick(parseInt(inlineCit.dataset.cit, 10));
            return;
        }

        // Handle citation marker clicks (legacy [#] style)
        const marker = e.target.closest('.citation-marker');
        if (marker && marker.dataset.cit) {
            e.preventDefault();
            e.stopPropagation();
            handleCitationClick(parseInt(marker.dataset.cit, 10));
            return;
        }

        // Handle citation item clicks in the Sources Referenced list
        const citItem = e.target.closest('[data-cit-click]');
        if (citItem) {
            e.preventDefault();
            e.stopPropagation();
            handleCitationClick(parseInt(citItem.dataset.citClick, 10));
            return;
        }
    });

    // Checkpoint dir and readme path are now set server-side via hidden inputs
    // No need to manipulate them here

    // Set model selection from config
    const modelSelect = document.getElementById('hypothesis-model');
    if (modelSelect) {
        if (config.hypothesisModel) {
            modelSelect.value = config.hypothesisModel;
        } else if (!config.devMode) {
            modelSelect.value = 'claude-opus-4-6';
        }
    }

    const numInput = document.getElementById('num-hypotheses');
    if (config.numHypotheses && numInput) {
        numInput.value = config.numHypotheses;
    }

    initGithubButton(config.githubUrl || '');

    const readmeToggleBtn = document.getElementById('readme-toggle-btn');
    if (readmeToggleBtn) {
        readmeToggleBtn.addEventListener('click', () => {
            readmeExpanded = !readmeExpanded;
            renderReadmePreview();
        });
    }

    const readmeFullBtn = document.getElementById('readme-full-btn');
    if (readmeFullBtn) {
        readmeFullBtn.addEventListener('click', () => {
            window.open('/readme_view', '_blank', 'noopener');
        });
    }
    await loadReadmePreview();

    const backgroundCheckbox = document.getElementById('include-background-literature');
    if (backgroundCheckbox) {
        if (typeof config.includeBackgroundLiterature === 'boolean') {
            backgroundCheckbox.checked = !!config.includeBackgroundLiterature;
        } else if (!backgroundCheckbox.dataset.boundDefault) {
            backgroundCheckbox.checked = !config.devMode;
            backgroundCheckbox.dataset.boundDefault = 'true';
        }
    }

    const enableCitationsCheckbox = document.getElementById('enable-citations');
    if (enableCitationsCheckbox && typeof config.enableCitations === 'boolean') {
        enableCitationsCheckbox.checked = !!config.enableCitations;
    }


    const planCheckbox = document.getElementById('generate-plan');
    if (planCheckbox) {
        if (typeof config.generatePlan === 'boolean') {
            planCheckbox.checked = !!config.generatePlan;
        } else {
            planCheckbox.checked = !config.devMode;
        }
    }

    codeChatAvailable = !!config.codeChatAvailable;
    updateCodeModeAvailability();

    if (config.autoInitialize && config.checkpointDir) {
        console.log('Auto-initializing with CLI config...');
        await initializeSession('init-status');
    }
});
