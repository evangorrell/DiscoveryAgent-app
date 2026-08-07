// Shared Hypothesis Studio unseen-activity state for project-scoped pages.
(function () {
    'use strict';

    const CFG = window.hypothesisActivityConfig || {};
    const projectSlug = CFG.projectSlug || '';
    if (!projectSlug) return;

    const STORAGE_PREFIX = 'da_hs_seen_v1_' + projectSlug + '_';
    let lastRows = [];

    function seenKey(roundIndex) { return STORAGE_PREFIX + String(roundIndex); }
    function getSeen(roundIndex) {
        try { return window.localStorage.getItem(seenKey(roundIndex)); }
        catch (err) { return null; }
    }
    function setSeen(roundIndex, signature) {
        if (roundIndex == null || !signature) return;
        try { window.localStorage.setItem(seenKey(roundIndex), signature); }
        catch (err) { /* ignore private-mode/quota failures */ }
    }
    function hasUnseen(rows) {
        return (rows || []).some((row) => {
            const sig = row && row.activity_signature;
            const idx = row && (row.storage_round_index != null ? row.storage_round_index : row.round_index);
            return sig && idx != null && getSeen(idx) !== sig;
        });
    }
    function clearNavUnread() {
        const navLink = document.querySelector('[data-hs-nav-link]');
        if (!navLink) return;
        navLink.classList.remove('has-unseen');
        const dot = navLink.querySelector('.v2-nav-unread-dot');
        if (dot) dot.remove();
    }
    async function refresh() {
        try {
            const resp = await fetch('/api/hypothesis_history', { credentials: 'include', headers: { 'Accept': 'application/json' } });
            const data = await resp.json().catch(() => null);
            if (!resp.ok || !data || data.success === false) return;
            lastRows = Array.isArray(data.rounds) ? data.rounds : [];
            clearNavUnread();
        } catch (err) { /* non-critical */ }
    }

    window.HypothesisActivity = {
        markRoundSeen(roundIndex, signature) {
            setSeen(roundIndex, signature);
            clearNavUnread();
        },
        refresh,
        seenKey,
    };

    window.addEventListener('hs-activity-seen-changed', refresh);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    refresh();
    window.setInterval(refresh, 15000);
})();
