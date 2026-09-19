// Shared, single-seam round launch for Project Home, Hypothesis Studio, and
// Round Workspace. The browser may submit only the fields rendered by the
// shared launch dialog; the server independently allowlists and validates them.
(function () {
    'use strict';

    var pending = false;
    var trigger = null;
    var triggerSurface = null;
    var launchContext = null;
    var bodyAlreadyModalOpen = false;

    var els = {
        overlay: document.getElementById('round-launch-overlay'),
        title: document.getElementById('round-launch-title'),
        queries: document.getElementById('round-launch-queries'),
        queryHelp: document.getElementById('round-launch-query-help'),
        deepResearch: document.getElementById('round-launch-deep-research'),
        webSearch: document.getElementById('round-launch-web-search'),
        reasoningModel: document.getElementById('round-launch-reasoning-model'),
        executionModel: document.getElementById('round-launch-execution-model'),
        error: document.getElementById('round-launch-error'),
        cancel: document.getElementById('round-launch-cancel'),
        confirm: document.getElementById('round-launch-confirm'),
    };

    function projectId() {
        return String(
            window.discoveryProjectContext && window.discoveryProjectContext.projectId || ''
        ).trim();
    }

    function roundNumber(btn) {
        var rawConfigured = btn && btn.getAttribute && btn.getAttribute('data-start-round-number');
        var configured = rawConfigured == null || rawConfigured === '' ? NaN : Number(rawConfigured);
        if (Number.isInteger(configured) && configured >= 0) return configured;
        var match = String(btn && btn.textContent || '').match(/Start Round\s+(\d+)/i);
        return match ? Number(match[1]) : null;
    }

    function roundLabel(number) {
        return 'Round ' + (Number.isInteger(number) && number >= 0 ? number : 0);
    }

    function ensureTriggerNote(btn) {
        var surface = btn && btn.closest ? btn.closest('[data-start-round-surface], .v2-modal') : null;
        surface = surface || (btn && btn.parentNode);
        var note = surface && surface.querySelector ? surface.querySelector('[data-start-round-note]') : null;
        if (!note && btn && btn.parentNode) {
            note = document.createElement('p');
            note.className = 'v2-start-round-note';
            note.setAttribute('data-start-round-note', '');
            note.hidden = true;
            btn.parentNode.appendChild(note);
        }
        return note;
    }

    function setTriggerBusy(btn, busy) {
        if (!btn) return;
        btn.disabled = busy;
        if (busy) btn.setAttribute('aria-busy', 'true');
        else btn.removeAttribute('aria-busy');
    }

    function showTriggerError(btn, message) {
        var note = ensureTriggerNote(btn);
        if (!note) return;
        note.textContent = message;
        note.hidden = false;
    }

    async function readJson(response, fallback) {
        var contentType = response.headers.get('content-type') || '';
        if (contentType.toLowerCase().indexOf('application/json') === -1) {
            throw new Error('DiscoveryAgent returned an unexpected response. Restart it and try again.');
        }
        var data = await response.json();
        if (!response.ok || data.ok === false) {
            throw new Error(data.message || fallback);
        }
        return data;
    }

    function populateModelSelect(select, models, selected) {
        select.replaceChildren();
        models.forEach(function (model) {
            var option = document.createElement('option');
            option.value = model.id;
            option.textContent = model.display_name;
            option.selected = model.id === selected;
            select.appendChild(option);
        });
        select.value = selected;
    }

    function populateLaunchDialog(data) {
        var defaults = data.defaults || {};
        var models = Array.isArray(data.models) ? data.models : [];
        if (!Number.isInteger(data.round) || data.round < 0 || !models.length) {
            throw new Error('DiscoveryAgent returned incomplete round settings. Reload and try again.');
        }
        launchContext = { projectId: String(data.project_id || ''), round: data.round };
        if (!launchContext.projectId || launchContext.projectId !== projectId()) {
            throw new Error('The round settings belong to a different project. Reload this page and try again.');
        }
        els.title.textContent = 'Launch ' + roundLabel(data.round);
        els.confirm.textContent = 'Start ' + roundLabel(data.round);
        els.queries.min = String(defaults.minimum_queries);
        els.queries.max = String(defaults.maximum_queries);
        els.queries.value = String(defaults.num_queries);
        els.queryHelp.textContent = 'Choose ' + defaults.minimum_queries + '–' + defaults.maximum_queries +
            ' queries for this round.';
        els.deepResearch.checked = defaults.deep_research === true;
        els.webSearch.checked = defaults.web_search === true;
        populateModelSelect(els.reasoningModel, models, defaults.reasoning_model);
        populateModelSelect(els.executionModel, models, defaults.execution_model);
        els.error.textContent = data.model_warning || '';
        els.error.hidden = !data.model_warning;
    }

    function setUnderlyingSurfaceInert(btn, inert) {
        var surface = btn && btn.closest ? btn.closest('[data-start-round-surface], .v2-modal') : null;
        if (inert) {
            triggerSurface = surface;
            if (triggerSurface && triggerSurface.setAttribute) triggerSurface.setAttribute('inert', '');
        } else if (triggerSurface) {
            triggerSurface.removeAttribute('inert');
            triggerSurface = null;
        }
    }

    function showLaunchDialog(btn) {
        trigger = btn;
        bodyAlreadyModalOpen = document.body.classList.contains('v2-modal-open');
        setUnderlyingSurfaceInert(btn, true);
        document.body.classList.add('v2-modal-open');
        els.overlay.hidden = false;
        els.queries.focus();
    }

    function closeLaunchDialog() {
        if (pending || els.overlay.hidden) return;
        els.overlay.hidden = true;
        setUnderlyingSurfaceInert(trigger, false);
        if (!bodyAlreadyModalOpen) document.body.classList.remove('v2-modal-open');
        var returnFocus = trigger;
        trigger = null;
        launchContext = null;
        if (returnFocus && returnFocus.focus) returnFocus.focus();
    }

    async function openLaunchDialog(btn) {
        if (!btn || pending || !els.overlay || !els.overlay.hidden) return;
        pending = true;
        setTriggerBusy(btn, true);
        var note = ensureTriggerNote(btn);
        if (note) {
            note.textContent = 'Loading settings for ' + roundLabel(roundNumber(btn)) + '…';
            note.hidden = false;
        }
        try {
            var response = await fetch('/api/rounds/options?project_id=' + encodeURIComponent(projectId()), {
                credentials: 'include', headers: { 'Accept': 'application/json' },
            });
            var data = await readJson(response, 'Round settings could not be loaded.');
            populateLaunchDialog(data);
            if (note) note.hidden = true;
            showLaunchDialog(btn);
        } catch (error) {
            showTriggerError(btn, error && error.message ? error.message : 'Round settings could not be loaded.');
        } finally {
            pending = false;
            setTriggerBusy(btn, false);
        }
    }

    function selectedRoundOptions() {
        var numQueries = Number(els.queries.value);
        var minimum = Number(els.queries.min);
        var maximum = Number(els.queries.max);
        if (!Number.isInteger(numQueries) || numQueries < minimum || numQueries > maximum) {
            throw new Error('Choose a whole-number query count from ' + minimum + ' to ' + maximum + '.');
        }
        if (!els.reasoningModel.value || !els.executionModel.value) {
            throw new Error('Choose both a reasoning model and an execution model.');
        }
        return {
            project_id: launchContext.projectId,
            num_queries: numQueries,
            deep_research: Boolean(els.deepResearch.checked),
            web_search: Boolean(els.webSearch.checked),
            reasoning_model: els.reasoningModel.value,
            execution_model: els.executionModel.value,
        };
    }

    function setLaunchBusy(busy) {
        els.confirm.disabled = busy;
        els.cancel.disabled = busy;
        if (busy) els.confirm.setAttribute('aria-busy', 'true');
        else els.confirm.removeAttribute('aria-busy');
    }

    async function performLaunch() {
        if (pending || !launchContext) return;
        var options;
        try {
            options = selectedRoundOptions();
        } catch (error) {
            els.error.textContent = error.message;
            els.error.hidden = false;
            return;
        }
        pending = true;
        setLaunchBusy(true);
        els.error.textContent = 'Starting ' + roundLabel(launchContext.round) + '…';
        els.error.hidden = false;
        try {
            var response = await fetch('/api/rounds/start', {
                method: 'POST', credentials: 'include',
                headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify(options),
            });
            var result = await readJson(response, roundLabel(launchContext.round) + ' could not be started.');
            if (!result.redirect_url) throw new Error('The Round Workspace address was missing from the response.');
            window.location.assign(result.redirect_url);
        } catch (error) {
            pending = false;
            setLaunchBusy(false);
            els.error.textContent = error && error.message ? error.message : roundLabel(launchContext.round) + ' could not be started.';
            els.error.hidden = false;
        }
    }

    document.addEventListener('click', function (event) {
        var btn = event.target && event.target.closest ? event.target.closest('[data-start-round]') : null;
        if (!btn) return;
        event.preventDefault();
        void openLaunchDialog(btn);
    });
    if (els.cancel) els.cancel.addEventListener('click', closeLaunchDialog);
    if (els.confirm) els.confirm.addEventListener('click', function () { void performLaunch(); });
    if (els.overlay) els.overlay.addEventListener('mousedown', function (event) {
        if (event.target === els.overlay) closeLaunchDialog();
    });
    document.addEventListener('keydown', function (event) {
        if (!els.overlay || els.overlay.hidden) return;
        if (event.key === 'Escape') {
            closeLaunchDialog();
            return;
        }
        if (event.key !== 'Tab') return;
        var controls = [
            els.queries, els.deepResearch, els.webSearch,
            els.reasoningModel, els.executionModel, els.cancel, els.confirm,
        ].filter(function (control) { return control && !control.disabled; });
        if (!controls.length) return;
        var index = controls.indexOf(document.activeElement);
        var next = event.shiftKey
            ? (index <= 0 ? controls.length - 1 : index - 1)
            : (index < 0 || index === controls.length - 1 ? 0 : index + 1);
        event.preventDefault();
        controls[next].focus();
    });

    window.DAStartRound = { start: openLaunchDialog };
})();
