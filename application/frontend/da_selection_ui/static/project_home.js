// Project Home (V2) — renders the canonical product state returned by
// GET /api/project_state. The SERVER decides the durable Research Status + copy;
// this file only renders it. There is no client-side state classification,
// filesystem inference, or multi-endpoint reconstruction here.
(function () {
    'use strict';

    const ReportFigures = window.DiscoveryReportFigures;
    if (!ReportFigures) throw new Error('Shared report figure renderer is unavailable');

    // Auth-fetch: redirect to login on an expired session (the small shared
    // pattern the existing screens use; kept minimal rather than copied wholesale).
    if (!window.__authFetchWrapped) {
        const nativeFetch = window.fetch;
        window.fetch = async (...args) => {
            const response = await nativeFetch(...args);
            if (response.status === 401) {
                window.location.href = '/';
            }
            return response;
        };
        window.__authFetchWrapped = true;
    }

    const els = {
        loading: document.getElementById('ph-loading'),
        error: document.getElementById('ph-error'),
        errorDetail: document.getElementById('ph-error-detail'),
        retry: document.getElementById('ph-retry'),
        content: document.getElementById('ph-content'),
    };

    // ---- small, safe helpers -------------------------------------------------

    function esc(text) {
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    function el(tag, className, html) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (html != null) node.innerHTML = html;
        return node;
    }

    function label(text) {
        return el('p', 'v2-section-label', esc(text));
    }

    function ctaButton(a) {
        if (!a) return null;
        // Action CTA (Start round): a button bound to the one shared round-start
        // action via [data-start-round] (see round_start.js), not a navigation
        // link. Project Home uses the SAME action as the Studio/Workspace buttons.
        if (a.cta_action === 'start_round') {
            const btn = el('button', 'v2-btn v2-btn-primary v2-start-round-btn',
                esc(a.cta_label || 'Start round') + ' <span class="v2-arrow" aria-hidden="true">&rarr;</span>');
            btn.type = 'button';
            btn.setAttribute('data-start-round', '');
            if (Number.isInteger(a.cta_round)) {
                btn.setAttribute('data-start-round-number', String(a.cta_round));
            }
            return btn;
        }
        if (a.cta_action === 'resume_round' || a.cta_action === 'resubmit_round') {
            const btn = el('button', 'v2-btn v2-btn-primary v2-round-recovery-btn',
                esc(a.cta_label) + ' <span class="v2-arrow" aria-hidden="true">&rarr;</span>');
            btn.type = 'button';
            btn.addEventListener('click', () => { void performRoundRecovery(btn, a); });
            return btn;
        }
        if (!a.cta_destination) return null;
        const btn = el('a', 'v2-btn v2-btn-primary',
            esc(a.cta_label) + ' <span class="v2-arrow" aria-hidden="true">&rarr;</span>');
        btn.href = a.cta_destination;
        return btn;
    }

    function recoveryError(button, message) {
        const actions = button.closest('.v2-actions');
        if (!actions) return;
        let note = actions.querySelector('.v2-round-recovery-error');
        if (!note) {
            note = el('p', 'v2-round-recovery-error');
            note.setAttribute('role', 'alert');
            actions.appendChild(note);
        }
        note.textContent = message || 'The round could not be launched.';
    }

    async function performRoundRecovery(button, status) {
        if (status.cta_action === 'resubmit_round') {
            const confirmed = await window.V2Dialog.confirm({
                title: 'Resubmit this round?',
                message: 'DiscoveryAgent will resubmit this round and run it again from the beginning.',
                confirmLabel: 'Resubmit Round',
                cancelLabel: 'Cancel',
                danger: false,
            });
            if (!confirmed) return;
        }
        const endpoint = status.cta_action === 'resume_round'
            ? '/api/rounds/resume'
            : '/api/rounds/resubmit';
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Accept': 'application/json' },
            });
            const data = await response.json();
            if (!response.ok || data.ok === false) {
                throw new Error(data.message || 'The round could not be launched.');
            }
            window.location.assign(status.cta_destination);
        } catch (error) {
            button.disabled = false;
            button.removeAttribute('aria-busy');
            recoveryError(button, error && error.message);
        }
    }

    function textLink(text, href) {
        if (!href) return null;
        const a = el('a', 'v2-textlink', esc(text) + ' <span class="v2-arrow" aria-hidden="true">&rarr;</span>');
        a.href = href;
        return a;
    }

    // ---- sections ------------------------------------------------------------

    function buildIdentity(project) {
        const wrap = el('header', 'v2-identity');
        wrap.appendChild(el('h1', 'v2-title', esc(project.title || 'DiscoveryAgent Project')));
        return wrap;
    }

    // Durable Research Status: one workflow state and one primary action.
    function buildResearchStatus(status) {
        const section = el('section', 'v2-attention');
        const head = el('div', 'v2-attention-head');
        head.appendChild(label('Research Status'));
        if (status.status_pill) {
            const pillClass = 'v2-pill'
                + (status.state === 'research_stopped' ? ' v2-pill-danger' : '')
                + (status.state === 'research_in_progress' ? ' v2-pill-active' : '');
            head.appendChild(el('span', pillClass, '<span class="v2-pill-dot" aria-hidden="true"></span>' + esc(status.status_pill)));
        }
        section.appendChild(head);
        if (status.headline) {
            section.appendChild(el('h2', 'v2-attention-headline', esc(status.headline)));
        }
        if (status.body) {
            section.appendChild(el('p', 'v2-attention-body', esc(status.body)));
        }
        const cta = ctaButton(status);
        if (cta) {
            const actions = el('div', 'v2-actions');
            actions.appendChild(cta);
            section.appendChild(actions);
        }
        return section;
    }

    function buildUnderstanding(understanding) {
        if (!understanding) return null;
        const section = el('section', 'v2-section');
        section.appendChild(label('Current Research Understanding'));
        if (understanding.preview_markdown) {
            const fullMarkdown = understanding.full_markdown || understanding.preview_markdown;
            const frame = el('div', 'v2-understanding-frame is-collapsed');
            frame.id = 'v2-research-understanding';
            const content = el('div', 'v2-prose v2-report-markdown');
            if (understanding.figures && understanding.figures.length) {
                ReportFigures.renderReportMarkdown(content, fullMarkdown, understanding.figures);
            } else {
                // Preserve Project Home's established heading hierarchy while
                // sharing the safe block/list parser used by report surfaces.
                content.innerHTML = ReportFigures.renderFallbackMarkdown(
                    fullMarkdown,
                    { headingOffset: 1 },
                );
            }
            frame.appendChild(content);
            section.appendChild(frame);

            const toggle = el('button', 'v2-understanding-link',
                'Explore Full Research Understanding <span class="v2-arrow" aria-hidden="true">&darr;</span>');
            const toggleDock = el('div', 'v2-understanding-toggle-dock');
            toggle.type = 'button';
            toggle.setAttribute('aria-expanded', 'false');
            toggle.setAttribute('aria-controls', frame.id);
            toggleDock.appendChild(toggle);
            toggle.addEventListener('click', () => {
                const expanded = toggle.getAttribute('aria-expanded') === 'true';
                toggle.setAttribute('aria-expanded', String(!expanded));
                frame.classList.toggle('is-collapsed', expanded);
                toggleDock.classList.toggle('is-sticky', !expanded);
                if (expanded) section.appendChild(toggleDock);
                else section.insertBefore(toggleDock, frame);
                toggle.innerHTML = expanded
                    ? 'Explore Full Research Understanding <span class="v2-arrow" aria-hidden="true">&darr;</span>'
                    : 'Collapse Full Research Understanding <span class="v2-arrow" aria-hidden="true">&uarr;</span>';
            });
            section.appendChild(toggleDock);
        } else {
            section.appendChild(el('p', 'v2-muted', 'A readable preview isn’t available for this report yet.'));
        }
        return section;
    }

    // Chronological history (oldest -> newest). Chronology only, not lineage.
    function buildEvolution(evolution) {
        if (!evolution || !evolution.length) return null;
        const section = el('section', 'v2-section');
        section.appendChild(label('How the Research Evolved'));
        const list = el('ol', 'v2-timeline');
        evolution.forEach((round) => {
            const item = el('li', 'v2-timeline-item' + (round.is_latest ? ' is-latest' : ''));
            item.appendChild(el('span', 'v2-timeline-node', esc(round.round_index)));
            const meta = el('div', 'v2-timeline-meta');
            meta.appendChild(el('p', 'v2-timeline-round', 'ROUND ' + esc(round.round_index)));
            const title = round.display_title || round.direction || 'Research direction not recorded';
            meta.appendChild(el('p', 'v2-timeline-title', esc(title)));
            item.appendChild(meta);
            list.appendChild(item);
        });
        section.appendChild(list);
        return section;
    }

    // Latest Findings: what the most recent completed rounds actually discovered.
    // The research direction/title is NOT repeated here (it lives in the timeline
    // above); each entry shows only the concise central finding.
    function buildRecentRounds(rounds) {
        if (!rounds || !rounds.length) return null;
        const section = el('section', 'v2-section');
        section.appendChild(label('Latest Findings'));
        rounds.forEach((round) => {
            const row = el('article', 'v2-recent-row');
            const left = el('div', 'v2-recent-left');
            left.appendChild(el('p', 'v2-recent-round', 'Round ' + esc(round.round_index)));
            left.appendChild(el('p', 'v2-recent-status', 'Complete'));
            row.appendChild(left);
            const right = el('div', 'v2-recent-right');
            if (round.outcome_preview) {
                right.appendChild(el('div', 'v2-prose v2-recent-outcome', esc(round.outcome_preview)));
            }
            const link = textLink('Open Round ' + round.round_index, round.destination);
            if (link) right.appendChild(link);
            row.appendChild(right);
            section.appendChild(row);
        });
        return section;
    }

    // ---- top-level render ----------------------------------------------------

    function render(state) {
        const frag = document.createDocumentFragment();
        frag.appendChild(buildIdentity(state.project || {}));

        if (state.mode === 'research_briefing') {
            [
                buildResearchStatus(state.research_status || {}),
                buildUnderstanding(state.research_understanding),
                buildRecentRounds(state.recent_rounds),
                buildEvolution(state.evolution),
            ].forEach((node) => { if (node) frag.appendChild(node); });
        } else {
            // Pre-research uses the same Research Status component.
            frag.appendChild(buildResearchStatus(state.research_status || {}));
        }

        els.content.innerHTML = '';
        els.content.appendChild(frag);
        els.content.hidden = false;
        els.loading.hidden = true;
        els.error.hidden = true;
    }

    function showError(message) {
        els.loading.hidden = true;
        els.content.hidden = true;
        els.errorDetail.textContent = message || 'Please try again in a moment.';
        els.error.hidden = false;
    }

    async function load() {
        els.loading.hidden = false;
        els.error.hidden = true;
        els.content.hidden = true;
        try {
            const response = await fetch('/api/project_state', {
                credentials: 'include',
                headers: { 'Accept': 'application/json' },
            });
            if (!response.ok) {
                showError('The server returned an error (' + response.status + ').');
                return;
            }
            const data = await response.json();
            if (!data || data.success === false) {
                showError((data && data.error) || 'Unknown error.');
                return;
            }
            render(data);
        } catch (err) {
            showError(err && err.message ? err.message : 'Network error.');
        }
    }

    if (els.retry) els.retry.addEventListener('click', load);
    document.addEventListener('DOMContentLoaded', load);
})();
