// Shared, single-seam round-start action for the three V2 screens
// (Project Home, Hypothesis Studio, Round Workspace). Every "Start round"
// button carries the [data-start-round] attribute and routes through THIS one
// action — there is deliberately not a separate launch implementation per
// screen.
//
// The actual launch request is intentionally left PENDING. Tracing the external
// DiscoveryAgent worker showed that V2 projects are local-only (their S3 prefix
// is cleared on activation) and never reach the worker, so there is no proven
// round-start contract — no S3 object, marker file, queue message, or endpoint —
// to call for a brand-new project's autonomous Round 0. We do NOT fabricate one
// here. When the real contract is established, only performLaunch() below
// changes; the wiring on all three screens stays exactly as it is.
(function () {
    'use strict';

    // Honest placeholder shown when the (not-yet-connected) launch is invoked.
    // This is not a fake success and does not navigate or mutate any state.
    var PENDING_NOTE = 'Starting the first round isn’t connected yet.';

    function ensureNote(btn) {
        var note = btn.parentNode ? btn.parentNode.querySelector('[data-start-round-note]') : null;
        if (!note && btn.parentNode) {
            note = document.createElement('p');
            note.className = 'v2-start-round-note';
            note.setAttribute('data-start-round-note', '');
            note.hidden = true;
            btn.parentNode.appendChild(note);
        }
        return note;
    }

    // The single seam. Returns true once a real launch has been issued; until the
    // external worker's round-start contract exists it returns false (pending).
    function performLaunch(/* btn */) {
        // TODO(round-start-contract): issue the real launch request here once the
        // external DiscoveryAgent worker's round-start contract is known. No fake
        // success, no marker write, no S3 upload, and no navigation until then.
        return false;
    }

    function startRound(btn) {
        if (!btn) return;
        if (performLaunch(btn)) return;
        var note = ensureNote(btn);
        if (note) {
            note.textContent = PENDING_NOTE;
            note.hidden = false;
        }
    }

    document.addEventListener('click', function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('[data-start-round]') : null;
        if (!btn) return;
        e.preventDefault();
        startRound(btn);
    });

    // Exposed so a screen can trigger the same canonical action programmatically.
    window.DAStartRound = { start: startRound };
})();
