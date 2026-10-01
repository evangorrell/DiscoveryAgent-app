// Project-scoped pending-selection shortcut. The history API owns eligibility;
// round_start.js owns launch options and execution. No new launch semantics.
(function () {
    'use strict';
    const context = window.discoveryProjectContext || {};
    const surface = document.getElementById('project-round-action');
    const button = document.getElementById('project-start-round');
    if (!context.projectId || !context.projectSlug || !surface || !button) return;

    const seenKey = 'da_pending_round_cue_v1_' + context.projectId;
    let requestVersion = 0;
    let identity = null;
    let launched = false;

    function hide() {
        surface.hidden = true;
        button.hidden = true;
        button.classList.remove('is-attention-cue');
        button.removeAttribute('data-start-round-number');
        identity = null;
    }

    function attentionOnce(nextIdentity) {
        if (!nextIdentity) return;
        // Session scope survives project navigation/reloads but not a new
        // browser session. If storage is unavailable, omit the optional cue.
        try {
            if (window.sessionStorage.getItem(seenKey) === nextIdentity) return;
            window.sessionStorage.setItem(seenKey, nextIdentity);
        } catch (_error) { return; }
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        button.classList.remove('is-attention-cue');
        requestAnimationFrame(() => {
            if (identity === nextIdentity && !button.hidden) button.classList.add('is-attention-cue');
        });
    }

    async function read(url) {
        const response = await fetch(url, { credentials: 'include', headers: { Accept: 'application/json' } });
        const data = await response.json();
        if (!response.ok || !data || data.success === false) throw new Error('Pending round unavailable');
        return data;
    }

    async function refresh() {
        if (launched) return;
        const version = ++requestVersion;
        try {
            const list = await read('/api/hypothesis_history');
            if (version !== requestVersion) return;
            const current = (list.rounds || []).find(row => row.is_current);
            if (!current || current.status !== 'selected' || !current.has_selected || !current.ready_to_start) {
                hide();
                return;
            }
            const index = current.storage_round_index == null ? current.round_index : current.storage_round_index;
            if (!Number.isInteger(index) || index < 0) { hide(); return; }
            const detail = await read('/api/hypothesis_history/' + index);
            if (version !== requestVersion) return;
            const round = detail.target_round_number;
            if (!detail.is_current || detail.read_only || detail.status !== 'selected' ||
                    !detail.has_selected || !detail.ready_to_start ||
                    !Number.isInteger(round) || round < 1) {
                hide();
                return;
            }
            const selections = (detail.events || []).filter(event => event.type === 'hypothesis_selected');
            const latest = selections[selections.length - 1];
            const selected = detail.selected_hypothesis || {};
            const selectionId = latest && latest.id || selected.state_id;
            const nextIdentity = selectionId ? round + ':' + selectionId : null;
            if (identity !== nextIdentity) button.classList.remove('is-attention-cue');
            identity = nextIdentity;
            button.textContent = 'Start Round ' + round;
            button.setAttribute('data-start-round-number', String(round));
            button.hidden = false;
            surface.hidden = false;
            attentionOnce(nextIdentity);
        } catch (_error) {
            if (version === requestVersion) hide(); // Never leave an unverified action visible.
        }
    }

    button.addEventListener('animationend', () => button.classList.remove('is-attention-cue'));
    window.addEventListener('da-pending-selection-changed', () => { hide(); void refresh(); });
    window.addEventListener('da-round-launched', () => {
        launched = true;
        requestVersion += 1; // Ignore a history response begun before launch.
        hide();
    });
    window.addEventListener('focus', () => { void refresh(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); });
    window.setInterval(() => { if (!document.hidden) void refresh(); }, 15000);
    void refresh();
})();
