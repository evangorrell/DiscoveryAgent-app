(function () {
    'use strict';

    const context = window.discoveryProjectContext || {};
    const projectSlug = typeof context.projectSlug === 'string' ? context.projectSlug.trim() : '';
    const storageKey = 'discoveryAgentTabContext';
    const continuityKey = 'discoveryAgentContinuousProject';
    const auditFreshEntryKey = projectSlug ? `discoveryAgentAuditFreshEntry:${projectSlug}` : '';

    try {
        const previousProject = window.sessionStorage.getItem(continuityKey) || '';
        if (!projectSlug) {
            window.sessionStorage.removeItem(continuityKey);
        } else {
            if (previousProject !== projectSlug) {
                window.sessionStorage.setItem(auditFreshEntryKey, 'true');
            }
            window.sessionStorage.setItem(continuityKey, projectSlug);
            context.auditFreshEntry = window.sessionStorage.getItem(auditFreshEntryKey) === 'true';
            context.auditFreshEntryKey = auditFreshEntryKey;
        }
    } catch (_error) {
        context.auditFreshEntry = true;
        context.auditFreshEntryKey = '';
    }

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
