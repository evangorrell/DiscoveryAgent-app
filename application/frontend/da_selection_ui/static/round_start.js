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

    async function performLaunch(btn, options) {
        var response = await fetch('/api/rounds/start', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(options || {})
        });
        var result = await readJson(response, btn);
        if (!result.redirect_url) throw new Error('The Round Workspace address was missing from the response.');
        window.location.assign(result.redirect_url);
    }

    // ---- round settings dialog ----------------------------------------------
    //
    // The scientist decides how much the round may spend and which research
    // agents the director may call, per round. The server owns the defaults and
    // re-validates every value; this dialog only collects them.

    // Only agents that work without authorization DiscoveryAgent doesn't ask
    // for. The Open Targets and Census-family agents need an MCP server or a
    // CENSUS_API_KEY, so they are not offered here.
    var DIRECTOR_AGENTS = [
        { key: 'deep_research', label: 'Deep research', hint: 'Literature search and synthesis. Thorough but expensive.' },
        { key: 'web_search', label: 'Web search', hint: 'Quick lookups of facts, databases, and papers.' }
    ];

    var MODEL_ROLES = [
        {
            key: 'reasoning_model',
            label: 'REASONING MODEL',
            hint: 'Generates hypotheses and directs the round. The most capable model pays off most here.'
        },
        {
            key: 'execution_model',
            label: 'EXECUTION MODEL',
            hint: 'Writes and runs the analysis code. This one does the most work, so it costs the most.'
        }
    ];

    // The model list comes from Anthropic's Models API, so a newly released
    // model appears here without a code change. `models_source` says whether
    // this list is live or the built-in fallback.
    function modelField(role, models, chosen) {
        var field = document.createElement('div');
        field.className = 'v2-field';

        var label = document.createElement('label');
        label.className = 'v2-field-label';
        label.setAttribute('for', 'v2-round-' + role.key);
        label.textContent = role.label;
        field.appendChild(label);

        var hint = document.createElement('p');
        hint.className = 'v2-field-help';
        hint.textContent = role.hint;
        field.appendChild(hint);

        var select = document.createElement('select');
        select.className = 'v2-input v2-select';
        select.id = 'v2-round-' + role.key;
        var listed = models.slice();
        // A saved choice the current list no longer offers stays selectable —
        // dropping it would silently move the project to another model.
        if (chosen && !listed.some(function (model) { return model.id === chosen; })) {
            listed.unshift({ id: chosen, display_name: chosen });
        }
        listed.forEach(function (model) {
            var option = document.createElement('option');
            option.value = model.id;
            option.textContent = model.display_name || model.id;
            if (model.id === chosen) option.selected = true;
            select.appendChild(option);
        });
        field.appendChild(select);
        return { field: field, select: select };
    }

    function optionsDialog(roundNumber, defaults, models, modelsSource) {
        var overlay = document.createElement('div');
        overlay.className = 'v2-modal-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('data-start-round-surface', '');

        var modal = document.createElement('div');
        modal.className = 'v2-modal v2-round-options';
        overlay.appendChild(modal);

        var head = document.createElement('div');
        head.className = 'v2-modal-head';
        var title = document.createElement('h2');
        title.className = 'v2-modal-title';
        title.textContent = 'Start Round ' + roundNumber;
        head.appendChild(title);
        modal.appendChild(head);

        var body = document.createElement('div');
        body.className = 'v2-modal-body';
        modal.appendChild(body);

        var queryField = document.createElement('div');
        queryField.className = 'v2-field';
        var queryLabel = document.createElement('label');
        queryLabel.className = 'v2-field-label';
        queryLabel.setAttribute('for', 'v2-round-queries');
        queryLabel.textContent = 'QUERIES THIS ROUND';
        queryField.appendChild(queryLabel);
        var queryHelp = document.createElement('p');
        queryHelp.className = 'v2-field-help';
        queryHelp.textContent = 'How many investigations the director may run before it writes up the round. '
            + 'More queries means more resources spent exploring this hypothesis: the round digs deeper '
            + 'and follows more leads, but it takes longer and costs more. Fewer queries gets you a '
            + 'faster, cheaper, shallower read.';
        queryField.appendChild(queryHelp);
        var queries = document.createElement('input');
        queries.className = 'v2-input v2-input-number';
        queries.type = 'number';
        queries.id = 'v2-round-queries';
        queries.min = String(defaults.minimum_queries);
        queries.max = String(defaults.maximum_queries);
        queries.step = '1';
        queries.value = String(defaults.num_queries);
        queryField.appendChild(queries);
        body.appendChild(queryField);

        var modelSelects = {};
        MODEL_ROLES.forEach(function (role) {
            var built = modelField(role, models || [], defaults[role.key]);
            modelSelects[role.key] = built.select;
            body.appendChild(built.field);
        });
        if (modelsSource !== 'anthropic') {
            var stale = document.createElement('p');
            stale.className = 'v2-field-help';
            stale.textContent = 'Showing DiscoveryAgent’s built-in model list — the live list from '
                + 'Anthropic could not be loaded, so a newly released model may be missing.';
            body.appendChild(stale);
        }

        var agentsField = document.createElement('div');
        agentsField.className = 'v2-field';
        var agentsLabel = document.createElement('p');
        agentsLabel.className = 'v2-field-label';
        agentsLabel.textContent = 'AGENTS THE DIRECTOR MAY USE';
        agentsField.appendChild(agentsLabel);
        var boxes = {};
        DIRECTOR_AGENTS.forEach(function (agent) {
            var row = document.createElement('label');
            row.className = 'v2-checkbox-row';
            var box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = Boolean(defaults[agent.key]);
            boxes[agent.key] = box;
            row.appendChild(box);
            var text = document.createElement('span');
            var name = document.createElement('span');
            name.className = 'v2-checkbox-label';
            name.textContent = agent.label;
            text.appendChild(name);
            var hint = document.createElement('span');
            hint.className = 'v2-checkbox-hint';
            hint.textContent = agent.hint;
            text.appendChild(hint);
            row.appendChild(text);
            agentsField.appendChild(row);
        });
        body.appendChild(agentsField);

        // The sub-agent execution architecture is always on and is not offered
        // as a choice — see SUB_AGENT_ARCHITECTURE in local_round_runner.py.

        var note = document.createElement('p');
        note.className = 'v2-start-round-note';
        note.setAttribute('data-start-round-note', '');
        note.hidden = true;
        body.appendChild(note);

        var footer = document.createElement('div');
        footer.className = 'v2-modal-footer';
        var cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'v2-btn v2-btn-secondary';
        cancel.setAttribute('data-start-round-dismiss', '');
        cancel.textContent = 'Cancel';
        footer.appendChild(cancel);
        var confirm = document.createElement('button');
        confirm.type = 'button';
        confirm.className = 'v2-btn v2-btn-primary';
        confirm.textContent = 'Start Round ' + roundNumber;
        footer.appendChild(confirm);
        modal.appendChild(footer);

        function collect() {
            var chosen = { num_queries: Number(queries.value) };
            DIRECTOR_AGENTS.forEach(function (agent) {
                chosen[agent.key] = boxes[agent.key].checked;
            });
            MODEL_ROLES.forEach(function (role) {
                chosen[role.key] = modelSelects[role.key].value;
            });
            return chosen;
        }

        return { overlay: overlay, confirm: confirm, cancel: cancel, note: note, collect: collect };
    }

    async function loadOptionDefaults() {
        var response = await fetch('/api/rounds/options', {
            credentials: 'include',
            headers: { 'Accept': 'application/json' }
        });
        var data = await response.json();
        if (!response.ok || data.ok === false) {
            throw new Error(data.message || 'The round settings could not be loaded.');
        }
        return data;
    }

    // Resolves with the chosen options, or null when the scientist cancels.
    function askForOptions() {
        return loadOptionDefaults().then(function (data) {
            return new Promise(function (resolve) {
                var previouslyFocused = document.activeElement;
                var dialog = optionsDialog(data.round, data.defaults, data.models, data.models_source);
                document.body.appendChild(dialog.overlay);
                document.body.classList.add('v2-modal-open');

                function close(result) {
                    document.removeEventListener('keydown', onKey);
                    dialog.overlay.remove();
                    document.body.classList.remove('v2-modal-open');
                    if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
                        previouslyFocused.focus();
                    }
                    resolve(result);
                }
                function onKey(event) {
                    if (event.key === 'Escape') close(null);
                }
                document.addEventListener('keydown', onKey);
                dialog.cancel.addEventListener('click', function () { close(null); });
                dialog.overlay.addEventListener('mousedown', function (event) {
                    if (event.target === dialog.overlay) close(null);
                });
                dialog.confirm.addEventListener('click', function () { close(dialog.collect()); });
                setTimeout(function () { dialog.confirm.focus(); }, 30);
            });
        });
    }

    async function startRound(btn) {
        if (!btn || pending) return;
        pending = true;
        var note = ensureNote(btn);
        var originalText = btn.textContent;
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
        setDismissDisabled(btn, true);
        try {
            var options = await askForOptions();
            if (options === null) {
                pending = false;
                btn.disabled = false;
                btn.removeAttribute('aria-busy');
                setDismissDisabled(btn, false);
                return;
            }
            if (note) {
                note.textContent = startingNote(btn);
                note.hidden = false;
            }
            await performLaunch(btn, options);
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
