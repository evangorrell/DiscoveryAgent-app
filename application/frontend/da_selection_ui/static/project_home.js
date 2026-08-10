// Project Home (V2) — renders the canonical product state returned by
// GET /api/project_state. The SERVER decides mode + attention state + all copy;
// this file only renders it. There is no client-side state classification,
// filesystem inference, or multi-endpoint reconstruction here.
(function () {
    'use strict';

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

    // Minimal, deterministic Markdown-ish renderer for report content. Handles
    // paragraphs, headings, bold, italics and inline code; everything is
    // HTML-escaped first so report text can never inject markup.
    function renderPreviewMarkdown(md) {
        if (!md) return '';
        return String(md)
            .split(/\n\s*\n/)
            .map((para) => para.trim())
            .filter(Boolean)
            .map((para) => {
                const heading = para.match(/^(#{1,6})\s+(.+)$/s);
                if (heading) {
                    const level = Math.min(heading[1].length + 1, 6);
                    return '<h' + level + '>' + esc(heading[2]) + '</h' + level + '>';
                }
                let html = esc(para).replace(/\n/g, '<br>');
                html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
                html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
                html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
                return '<p>' + html + '</p>';
            })
            .join('');
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
                esc(a.cta_label || 'Start round'));
            btn.type = 'button';
            btn.setAttribute('data-start-round', '');
            return btn;
        }
        if (!a.cta_destination) return null;
        const btn = el('a', 'v2-btn v2-btn-primary',
            esc(a.cta_label) + ' <span class="v2-arrow" aria-hidden="true">&rarr;</span>');
        btn.href = a.cta_destination;
        return btn;
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

    // Research Briefing: Needs Your Attention (compact, single CTA).
    function buildAttention(attention) {
        const section = el('section', 'v2-attention');
        const head = el('div', 'v2-attention-head');
        head.appendChild(label('Needs Your Attention'));
        if (attention.status_pill) {
            const pillClass = 'v2-pill' + (attention.state === 'research_stopped' ? ' v2-pill-danger' : '');
            head.appendChild(el('span', pillClass, '<span class="v2-pill-dot" aria-hidden="true"></span>' + esc(attention.status_pill)));
        }
        section.appendChild(head);
        if (attention.headline) {
            section.appendChild(el('h2', 'v2-attention-headline', esc(attention.headline)));
        }
        // Deterministic framing: fixed lead-in (UI copy) above the exact
        // artifact-derived content (hypothesis / finding), when present.
        if (attention.body_lead) {
            section.appendChild(el('p', 'v2-attention-lead', esc(attention.body_lead)));
        }
        if (attention.body) {
            section.appendChild(el('p', 'v2-attention-body', esc(attention.body)));
        }
        const cta = ctaButton(attention);
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
            frame.appendChild(el('div', 'v2-prose', renderPreviewMarkdown(fullMarkdown)));
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
            // No permanent Current Research Direction section: the selected /
            // current hypothesis now appears only inside Needs Your Attention when
            // it is the scientist's current actionable state.
            [
                buildAttention(state.attention || {}),
                buildUnderstanding(state.research_understanding),
                buildEvolution(state.evolution),
                buildRecentRounds(state.recent_rounds),
            ].forEach((node) => { if (node) frag.appendChild(node); });
        } else {
            // Pre-research uses the same Needs Your Attention component as
            // completed/review states so the page keeps one visual language.
            frag.appendChild(buildAttention(state.attention || {}));
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
