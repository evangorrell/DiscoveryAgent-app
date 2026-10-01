(function () {
    'use strict';

    const context = window.discoveryProjectContext || {};
    const projectSlug = typeof context.projectSlug === 'string' ? context.projectSlug.trim() : '';
    const storageKey = 'discoveryAgentTabContext';

    function newTabToken() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID().replace(/-/g, '');
        }
        return Math.random().toString(36).slice(2) + Date.now().toString(36);
    }

    let tabToken = '';
    try {
        tabToken = window.sessionStorage.getItem(storageKey) || '';
        if (!/^[A-Za-z0-9_-]{16,80}$/.test(tabToken)) {
            tabToken = newTabToken();
            window.sessionStorage.setItem(storageKey, tabToken);
        }
    } catch (_error) {
        tabToken = newTabToken();
    }

    window.discoveryProjectContext.tabToken = tabToken;

    // A visit spans project-level pages in this browser tab, not individual
    // documents. Global pages may still carry the server's last project ID;
    // require the explicit project slug before retaining any Audit view.
    const projectId = projectSlug && typeof context.projectId === 'string' ? context.projectId.trim() : '';
    const visitStorageKey = 'discoveryAgentProjectVisitV1';
    let visit = null;

    function enterProjectVisit() {
        visit = null;
        try {
            if (!projectId) {
                window.sessionStorage.removeItem(visitStorageKey);
                return;
            }
            let stored = null;
            try { stored = JSON.parse(window.sessionStorage.getItem(visitStorageKey)); } catch (_) {}
            visit = stored && stored.projectId === projectId && typeof stored.token === 'string'
                ? stored : { projectId, token: newTabToken(), auditView: null };
            window.sessionStorage.setItem(visitStorageKey, JSON.stringify(visit));
        } catch (_) { visit = null; }
    }

    function currentVisit() {
        try {
            const stored = JSON.parse(window.sessionStorage.getItem(visitStorageKey));
            return visit && stored && stored.projectId === projectId && stored.token === visit.token
                ? stored : null;
        } catch (_) { return null; }
    }

    window.discoveryProjectVisit = {
        readAuditView() {
            const current = currentVisit();
            return current ? current.auditView : null;
        },
        rememberAuditView(view) {
            const current = currentVisit();
            if (!current) return;
            current.auditView = {
                chatId: typeof view.chatId === 'string' ? view.chatId : null,
                draft: typeof view.draft === 'string' ? view.draft : '',
            };
            try { window.sessionStorage.setItem(visitStorageKey, JSON.stringify(current)); } catch (_) {}
        },
    };
    enterProjectVisit();
    window.addEventListener('pageshow', function(event) {
        if (event.persisted) enterProjectVisit();
    });

    const nativeFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
        let target;
        try {
            target = new URL(input instanceof Request ? input.url : String(input), window.location.href);
        } catch (_error) {
            return nativeFetch(input, init);
        }
        if (target.origin !== window.location.origin || target.pathname === '/api/browser-presence') {
            return nativeFetch(input, init);
        }
        const options = Object.assign({}, init || {});
        const headers = new Headers(
            options.headers || (input instanceof Request ? input.headers : undefined)
        );
        headers.set('X-DiscoveryAgent-Tab', tabToken);
        if (projectSlug) headers.set('X-DiscoveryAgent-Project', projectSlug);
        options.headers = headers;
        return nativeFetch(input, options);
    };
})();
