(function () {
    const config = window.auditHomeConfig || {};
    const mainInterfaceEl = document.getElementById('main-interface');
    const workspaceEl = document.querySelector('[data-audit-scroll-region]');
    const chatMessagesEl = document.getElementById('chat-messages');
    const chatInputEl = document.getElementById('chat-input');
    const composerEl = document.getElementById('aa-composer');
    const queuedMessagesEl = document.getElementById('audit-queued-messages');
    const chatSendBtn = document.getElementById('chat-send-btn');
    const newChatBtn = document.getElementById('audit-new-chat-btn');
    const chatListEl = document.getElementById('audit-chat-list');
    const chatExportCurrentBtn = document.getElementById('chat-export-current-btn');
    const bootOverlayEl = document.getElementById('audit-loading-overlay');
    const bootSpinnerEl = document.getElementById('audit-loading-spinner');
    const bootMessageEl = document.getElementById('audit-loading-message');
    const bootRetryBtn = document.getElementById('audit-loading-retry-btn');
    const guideBtn = document.getElementById('audit-guide-btn');
    const guideModal = document.getElementById('audit-guide-modal');
    const guideCloseBtn = document.getElementById('audit-guide-close');
    const guideDismissBtn = document.getElementById('audit-guide-dismiss');

    let sessionReady = false;
    let initPromise = null;
    let initErrorMessage = '';
    let chatHistory = [];
    let chatSessions = [];
    let activeChatId = null;
    let renamingChatId = null;
    let deleteConfirmChatId = null;
    let pendingAuditJobs = [];
    let queuedAuditMessages = {};
    const cancelledAuditJobIds = new Set();
    const pendingChatIds = new Set();
    const pollingJobIds = new Set();
    const CHAT_STORAGE_VERSION = 1;
    const AUDIT_CHAT_STORAGE_MAX_CHATS = 50;
    const AUDIT_CHAT_STORAGE_MAX_MESSAGES = 120;
    const AUDIT_CHAT_STORAGE_MAX_CHARS = 350000;
    const chatStorageScope = (typeof config.chatStorageScope === 'string' && config.chatStorageScope.trim())
        ? config.chatStorageScope.trim()
        : 'anonymous';
    const auditChatStorageKey = `da_audit_chat_state_v${CHAT_STORAGE_VERSION}_${chatStorageScope}`;
    let durablePersistTimer = null;
    let durablePersistChain = Promise.resolve();
    let pendingDurableAuditChatSnapshot = null;
    const auditFigurePresentationCache = new Map();
    let auditMarkdownRenderSequence = 0;

    function renderMarkdown(text) {
        if (!text) return '';
        if (window.marked) {
            try { return marked.parse(text); } catch (_) {}
        }
        return text
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/\n/g, '<br>');
    }

    function highlightAssistantCode(element) {
        if (!window.hljs || !element) return;
        element.querySelectorAll('pre code').forEach(function(code) {
            hljs.highlightElement(code);
        });
    }

    function auditMarkdownWithoutImageDestinations(text) {
        return String(text || '').replace(
            /!\[([^\]\r\n]*)\]\([^\r\n]*?\)/g,
            function(_match, alt) { return `**Figure unavailable:** ${alt || 'Referenced figure'}`; },
        );
    }

    async function prepareAuditFigurePresentation(text) {
        if (auditFigurePresentationCache.has(text)) {
            return auditFigurePresentationCache.get(text);
        }
        const projectQuery = config.projectSlug
            ? `?project=${encodeURIComponent(config.projectSlug)}`
            : '';
        const request = fetch(`/api/audit_chat_figures${projectQuery}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ markdown: text }),
        }).then(async function(response) {
            const data = await response.json().catch(function() { return {}; });
            if (!response.ok || !data.success) {
                throw new Error(data.error || 'Could not prepare Audit figures');
            }
            return {
                displayText: typeof data.display_text === 'string' ? data.display_text : text,
                figures: Array.isArray(data.figures) ? data.figures : [],
            };
        });
        auditFigurePresentationCache.set(text, request);
        try {
            return await request;
        } catch (error) {
            auditFigurePresentationCache.delete(text);
            throw error;
        }
    }

    async function renderAuditAssistantMarkdown(element, text) {
        if (!element) return;
        const renderId = String(++auditMarkdownRenderSequence);
        element.dataset.auditMarkdownRenderId = renderId;
        element.setAttribute('aria-busy', 'true');
        element.textContent = '';
        try {
            const presentation = await prepareAuditFigurePresentation(text);
            if (!element.isConnected || element.dataset.auditMarkdownRenderId !== renderId) return;
            if (window.DiscoveryReportFigures) {
                window.DiscoveryReportFigures.renderReportMarkdown(
                    element, presentation.displayText, presentation.figures,
                );
            } else {
                element.innerHTML = renderMarkdown(presentation.displayText);
            }
        } catch (_error) {
            if (!element.isConnected || element.dataset.auditMarkdownRenderId !== renderId) return;
            const safeFallback = auditMarkdownWithoutImageDestinations(text);
            if (window.DiscoveryReportFigures) {
                window.DiscoveryReportFigures.renderMarkdown(element, safeFallback);
            } else {
                element.innerHTML = renderMarkdown(safeFallback);
            }
        } finally {
            if (element.dataset.auditMarkdownRenderId === renderId) {
                element.removeAttribute('aria-busy');
                highlightAssistantCode(element);
                scrollToBottomAfterLayout();
            }
        }
    }

    function normalizeStoredAuditChatMessages(messages) {
        if (!Array.isArray(messages)) return [];
        const normalized = [];
        for (const message of messages) {
            if (!message || typeof message !== 'object') continue;
            const role = message.role === 'user' ? 'user' : (message.role === 'assistant' ? 'assistant' : '');
            if (!role) continue;
            const text = typeof message.text === 'string' ? message.text.trim() : '';
            if (!text) continue;
            const responseTimeMs = Number.isFinite(message.responseTimeMs)
                ? Math.max(0, Math.round(message.responseTimeMs))
                : null;
            normalized.push({ role, text, responseTimeMs });
        }
        const bounded = normalized.length <= AUDIT_CHAT_STORAGE_MAX_MESSAGES
            ? normalized
            : normalized.slice(normalized.length - AUDIT_CHAT_STORAGE_MAX_MESSAGES);
        const compact = [];
        let charBudget = 0;
        for (let i = bounded.length - 1; i >= 0; i -= 1) {
            const entry = bounded[i];
            if (compact.length >= AUDIT_CHAT_STORAGE_MAX_MESSAGES) break;
            if (charBudget + entry.text.length > AUDIT_CHAT_STORAGE_MAX_CHARS) break;
            compact.unshift(entry);
            charBudget += entry.text.length;
        }
        return compact;
    }

    function createChatId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        return `audit-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function createAuditJobId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID().replace(/-/g, '');
        }
        const randomPart = Math.random().toString(16).slice(2).padEnd(16, '0').slice(0, 16);
        return `${Date.now().toString(16).padStart(16, '0')}${randomPart}`.slice(0, 32);
    }

    function createEmptyChat() {
        const now = Date.now();
        return {
            id: createChatId(),
            title: 'New chat',
            titleEdited: false,
            pinned: false,
            unread: false,
            createdAt: now,
            updatedAt: now,
            messages: [],
        };
    }

    function normalizePendingAuditJobs(jobs, chats) {
        if (!Array.isArray(jobs)) return [];
        const chatIds = new Set((Array.isArray(chats) ? chats : []).map(function(chat) { return chat.id; }));
        const seen = new Set();
        const cutoff = Date.now() - (2 * 60 * 60 * 1000);
        const normalized = [];
        jobs.forEach(function(job) {
            if (!job || typeof job !== 'object') return;
            const jobId = typeof job.jobId === 'string' ? job.jobId.trim() : '';
            const chatId = typeof job.chatId === 'string' ? job.chatId.trim() : '';
            if (!jobId || !chatId || !chatIds.has(chatId) || seen.has(jobId)) return;
            const createdAt = Number.isFinite(job.createdAt) ? job.createdAt : Date.now();
            if (createdAt < cutoff) return;
            seen.add(jobId);
            normalized.push({
                jobId,
                chatId,
                message: typeof job.message === 'string' ? job.message : '',
                createdAt,
            });
        });
        return normalized;
    }

    function normalizeQueuedAuditMessages(queues, chats) {
        if (!queues || typeof queues !== 'object') return {};
        const chatIds = new Set((Array.isArray(chats) ? chats : []).map(function(chat) { return chat.id; }));
        const normalized = {};
        Object.keys(queues).forEach(function(chatId) {
            if (!chatIds.has(chatId) || !Array.isArray(queues[chatId])) return;
            const messages = queues[chatId]
                .map(function(entry) {
                    if (typeof entry === 'string') {
                        return { text: entry.trim(), createdAt: Date.now() };
                    }
                    if (!entry || typeof entry !== 'object') return null;
                    const text = typeof entry.text === 'string' ? entry.text.trim() : '';
                    if (!text) return null;
                    return {
                        text,
                        createdAt: Number.isFinite(entry.createdAt) ? entry.createdAt : Date.now(),
                    };
                })
                .filter(Boolean)
                .slice(0, 20);
            if (messages.length > 0) normalized[chatId] = messages;
        });
        return normalized;
    }

    function getQueuedAuditMessages(chatId) {
        return Array.isArray(queuedAuditMessages[chatId]) ? queuedAuditMessages[chatId] : [];
    }

    function queueAuditMessage(chatId, text) {
        const messageText = (text || '').trim();
        if (!chatId || !messageText) return;
        const queue = getQueuedAuditMessages(chatId).slice();
        queue.push({ text: messageText, createdAt: Date.now() });
        queuedAuditMessages[chatId] = queue;
    }

    function shiftQueuedAuditMessage(chatId) {
        const queue = getQueuedAuditMessages(chatId).slice();
        const next = queue.shift() || null;
        if (queue.length > 0) {
            queuedAuditMessages[chatId] = queue;
        } else {
            delete queuedAuditMessages[chatId];
        }
        return next;
    }

    function removeQueuedAuditMessage(chatId, index) {
        const queue = getQueuedAuditMessages(chatId).slice();
        if (index < 0 || index >= queue.length) return;
        queue.splice(index, 1);
        if (queue.length > 0) {
            queuedAuditMessages[chatId] = queue;
        } else {
            delete queuedAuditMessages[chatId];
        }
    }

    function rebuildPendingChatIds() {
        pendingChatIds.clear();
        pendingAuditJobs.forEach(function(job) {
            if (job && job.chatId) pendingChatIds.add(job.chatId);
        });
    }

    function addPendingAuditJob(job) {
        pendingAuditJobs = pendingAuditJobs.filter(function(entry) {
            return entry.jobId !== job.jobId && entry.chatId !== job.chatId;
        });
        pendingAuditJobs.push(job);
        rebuildPendingChatIds();
    }

    function removePendingAuditJob(jobId) {
        pendingAuditJobs = pendingAuditJobs.filter(function(job) { return job.jobId !== jobId; });
        rebuildPendingChatIds();
    }

    function isUnlistedDraftChat(chat) {
        if (!chat || typeof chat !== 'object') return false;
        if (pendingChatIds.has(chat.id)) return false;
        if (chat.pinned || chat.unread || chat.titleEdited) return false;
        const title = typeof chat.title === 'string' && chat.title.trim() ? chat.title.trim() : 'New chat';
        return title === 'New chat' && normalizeStoredAuditChatMessages(chat.messages).length === 0;
    }

    function cleanupUnlistedDraftChats(keepChatId) {
        chatSessions = chatSessions.filter(function(chat) {
            return chat.id === keepChatId || !isUnlistedDraftChat(chat);
        });
    }

    function deriveChatTitle(messages) {
        const firstUserMessage = normalizeStoredAuditChatMessages(messages).find(function(message) {
            return message.role === 'user';
        });
        if (!firstUserMessage) return 'New chat';
        const compact = firstUserMessage.text.replace(/\s+/g, ' ').trim();
        if (!compact) return 'New chat';
        return compact.length > 46 ? `${compact.slice(0, 43)}...` : compact;
    }

    function normalizeChatSession(chat, fallbackIndex) {
        if (!chat || typeof chat !== 'object') return null;
        const messages = normalizeStoredAuditChatMessages(chat.messages);
        const createdAt = Number.isFinite(chat.createdAt) ? chat.createdAt : Date.now() - fallbackIndex;
        const updatedAt = Number.isFinite(chat.updatedAt) ? chat.updatedAt : createdAt;
        const id = typeof chat.id === 'string' && chat.id.trim() ? chat.id.trim() : createChatId();
        const title = typeof chat.title === 'string' && chat.title.trim()
            ? chat.title.trim()
            : deriveChatTitle(messages);
        return {
            id,
            title,
            titleEdited: chat.titleEdited === true,
            pinned: chat.pinned === true,
            unread: chat.unread === true,
            createdAt,
            updatedAt,
            messages,
        };
    }

    function sortChatSessions() {
        chatSessions.sort(function(a, b) {
            if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
            return (b.updatedAt || 0) - (a.updatedAt || 0);
        });
    }

    function getActiveChat() {
        return chatSessions.find(function(chat) { return chat.id === activeChatId; }) || null;
    }

    function ensureActiveChat() {
        let chat = getActiveChat();
        if (chat) return chat;
        chat = createEmptyChat();
        chatSessions.unshift(chat);
        activeChatId = chat.id;
        return chat;
    }

    function syncActiveChatFromHistory(options) {
        const syncOptions = options || {};
        const chat = ensureActiveChat();
        chat.messages = normalizeStoredAuditChatMessages(chatHistory);
        if (!chat.titleEdited) {
            chat.title = deriveChatTitle(chat.messages);
        }
        if (syncOptions.touch !== false) {
            chat.updatedAt = Date.now();
        }
        sortChatSessions();
        return chat;
    }

    function updateActiveChatTitleFromHistory() {
        const chat = getActiveChat();
        if (!chat || chat.titleEdited) return;
        const nextTitle = deriveChatTitle(chatHistory);
        if (nextTitle && nextTitle !== 'New chat') {
            chat.title = nextTitle;
        }
    }

    function normalizeAuditChatStatePayload(parsed) {
            if (!parsed || typeof parsed !== 'object') return null;
            if (Array.isArray(parsed.chats)) {
                const chats = parsed.chats
                    .map(function(chat, index) { return normalizeChatSession(chat, index); })
                    .filter(Boolean)
                    .slice(0, AUDIT_CHAT_STORAGE_MAX_CHATS);
                if (chats.length === 0) return null;
                const storedActiveChatId = typeof parsed.activeChatId === 'string' ? parsed.activeChatId : '';
                const selectedChat = chats.find(function(chat) { return chat.id === storedActiveChatId; }) || chats[0];
                return {
                    version: CHAT_STORAGE_VERSION,
                    updatedAt: Number.isFinite(parsed.updatedAt) ? parsed.updatedAt : 0,
                    chats,
                    activeChatId: selectedChat.id,
                    pendingJobs: normalizePendingAuditJobs(parsed.pendingJobs, chats),
                    queuedMessages: normalizeQueuedAuditMessages(parsed.queuedMessages, chats),
                };
            }
            const legacyMessages = normalizeStoredAuditChatMessages(parsed.messages);
            if (legacyMessages.length > 0) {
                const migratedChat = createEmptyChat();
                migratedChat.messages = legacyMessages;
                migratedChat.title = deriveChatTitle(legacyMessages);
                return { version: CHAT_STORAGE_VERSION, updatedAt: 0, chats: [migratedChat], activeChatId: migratedChat.id, pendingJobs: [], queuedMessages: {} };
            }
            return null;
    }

    function readAuditChatStateAtKey(storageKey) {
        try {
            const raw = localStorage.getItem(storageKey);
            if (!raw) return null;
            return normalizeAuditChatStatePayload(JSON.parse(raw));
        } catch (_) {
            return null;
        }
    }

    function legacyAuditChatStorageKeys() {
        const configured = Array.isArray(config.legacyChatStorageScopes)
            ? config.legacyChatStorageScopes
                .map(function(scope) { return String(scope || '').trim(); })
                .filter(Boolean)
                .map(function(scope) { return `da_audit_chat_state_v${CHAT_STORAGE_VERSION}_${scope}`; })
            : [];
        const projectSuffix = config.projectSlug ? `:v2:${config.projectSlug}` : '';
        try {
            for (let i = 0; i < localStorage.length; i += 1) {
                const key = localStorage.key(i);
                if (!key || !key.startsWith(`da_audit_chat_state_v${CHAT_STORAGE_VERSION}_`)) continue;
                if (projectSuffix && key.endsWith(projectSuffix)) configured.push(key);
            }
        } catch (_) {}
        return Array.from(new Set(configured)).filter(function(key) { return key !== auditChatStorageKey; });
    }

    function hasMeaningfulAuditChatState(candidate) {
        return !!(candidate && candidate.state && Array.isArray(candidate.state.chats) && candidate.state.chats.length);
    }

    function readAuditChatStateFromStorage() {
        const durable = normalizeAuditChatStatePayload(config.durableChatState);
        const current = readAuditChatStateAtKey(auditChatStorageKey);
        const recovered = legacyAuditChatStorageKeys()
            .map(function(storageKey) { return { storageKey, state: readAuditChatStateAtKey(storageKey) }; })
            .filter(hasMeaningfulAuditChatState);
        const candidates = [
            durable ? { source: 'durable', state: durable } : null,
            current ? { source: 'current', state: current } : null,
            ...recovered.map(function(entry) { return { source: entry.storageKey, state: entry.state }; }),
        ].filter(Boolean).sort(function(a, b) {
            return (Number(b.state.updatedAt) || 0) - (Number(a.state.updatedAt) || 0);
        });
        if (!candidates.length) return null;
        const selected = candidates[0];
        try {
            localStorage.setItem(auditChatStorageKey, JSON.stringify(selected.state));
        } catch (_) {}
        // If page navigation happened before the debounced PUT, the newer
        // project-local browser snapshot repairs the durable project copy.
        if (selected.source !== 'durable') queueDurableAuditChatState(selected.state);
        return selected.state;
    }

    function writeDurableAuditChatState(snapshot, keepalive) {
        const projectQuery = `?project=${encodeURIComponent(config.projectSlug)}`;
        return fetch('/api/conversation_state' + projectQuery, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            keepalive: keepalive === true,
            body: JSON.stringify({
                surface: 'audit_agent',
                scope: 'main',
                project: config.projectSlug,
                state: snapshot,
            }),
        });
    }

    function queueDurableAuditChatState(state) {
        if (!config.projectSlug || !state || typeof state !== 'object') return;
        if (durablePersistTimer) window.clearTimeout(durablePersistTimer);
        const snapshot = JSON.parse(JSON.stringify(state));
        pendingDurableAuditChatSnapshot = snapshot;
        durablePersistTimer = window.setTimeout(function() {
            durablePersistTimer = null;
            pendingDurableAuditChatSnapshot = null;
            durablePersistChain = durablePersistChain
                .catch(function() {})
                .then(function() {
                    return writeDurableAuditChatState(snapshot, false);
                })
                .then(function(response) {
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                })
                .catch(function(error) {
                    console.warn('[audit-agent] durable conversation save failed', error);
                });
        }, 120);
    }

    window.addEventListener('pagehide', function() {
        if (!pendingDurableAuditChatSnapshot) return;
        if (durablePersistTimer) window.clearTimeout(durablePersistTimer);
        durablePersistTimer = null;
        const snapshot = pendingDurableAuditChatSnapshot;
        pendingDurableAuditChatSnapshot = null;
        writeDurableAuditChatState(snapshot, true).catch(function() {});
    });

    function persistAuditChatState(options) {
        const persistOptions = options || {};
        try {
            if (!persistOptions.skipSync) {
                syncActiveChatFromHistory({ touch: persistOptions.touchActive !== false });
            }
            // The fresh blank composer is intentionally recreated on every
            // visit; do not let it consume a durable History slot and evict an
            // actual prior conversation merely because the user navigated.
            const chats = chatSessions
                .filter(function(chat) { return !isUnlistedDraftChat(chat); })
                .slice(0, AUDIT_CHAT_STORAGE_MAX_CHATS);
            const persistedActiveChatId = chats.some(function(chat) { return chat.id === activeChatId; })
                ? activeChatId
                : null;
            const state = {
                version: CHAT_STORAGE_VERSION,
                updatedAt: Date.now(),
                activeChatId: persistedActiveChatId,
                chats,
                pendingJobs: normalizePendingAuditJobs(pendingAuditJobs, chats),
                queuedMessages: normalizeQueuedAuditMessages(queuedAuditMessages, chats),
            };
            localStorage.setItem(auditChatStorageKey, JSON.stringify(state));
            queueDurableAuditChatState(state);
        } catch (_) {}
    }

    function renderChatList() {
        if (!chatListEl) return;
        sortChatSessions();
        while (chatListEl.firstChild) chatListEl.removeChild(chatListEl.firstChild);
        chatSessions.filter(function(chat) { return !isUnlistedDraftChat(chat); }).forEach(function(chat) {
            const item = document.createElement('div');
            item.className = 'aa-chat-item';
            item.classList.toggle('is-active', chat.id === activeChatId);
            item.classList.toggle('is-pinned', chat.pinned === true);
            item.classList.toggle('is-renaming', chat.id === renamingChatId);
            item.classList.toggle('is-confirming-delete', chat.id === deleteConfirmChatId);
            item.dataset.chatId = chat.id;

            if (chat.id === deleteConfirmChatId) {
                const confirmWrap = document.createElement('div');
                confirmWrap.className = 'aa-chat-delete-confirm';

                const promptText = document.createElement('span');
                promptText.className = 'aa-chat-delete-text';
                promptText.textContent = chat.title || 'New chat';

                const cancelButton = document.createElement('button');
                cancelButton.type = 'button';
                cancelButton.className = 'aa-chat-confirm-cancel';
                cancelButton.textContent = 'Cancel';
                cancelButton.addEventListener('click', function(e) {
                    e.stopPropagation();
                    deleteConfirmChatId = null;
                    renderChatList();
                });

                const deleteButton = document.createElement('button');
                deleteButton.type = 'button';
                deleteButton.className = 'aa-chat-confirm-delete';
                deleteButton.textContent = 'Delete';
                deleteButton.addEventListener('click', function(e) {
                    e.stopPropagation();
                    confirmDeleteChat(chat.id);
                });

                confirmWrap.appendChild(promptText);
                confirmWrap.appendChild(cancelButton);
                confirmWrap.appendChild(deleteButton);
                item.appendChild(confirmWrap);
                chatListEl.appendChild(item);
                return;
            }

            if (chat.id === renamingChatId) {
                const renameForm = document.createElement('form');
                renameForm.className = 'aa-chat-rename-form';

                const renameInput = document.createElement('input');
                renameInput.className = 'aa-chat-rename-input';
                renameInput.type = 'text';
                renameInput.value = chat.title || 'New chat';
                renameInput.setAttribute('aria-label', 'Rename chat');
                renameInput.autocomplete = 'off';

                renameForm.appendChild(renameInput);
                renameForm.addEventListener('submit', function(e) {
                    e.preventDefault();
                    commitRenameChat(chat.id, renameInput.value);
                });
                renameInput.addEventListener('keydown', function(e) {
                    if (e.key === 'Escape') {
                        e.preventDefault();
                        renamingChatId = null;
                        renderChatList();
                    }
                });
                renameInput.addEventListener('blur', function() {
                    commitRenameChat(chat.id, renameInput.value);
                });

                item.appendChild(renameForm);
                chatListEl.appendChild(item);
                requestAnimationFrame(function() {
                    renameInput.focus();
                    renameInput.select();
                });
                return;
            }

            const titleButton = document.createElement('button');
            titleButton.type = 'button';
            titleButton.className = 'aa-chat-title-btn';

            const title = document.createElement('span');
            title.className = 'aa-chat-title';
            title.textContent = chat.title || 'New chat';

            titleButton.appendChild(title);
            titleButton.addEventListener('click', function() {
                switchChat(chat.id);
            });

            let unreadDot = null;
            if (chat.unread === true && chat.id !== activeChatId) {
                unreadDot = document.createElement('span');
                unreadDot.className = 'aa-chat-unread-dot';
                unreadDot.title = 'Unread message';
                unreadDot.setAttribute('aria-label', 'Unread message');
            }

            let pinIcon = null;
            if (chat.pinned) {
                pinIcon = document.createElement('span');
                pinIcon.className = 'aa-chat-pin-icon';
                pinIcon.title = 'Pinned';
                pinIcon.innerHTML = '<svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true" focusable="false"><path d="M12.8 2.6c.3-.3.8-.3 1.1 0l3.5 3.5c.3.3.3.8 0 1.1l-1.6 1.6c-.2.2-.4.2-.7.2l-2.5-.3-2.4 2.4.5 3.2c0 .3-.1.6-.3.8l-.8.8-3.1-3.1-3.3 3.3c-.3.3-.8.3-1.1 0s-.3-.8 0-1.1l3.3-3.3-3.1-3.1.8-.8c.2-.2.5-.3.8-.3l3.2.5 2.4-2.4-.3-2.5c0-.2.1-.5.2-.7l1.4-1.8Z" fill="currentColor"/></svg>';
            }

            const menuWrap = document.createElement('div');
            menuWrap.className = 'aa-chat-menu-wrap';

            const menuButton = document.createElement('button');
            menuButton.type = 'button';
            menuButton.className = 'aa-chat-menu-button';
            menuButton.setAttribute('aria-label', `Chat options for ${chat.title || 'New chat'}`);
            menuButton.setAttribute('aria-haspopup', 'menu');
            menuButton.setAttribute('aria-expanded', 'false');
            menuButton.innerHTML = '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true" focusable="false"><circle cx="4" cy="9" r="1.15" fill="currentColor"/><circle cx="9" cy="9" r="1.15" fill="currentColor"/><circle cx="14" cy="9" r="1.15" fill="currentColor"/></svg>';

            const menu = document.createElement('div');
            menu.className = 'aa-chat-menu';
            menu.setAttribute('role', 'menu');

            function addMenuItem(label, handler, extraClass) {
                const option = document.createElement('button');
                option.type = 'button';
                option.className = extraClass ? `aa-chat-menu-item ${extraClass}` : 'aa-chat-menu-item';
                option.setAttribute('role', 'menuitem');
                option.textContent = label;
                option.addEventListener('click', function(e) {
                    e.stopPropagation();
                    closeChatMenus();
                    handler();
                });
                menu.appendChild(option);
            }

            addMenuItem('Rename', function() { renameChat(chat.id); });
            addMenuItem(chat.pinned ? 'Unpin chat' : 'Pin chat', function() { togglePinChat(chat.id); });
            addMenuItem('Delete', function() { requestDeleteChat(chat.id); }, 'is-danger');

            menuButton.addEventListener('click', function(e) {
                e.stopPropagation();
                const isOpen = item.classList.contains('is-menu-open');
                closeChatMenus();
                if (!isOpen) {
                    item.classList.add('is-menu-open');
                    menuButton.setAttribute('aria-expanded', 'true');
                }
            });

            menuWrap.appendChild(menuButton);
            menuWrap.appendChild(menu);
            item.appendChild(titleButton);
            if (unreadDot) item.appendChild(unreadDot);
            if (pinIcon) item.appendChild(pinIcon);
            item.appendChild(menuWrap);
            chatListEl.appendChild(item);
        });
    }

    function closeChatMenus() {
        if (!chatListEl) return;
        chatListEl.querySelectorAll('.aa-chat-item.is-menu-open').forEach(function(item) {
            item.classList.remove('is-menu-open');
            const button = item.querySelector('.aa-chat-menu-button');
            if (button) button.setAttribute('aria-expanded', 'false');
        });
    }

    function revealActiveChatRow() {
        if (!chatListEl || !activeChatId) return;
        const activeRow = Array.from(chatListEl.querySelectorAll('.aa-chat-item')).find(function(row) {
            return row.dataset.chatId === activeChatId;
        });
        if (activeRow) {
            activeRow.scrollIntoView({ block: 'nearest' });
        }
    }

    function clearChatBubbles() {
        while (chatMessagesEl && chatMessagesEl.firstChild) chatMessagesEl.removeChild(chatMessagesEl.firstChild);
    }

    function renderQueuedMessages() {
        if (!queuedMessagesEl) return;
        while (queuedMessagesEl.firstChild) queuedMessagesEl.removeChild(queuedMessagesEl.firstChild);
        const queued = getQueuedAuditMessages(activeChatId);
        queuedMessagesEl.classList.toggle('hidden', queued.length === 0);
        queued.forEach(function(entry, index) {
            const item = document.createElement('div');
            item.className = 'aa-queued-item';
            item.innerHTML = '<svg class="aa-queued-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M3.5 4.3h5.2c2 0 3.6 1.6 3.6 3.6v.7l1.2-1.2.9.9-2.7 2.7L9 8.3l.9-.9 1.2 1.2v-.7c0-1.3-1.1-2.4-2.4-2.4H3.5V4.3Z" fill="currentColor"/></svg>';
            const text = document.createElement('span');
            text.className = 'aa-queued-text';
            text.textContent = entry.text || '';
            const status = document.createElement('span');
            status.className = 'aa-queued-status';
            status.textContent = 'Queued';
            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.className = 'aa-queued-delete';
            deleteButton.setAttribute('aria-label', 'Remove queued message');
            deleteButton.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M6.2 2.4h3.6l.5 1.1h2.5v1.2H3.2V3.5h2.5l.5-1.1Zm1 .9-.1.2h1.8l-.1-.2H7.2ZM4.2 5.6h7.6l-.4 7.2c0 .7-.6 1.2-1.3 1.2H5.9c-.7 0-1.2-.5-1.3-1.2L4.2 5.6Zm1.3 1.2.3 5.9c0 .1.1.2.2.2h4.2c.1 0 .2-.1.2-.2l.3-5.9H5.5Zm1.4 1h1.1v4H6.9v-4Zm2.1 0h1.1v4H9v-4Z" fill="currentColor"/></svg>';
            deleteButton.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                removeQueuedAuditMessage(activeChatId, index);
                persistAuditChatState({ touchActive: true });
                syncEmptyState();
                if (chatInputEl) chatInputEl.focus();
            });
            item.appendChild(text);
            item.appendChild(status);
            item.appendChild(deleteButton);
            queuedMessagesEl.appendChild(item);
        });
    }

    function syncEmptyState() {
        if (!mainInterfaceEl) return;
        const hasQueue = getQueuedAuditMessages(activeChatId).length > 0;
        mainInterfaceEl.classList.toggle('is-empty', chatHistory.length === 0 && !pendingChatIds.has(activeChatId) && !hasQueue);
        if (chatExportCurrentBtn) {
            chatExportCurrentBtn.disabled = chatHistory.length === 0;
        }
        renderQueuedMessages();
    }

    function renderStoredChatHistory(entries) {
        clearChatBubbles();
        entries.forEach(function(entry) {
            appendTurn(entry.role, entry.text, false, entry.responseTimeMs);
        });
        if (pendingChatIds.has(activeChatId)) {
            appendTurn('assistant', '', true, null);
        }
        syncEmptyState();
        scrollToBottomAfterLayout();
    }

    function isActiveChatPending() {
        return !!activeChatId && pendingChatIds.has(activeChatId);
    }

    function refreshSendEnabled() {
        if (!chatSendBtn) return;
        const isPending = isActiveChatPending();
        const hasMessage = !!(chatInputEl && (chatInputEl.value || '').trim());
        chatSendBtn.classList.toggle('is-stop', isPending);
        chatSendBtn.setAttribute('aria-label', isPending ? 'Cancel audit response' : 'Send message');
        chatSendBtn.innerHTML = isPending
            ? '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false"><rect x="5.25" y="5.25" width="9.5" height="9.5" rx="1.8" fill="currentColor"/></svg>'
            : '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false"><path d="M10 4.2 4.9 9.3l1.2 1.2 3-3V16h1.8V7.5l3 3 1.2-1.2L10 4.2Z" fill="currentColor"/></svg>';
        chatSendBtn.disabled = !chatInputEl || chatInputEl.disabled || (!isPending && !hasMessage);
    }

    function updateComposerForActiveChat() {
        setComposerEnabled(true);
        renderQueuedMessages();
    }

    async function activateChat(chatId, options) {
        const activateOptions = options || {};
        renamingChatId = null;
        deleteConfirmChatId = null;
        if (activeChatId && activeChatId !== chatId && isUnlistedDraftChat(getActiveChat())) {
            cleanupUnlistedDraftChats(chatId);
            activeChatId = null;
        }
        if (activeChatId && !activateOptions.skipPersistCurrent) {
            persistAuditChatState({ touchActive: false });
        }
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return;
        activeChatId = chat.id;
        chat.unread = false;
        chatHistory = normalizeStoredAuditChatMessages(chat.messages);
        clearChatBubbles();
        renderStoredChatHistory(chatHistory);
        renderChatList();
        revealActiveChatRow();
        scrollToBottomAfterLayout();
        if (sessionReady && !activateOptions.skipServerRestore && pendingChatIds.size === 0) {
            chatInputEl.disabled = true;
            chatSendBtn.disabled = true;
            await resetAuditConversationOnServer();
            await restoreAuditConversationOnServer(chatHistory);
            chatInputEl.disabled = false;
            refreshSendEnabled();
            chatInputEl.focus();
        } else {
            updateComposerForActiveChat();
        }
        persistAuditChatState({ touchActive: false });
    }

    async function switchChat(chatId) {
        if (chatId === activeChatId) return;
        await activateChat(chatId);
    }

    async function startNewChat() {
        renamingChatId = null;
        deleteConfirmChatId = null;
        if (activeChatId && !isUnlistedDraftChat(getActiveChat())) {
            persistAuditChatState({ touchActive: false });
        }
        cleanupUnlistedDraftChats(null);
        const chat = createEmptyChat();
        chatSessions.unshift(chat);
        const newChatId = chat.id;

        function selectNewChatLocally() {
            activeChatId = newChatId;
            chatHistory = [];
            chat.messages = [];
            clearChatBubbles();
            syncEmptyState();
            persistAuditChatState({ touchActive: false });
            renderChatList();
            revealActiveChatRow();
        }

        selectNewChatLocally();
        if (sessionReady && pendingChatIds.size === 0) {
            chatInputEl.disabled = true;
            chatSendBtn.disabled = true;
            await resetAuditConversationOnServer();
            selectNewChatLocally();
            chatInputEl.disabled = false;
            refreshSendEnabled();
            chatInputEl.focus();
        } else {
            updateComposerForActiveChat();
        }
    }

    async function restoreAuditConversationOnServer(entries) {
        if (!Array.isArray(entries) || entries.length === 0) return;
        const payloadMessages = normalizeStoredAuditChatMessages(entries).map(function(entry) {
            return { role: entry.role, content: entry.text };
        });
        if (payloadMessages.length === 0) return;
        try {
            await fetch('/api/audit_chat_restore', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ messages: payloadMessages }),
            });
        } catch (_) {}
    }

    async function resetAuditConversationOnServer() {
        await fetch('/api/reset_chat_context', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ scope: 'report', report_type: 'background', report_view: 'summary' }),
        }).catch(() => {});
    }

    function showBootOverlay(message, options) {
        const overlayConfig = options || {};
        if (bootMessageEl && typeof message === 'string') bootMessageEl.textContent = message;
        if (bootSpinnerEl) bootSpinnerEl.classList.toggle('hidden', !!overlayConfig.hideSpinner);
        if (bootRetryBtn) {
            bootRetryBtn.classList.toggle('hidden', !overlayConfig.allowRetry);
            bootRetryBtn.disabled = false;
        }
        if (bootOverlayEl) bootOverlayEl.classList.remove('hidden');
    }

    function hideBootOverlay() {
        if (bootOverlayEl) bootOverlayEl.classList.add('hidden');
        if (bootSpinnerEl) bootSpinnerEl.classList.remove('hidden');
        if (bootRetryBtn) {
            bootRetryBtn.classList.add('hidden');
            bootRetryBtn.disabled = false;
        }
    }

    function revealMainInterface() {
        if (mainInterfaceEl) mainInterfaceEl.classList.remove('hidden');
    }

    function concealMainInterface() {
        if (mainInterfaceEl) mainInterfaceEl.classList.add('hidden');
    }

    function showBootError(message) {
        showBootOverlay(message, { hideSpinner: true, allowRetry: true });
    }

    function exportChat(chat) {
        const targetChat = chat || getActiveChat();
        const messages = normalizeStoredAuditChatMessages(
            targetChat && targetChat.id === activeChatId ? chatHistory : (targetChat && targetChat.messages)
        );
        if (messages.length === 0) {
            alert('Nothing to export yet.');
            return;
        }
        const title = targetChat && targetChat.title ? targetChat.title : 'Audit Chat';
        const lines = [`# ${title}`, ''];
        messages.forEach(function(entry) {
            lines.push(entry.role === 'user' ? '<user>' : '<assistant>');
            lines.push(entry.text);
            lines.push(entry.role === 'user' ? '</user>' : '</assistant>');
            lines.push('');
        });
        const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        a.download = `audit_chat_export_${ts}.md`;
        a.click();
        URL.revokeObjectURL(url);
    }

    function renameChat(chatId) {
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return;
        closeChatMenus();
        deleteConfirmChatId = null;
        renamingChatId = chat.id;
        renderChatList();
    }

    function commitRenameChat(chatId, nextTitle) {
        if (renamingChatId !== chatId) return;
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return;
        const trimmed = nextTitle.trim();
        if (trimmed) {
            chat.title = trimmed.length > 64 ? trimmed.slice(0, 64) : trimmed;
            chat.titleEdited = true;
            chat.updatedAt = Date.now();
            persistAuditChatState({ touchActive: chat.id === activeChatId });
        }
        renamingChatId = null;
        renderChatList();
    }

    function togglePinChat(chatId) {
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return;
        chat.pinned = !chat.pinned;
        deleteConfirmChatId = null;
        renamingChatId = null;
        persistAuditChatState({ touchActive: chat.id === activeChatId });
        renderChatList();
    }

    function requestDeleteChat(chatId) {
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return;
        closeChatMenus();
        renamingChatId = null;
        deleteConfirmChatId = chat.id;
        renderChatList();
    }

    async function confirmDeleteChat(chatId) {
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return;
        deleteConfirmChatId = null;
        renamingChatId = null;
        const deletingActiveChat = chatId === activeChatId;
        chatSessions = chatSessions.filter(function(entry) { return entry.id !== chatId; });
        pendingAuditJobs = pendingAuditJobs.filter(function(job) { return job.chatId !== chatId; });
        delete queuedAuditMessages[chatId];
        rebuildPendingChatIds();
        sortChatSessions();
        if (deletingActiveChat) {
            const freshChat = createEmptyChat();
            chatSessions.unshift(freshChat);
            activeChatId = freshChat.id;
            chatHistory = [];
            clearChatBubbles();
            syncEmptyState();
            renderChatList();
            persistAuditChatState({ touchActive: false });
            if (sessionReady) {
                chatInputEl.disabled = true;
                chatSendBtn.disabled = true;
                await resetAuditConversationOnServer();
                chatInputEl.disabled = false;
                refreshSendEnabled();
                chatInputEl.focus();
            }
        } else {
            persistAuditChatState({ touchActive: false });
            renderChatList();
        }
    }

    function scrollToBottom() {
        const scrollTarget = workspaceEl || chatMessagesEl;
        if (!scrollTarget) return;
        scrollTarget.scrollTop = scrollTarget.scrollHeight;
    }

    function scrollToBottomAfterLayout() {
        scrollToBottom();
        requestAnimationFrame(scrollToBottom);
        setTimeout(scrollToBottom, 80);
        setTimeout(scrollToBottom, 240);
    }

    function formatResponseTimeMs(responseTimeMs) {
        if (!Number.isFinite(responseTimeMs) || responseTimeMs < 0) return null;
        if (responseTimeMs >= 10000) return `${(responseTimeMs / 1000).toFixed(1)}s`;
        if (responseTimeMs >= 1000) return `${(responseTimeMs / 1000).toFixed(2)}s`;
        return `${Math.round(responseTimeMs)} ms`;
    }

    function markChatUnreadIfNeeded(chatId) {
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return;
        if (chat.id !== activeChatId || document.hidden) {
            chat.unread = true;
            persistAuditChatState({ touchActive: false });
            renderChatList();
        }
    }

    function appendAssistantMessageToChat(chatId, text, responseTimeMs) {
        const assistantEntry = {
            role: 'assistant',
            text: text || '',
            responseTimeMs: Number.isFinite(responseTimeMs) ? responseTimeMs : null,
        };
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return assistantEntry;
        const nextMessages = normalizeStoredAuditChatMessages(
            (chat.id === activeChatId ? chatHistory : chat.messages).concat([assistantEntry])
        );
        chat.messages = nextMessages;
        if (chat.id === activeChatId) {
            chatHistory = nextMessages;
        } else {
            chat.unread = true;
        }
        chat.updatedAt = Date.now();
        return assistantEntry;
    }

    function appendTurn(role, text, loading, responseTimeMs) {
        const turn = document.createElement('article');
        turn.classList.add('aa-turn', role === 'user' ? 'aa-turn-user' : 'aa-turn-assistant');

        const label = document.createElement('div');
        label.classList.add('aa-turn-role');
        label.textContent = role === 'user' ? 'YOU' : 'AUDIT AGENT';

        const content = document.createElement('div');
        content.classList.add('aa-turn-body');
        if (role === 'user') {
            content.classList.add('markdown-body');
        } else {
            content.classList.add('v2-report-markdown', 'aa-chat-markdown');
        }
        if (loading) {
            content.classList.add('is-pending');
            content.innerHTML = '<span class="aa-typing" aria-label="Thinking"><span></span><span></span><span></span></span>';
        } else {
            if (role === 'user') {
                content.innerHTML = renderMarkdown(text);
            } else {
                void renderAuditAssistantMarkdown(content, text);
            }
        }

        turn.appendChild(label);
        turn.appendChild(content);
        const responseTimeLabel = formatResponseTimeMs(responseTimeMs);
        if (!loading && role === 'assistant' && responseTimeLabel) {
            const timing = document.createElement('div');
            timing.classList.add('aa-response-time');
            timing.textContent = `Response time: ${responseTimeLabel}`;
            turn.appendChild(timing);
        }
        chatMessagesEl.appendChild(turn);
        scrollToBottom();
        return content;
    }

    function renderAssistantReplyIntoPendingTurn(chatId, replyContent, replyText, responseTimeMs) {
        if (activeChatId !== chatId) return;
        if (replyContent && replyContent.isConnected) {
            replyContent.classList.remove('is-pending');
            void renderAuditAssistantMarkdown(replyContent, replyText);
            const responseTimeLabel = formatResponseTimeMs(responseTimeMs);
            if (responseTimeLabel) {
                const timing = document.createElement('div');
                timing.classList.add('aa-response-time');
                timing.textContent = `Response time: ${responseTimeLabel}`;
                replyContent.parentElement && replyContent.parentElement.appendChild(timing);
            }
        } else {
            renderStoredChatHistory(chatHistory);
        }
    }

    function handleAuditJobResult(job, data, replyContent) {
        const chatId = job.chatId;
        removePendingAuditJob(job.jobId);
        if (cancelledAuditJobIds.has(job.jobId)) {
            if (activeChatId === chatId) {
                renderStoredChatHistory(chatHistory);
            }
            persistAuditChatState({ touchActive: activeChatId === chatId });
            renderChatList();
            updateComposerForActiveChat();
            syncEmptyState();
            promoteNextQueuedAuditMessage(chatId);
            return;
        }
        if (data && data.cancelled) {
            if (activeChatId === chatId) {
                renderStoredChatHistory(chatHistory);
            }
            persistAuditChatState({ touchActive: activeChatId === chatId });
            renderChatList();
            updateComposerForActiveChat();
            syncEmptyState();
            promoteNextQueuedAuditMessage(chatId);
            return;
        }
        if (data && data.success) {
            const replyText = data.reply || '';
            appendAssistantMessageToChat(chatId, replyText, data.response_time_ms);
            markChatUnreadIfNeeded(chatId);
            renderAssistantReplyIntoPendingTurn(chatId, replyContent, replyText, data.response_time_ms);
        } else {
            const errorText = `Error: ${(data && data.error) ? data.error : 'Audit agent response failed.'}`;
            appendAssistantMessageToChat(chatId, errorText, null);
            markChatUnreadIfNeeded(chatId);
            if (activeChatId === chatId) {
                if (replyContent && replyContent.isConnected) {
                    replyContent.classList.remove('is-pending');
                    replyContent.innerHTML = `<em>${errorText}</em>`;
                } else {
                    renderStoredChatHistory(chatHistory);
                }
            }
        }
        persistAuditChatState({ touchActive: activeChatId === chatId });
        renderChatList();
        updateComposerForActiveChat();
        syncEmptyState();
        if (activeChatId === chatId) scrollToBottomAfterLayout();
        promoteNextQueuedAuditMessage(chatId);
    }

    async function pollAuditChatJob(job, replyContent) {
        if (!job || !job.jobId || pollingJobIds.has(job.jobId)) return;
        pollingJobIds.add(job.jobId);
        try {
            while (true) {
                if (cancelledAuditJobIds.has(job.jobId)) return;
                const res = await fetch(`/api/audit_chat_jobs/${encodeURIComponent(job.jobId)}`, {
                    credentials: 'include',
                });
                if (cancelledAuditJobIds.has(job.jobId)) return;
                let payload = null;
                try {
                    payload = await res.json();
                } catch (_) {
                    payload = null;
                }
                if (!res.ok || !payload || !payload.success) {
                    handleAuditJobResult(job, {
                        success: false,
                        error: (payload && payload.error) || `Audit chat job lookup failed with HTTP ${res.status}.`,
                    }, replyContent);
                    return;
                }
                if (payload.status === 'succeeded' || payload.status === 'failed' || payload.status === 'cancelled') {
                    handleAuditJobResult(job, payload.result || {
                        success: false,
                        cancelled: payload.status === 'cancelled',
                        error: 'Audit chat job finished without a response payload.',
                    }, replyContent);
                    return;
                }
                await new Promise(function(resolve) { setTimeout(resolve, 1200); });
            }
        } catch (e) {
            if (document.visibilityState === 'hidden') return;
            handleAuditJobResult(job, {
                success: false,
                error: 'Connection error: could not check the local audit-agent job status. The answer may still finish if the server is running; reload this chat to retry.',
            }, replyContent);
        } finally {
            pollingJobIds.delete(job.jobId);
        }
    }

    function resumePendingAuditJobs() {
        pendingAuditJobs.forEach(function(job) {
            pollAuditChatJob(job, null);
        });
        Object.keys(queuedAuditMessages).forEach(function(chatId) {
            promoteNextQueuedAuditMessage(chatId);
        });
    }

    function getActiveAuditJob() {
        return pendingAuditJobs.find(function(job) { return job.chatId === activeChatId; }) || null;
    }

    async function cancelActiveAuditResponse() {
        const job = getActiveAuditJob();
        if (!job) return;
        cancelledAuditJobIds.add(job.jobId);
        removePendingAuditJob(job.jobId);
        appendAssistantMessageToChat(job.chatId, 'Response stopped.', null);
        if (activeChatId === job.chatId) {
            renderStoredChatHistory(chatHistory);
        }
        persistAuditChatState({ touchActive: activeChatId === job.chatId });
        renderChatList();
        updateComposerForActiveChat();
        syncEmptyState();
        promoteNextQueuedAuditMessage(job.chatId);
        try {
            await fetch(`/api/audit_chat_jobs/${encodeURIComponent(job.jobId)}/cancel`, {
                method: 'POST',
                credentials: 'include',
            });
        } catch (_) {
            // The UI already discarded the local job; a stale server response is ignored.
        }
    }

    function autoSizeInput() {
        if (!chatInputEl) return;
        chatInputEl.style.height = 'auto';
        chatInputEl.style.height = `${Math.min(chatInputEl.scrollHeight, 260)}px`;
        refreshSendEnabled();
    }

    function openGuide() {
        if (!guideModal) return;
        guideModal.classList.remove('hidden');
        guideModal.hidden = false;
        document.body.classList.add('v2-modal-open');
    }

    function closeGuide() {
        if (!guideModal) return;
        guideModal.classList.add('hidden');
        guideModal.hidden = true;
        document.body.classList.remove('v2-modal-open');
    }

    if (guideBtn && guideModal) {
        guideBtn.addEventListener('click', function() { openGuide(); });
        guideCloseBtn && guideCloseBtn.addEventListener('click', closeGuide);
        guideDismissBtn && guideDismissBtn.addEventListener('click', closeGuide);
        guideModal.addEventListener('click', function (e) {
            if (e.target === guideModal) closeGuide();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !guideModal.classList.contains('hidden')) closeGuide();
        });
    }

    function restoreLocalChatInterface() {
        const storedState = readAuditChatStateFromStorage();
        chatSessions = storedState && Array.isArray(storedState.chats)
            ? storedState.chats
            : [];
        pendingAuditJobs = normalizePendingAuditJobs(storedState && storedState.pendingJobs, chatSessions);
        queuedAuditMessages = normalizeQueuedAuditMessages(storedState && storedState.queuedMessages, chatSessions);
        rebuildPendingChatIds();
        cleanupUnlistedDraftChats(null);
        // Every page entry starts in a transient blank composer. Restored
        // project chats remain visible in History and keep running in the
        // background, but none is selected until the user explicitly opens it.
        const freshChat = createEmptyChat();
        chatSessions.unshift(freshChat);
        activeChatId = freshChat.id;
        const activeChat = ensureActiveChat();
        const restoredMessages = normalizeStoredAuditChatMessages(activeChat.messages);
        clearChatBubbles();
        if (restoredMessages.length > 0) {
            chatHistory = restoredMessages;
            renderStoredChatHistory(chatHistory);
        } else {
            chatHistory = [];
            syncEmptyState();
        }
        renderChatList();
        revealActiveChatRow();
        revealMainInterface();
        hideBootOverlay();
        autoSizeInput();
        scrollToBottomAfterLayout();
        resumePendingAuditJobs();
    }

    function setComposerEnabled(enabled) {
        if (chatInputEl) chatInputEl.disabled = !enabled;
        refreshSendEnabled();
    }

    async function initSession(options) {
        const shouldRestoreInterface = !options || options.restoreInterface !== false;
        sessionReady = false;
        initErrorMessage = '';
        if (shouldRestoreInterface) {
            restoreLocalChatInterface();
        }
        updateComposerForActiveChat();
        try {
            const res = await fetch('/api/initialize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    checkpoint_dir: config.checkpointDir,
                    code_dir: config.codeDir,
                }),
            });
            const data = await res.json();
            if (!data.success) {
                initErrorMessage = data.error || 'Audit session initialization failed.';
                console.warn('[audit-agent] initialize failed:', data.error || 'unknown error');
                hideBootOverlay();
                updateComposerForActiveChat();
                initPromise = null;
                return;
            }
            if (chatHistory.length > 0) {
                await restoreAuditConversationOnServer(chatHistory);
            }
            sessionReady = true;
            initErrorMessage = '';
            // Migrates any legacy browser-only chat snapshot into the managed
            // project store on first successful project initialization.
            persistAuditChatState({ touchActive: false });
            hideBootOverlay();
            updateComposerForActiveChat();
            autoSizeInput();
            if (!chatInputEl.disabled) chatInputEl.focus();
        } catch (e) {
            initErrorMessage = (e && e.message) ? e.message : 'Audit session initialization request failed.';
            console.warn('[audit-agent] initialize request failed:', e);
            hideBootOverlay();
            updateComposerForActiveChat();
            initPromise = null;
        }
    }

    function appendUserMessageToChat(chatId, message) {
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return;
        const nextMessages = normalizeStoredAuditChatMessages(
            normalizeStoredAuditChatMessages(chat.messages).concat([{ role: 'user', text: message, responseTimeMs: null }])
        );
        chat.messages = nextMessages;
        if (!chat.titleEdited) chat.title = deriveChatTitle(nextMessages);
        chat.updatedAt = Date.now();
        if (chat.id === activeChatId) {
            chatHistory = nextMessages;
        }
    }

    function promoteNextQueuedAuditMessage(chatId) {
        if (pendingChatIds.has(chatId)) return false;
        const next = shiftQueuedAuditMessage(chatId);
        if (!next) return false;
        const chat = chatSessions.find(function(entry) { return entry.id === chatId; });
        if (!chat) return false;
        const requestContext = normalizeStoredAuditChatMessages(chat.messages).map(function(entry) {
            return { role: entry.role, content: entry.text };
        });
        appendUserMessageToChat(chatId, next.text);
        if (activeChatId === chatId) {
            renderStoredChatHistory(chatHistory);
        }
        persistAuditChatState({ touchActive: activeChatId === chatId });
        renderChatList();
        submitAuditJob(chatId, next.text, requestContext, null);
        return true;
    }

    async function submitAuditJob(requestChatId, message, requestContext, replyContent) {
        const submittedJob = {
            jobId: createAuditJobId(),
            chatId: requestChatId,
            message,
            createdAt: Date.now(),
        };
        let jobRegistered = false;
        addPendingAuditJob(submittedJob);
        persistAuditChatState({ touchActive: activeChatId === requestChatId });
        renderChatList();
        if (activeChatId === requestChatId && !replyContent) {
            renderStoredChatHistory(chatHistory);
        }
        updateComposerForActiveChat();

        try {
            if (!sessionReady) {
                if (!initPromise) initPromise = initSession({ restoreInterface: false });
            }

            const res = await fetch('/api/audit_chat_jobs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    job_id: submittedJob.jobId,
                    message,
                    messages: requestContext,
                }),
            });
            let data = null;
            try {
                data = await res.json();
            } catch (_) {
                data = null;
            }
            if (!res.ok) {
                if (res.status === 404) {
                    const legacyRes = await fetch('/api/chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'include',
                        body: JSON.stringify({
                            message,
                            mode: config.chatMode || 'audit',
                            messages: requestContext,
                        }),
                    });
                    let legacyData = null;
                    try {
                        legacyData = await legacyRes.json();
                    } catch (_) {
                        legacyData = null;
                    }
                    if (!legacyRes.ok) {
                        const legacyStatusPrefix = `HTTP ${legacyRes.status}`;
                        const legacyDetail = (legacyData && legacyData.error)
                            ? legacyData.error
                            : `Request failed with ${legacyStatusPrefix}.`;
                        throw new Error(`${legacyStatusPrefix}: ${legacyDetail}`);
                    }
                    if (!legacyData) throw new Error('Server returned an unreadable response payload.');
                    if (!legacyData.success) throw new Error(legacyData.error || 'unknown error');
                    jobRegistered = true;
                    handleAuditJobResult(submittedJob, legacyData, replyContent);
                    return;
                }
                const statusPrefix = `HTTP ${res.status}`;
                const detail = (data && data.error) ? data.error : `Request failed with ${statusPrefix}.`;
                throw new Error(`${statusPrefix}: ${detail}`);
            }
            if (!data) throw new Error('Server returned an unreadable response payload.');
            if (!data.success) {
                throw new Error(data.error || 'unknown error');
            } else {
                if (!data.job_id) throw new Error('Audit chat job did not return a job id.');
                jobRegistered = true;
                persistAuditChatState({ touchActive: activeChatId === requestChatId });
                renderChatList();
                await pollAuditChatJob(submittedJob, replyContent);
            }
        } catch (e) {
            const rawMessage = (e && e.message) ? e.message : 'Request failed due to a network or server error.';
            const isFetchFailure = /failed to fetch/i.test(rawMessage);
            const errorText = isFetchFailure
                ? 'Connection error: could not submit the audit-agent job to the local server. If the server was reloading, resend the question.'
                : `Error: ${rawMessage}`;
            handleAuditJobResult(submittedJob, { success: false, error: errorText.replace(/^Error:\s*/, '') }, replyContent);
        } finally {
            if (!jobRegistered && pendingAuditJobs.some(function(job) { return job.jobId === submittedJob.jobId; })) {
                removePendingAuditJob(submittedJob.jobId);
                persistAuditChatState({ touchActive: activeChatId === requestChatId });
            }
            updateComposerForActiveChat();
            syncEmptyState();
            if (!chatInputEl.disabled && activeChatId === requestChatId) chatInputEl.focus();
            if (activeChatId === requestChatId) scrollToBottom();
        }
    }

    function sendMessage() {
        const message = (chatInputEl.value || '').trim();
        if (!message) return;

        const requestChatId = activeChatId;
        chatInputEl.value = '';
        autoSizeInput();
        refreshSendEnabled();

        if (pendingChatIds.has(requestChatId)) {
            queueAuditMessage(requestChatId, message);
            persistAuditChatState({ touchActive: true });
            renderQueuedMessages();
            syncEmptyState();
            if (chatInputEl) chatInputEl.focus();
            return;
        }

        const requestContext = normalizeStoredAuditChatMessages(chatHistory).map(function(entry) {
            return { role: entry.role, content: entry.text };
        });
        appendUserMessageToChat(requestChatId, message);
        syncEmptyState();
        persistAuditChatState();
        renderChatList();
        renderStoredChatHistory(chatHistory);
        submitAuditJob(requestChatId, message, requestContext, null);
    }

    newChatBtn && newChatBtn.addEventListener('click', startNewChat);
    chatInputEl && chatInputEl.addEventListener('input', function() {
        autoSizeInput();
        refreshSendEnabled();
    });
    chatInputEl && chatInputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    composerEl && composerEl.addEventListener('submit', (e) => {
        e.preventDefault();
        if (isActiveChatPending()) {
            cancelActiveAuditResponse();
            return;
        }
        sendMessage();
    });

    document.addEventListener('click', function() {
        closeChatMenus();
    });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closeChatMenus();
            if (renamingChatId || deleteConfirmChatId) {
                renamingChatId = null;
                deleteConfirmChatId = null;
                renderChatList();
            }
        }
    });
    document.addEventListener('visibilitychange', function() {
        if (document.hidden || !activeChatId) return;
        resumePendingAuditJobs();
        const activeChat = getActiveChat();
        if (activeChat && activeChat.unread) {
            activeChat.unread = false;
            persistAuditChatState({ touchActive: false });
            renderChatList();
        }
    });

    chatExportCurrentBtn && chatExportCurrentBtn.addEventListener('click', function() {
        exportChat(getActiveChat());
    });
    bootRetryBtn && bootRetryBtn.addEventListener('click', function () {
        bootRetryBtn.disabled = true;
        initPromise = initSession();
    });

    window.addEventListener('beforeunload', function () {
        persistAuditChatState({ touchActive: false });
    });

    document.addEventListener('DOMContentLoaded', function() {
        initPromise = initSession();
    });
})();
