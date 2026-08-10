// Shared, single-seam round-start action for the three V2 screens
// (Project Home, Hypothesis Studio, Round Workspace). Every "Start round"
// button carries the [data-start-round] attribute and routes through THIS one
// action — there is deliberately not a separate launch implementation per
// screen.
//
// The server resolves the active project and local backend runtime. No client
// filesystem path or credential is accepted by this shared action.
(function () {
    'use strict';

    var pending = false;

    function roundNumber(btn) {
        var rawConfigured = btn && btn.getAttribute && btn.getAttribute('data-start-round-number');
        var configured = rawConfigured == null || rawConfigured === '' ? NaN : Number(rawConfigured);
        if (Number.isInteger(configured) && configured >= 0) return configured;
        var match = String(btn && btn.textContent || '').match(/Start Round\s+(\d+)/i);
        return match ? Number(match[1]) : null;
    }

    function roundLabel(btn) {
        var number = roundNumber(btn);
        return number == null ? 'Round 0' : 'Round ' + number;
    }

    function startingNote(btn) {
        return roundNumber(btn) == null
            ? 'Starting Round 0 locally…'
            : 'Starting ' + roundLabel(btn) + '…';
    }

    function ensureNote(btn) {
        var surface = btn.closest ? btn.closest('[data-start-round-surface], .v2-modal') : null;
        surface = surface || btn.parentNode;
        var note = surface ? surface.querySelector('[data-start-round-note]') : null;
        if (!note && btn.parentNode) {
            note = document.createElement('p');
            note.className = 'v2-start-round-note';
            note.setAttribute('data-start-round-note', '');
            note.hidden = true;
            btn.parentNode.appendChild(note);
        }
        return note;
    }

    function setDismissDisabled(btn, disabled) {
        var surface = btn.closest ? btn.closest('[data-start-round-surface]') : null;
        if (!surface || !surface.querySelectorAll) return;
        surface.querySelectorAll('[data-start-round-dismiss]').forEach(function (control) {
            control.disabled = disabled;
        });
    }

    async function readJson(response, btn) {
        var contentType = response.headers.get('content-type') || '';
        if (contentType.toLowerCase().indexOf('application/json') === -1) {
            throw new Error('DiscoveryAgent returned an unexpected response. Restart it and try again.');
        }
        var data = await response.json();
        if (!response.ok || data.ok === false) {
            throw new Error(data.message || roundLabel(btn) + ' could not be started.');
        }
        return data;
    }

    async function performLaunch(btn) {
        var response = await fetch('/api/rounds/start', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Accept': 'application/json' }
        });
        var result = await readJson(response, btn);
        if (!result.redirect_url) throw new Error('The Round Workspace address was missing from the response.');
        window.location.assign(result.redirect_url);
    }

    async function startRound(btn) {
        if (!btn || pending) return;
        pending = true;
        var note = ensureNote(btn);
        var originalText = btn.textContent;
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
        setDismissDisabled(btn, true);
        if (note) {
            note.textContent = startingNote(btn);
            note.hidden = false;
        }
        try {
            await performLaunch(btn);
        } catch (error) {
            pending = false;
            btn.disabled = false;
            btn.removeAttribute('aria-busy');
            btn.textContent = originalText;
            setDismissDisabled(btn, false);
            if (note) {
                note.textContent = error && error.message ? error.message : roundLabel(btn) + ' could not be started.';
                note.hidden = false;
            }
        }
    }

    document.addEventListener('click', function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('[data-start-round]') : null;
        if (!btn) return;
        e.preventDefault();
        void startRound(btn);
    });

    // Exposed so a screen can trigger the same canonical action programmatically.
    window.DAStartRound = { start: startRound };
})();
