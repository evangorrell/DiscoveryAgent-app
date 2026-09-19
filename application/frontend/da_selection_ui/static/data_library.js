(function () {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const els = {
        add: $('data-library-add'), retry: $('data-library-retry'), box: $('data-library-box'),
        pageRoot: $('data-library-root'), pageContent: $('data-library-content'),
        primaryAction: $('data-library-primary-action'),
        loading: $('data-library-loading'), error: $('data-library-error'), errorDetail: $('data-library-error-detail'),
        empty: $('data-library-empty'), list: $('data-library-list'),
        feedback: $('data-library-feedback'), returnProjectDraft: $('data-library-return-project-draft'),
        removeOverlay: $('data-library-remove-overlay'), removeCopy: $('data-library-remove-copy'),
        removeProjects: $('data-library-remove-projects'), removeError: $('data-library-remove-error'),
        removeCancel: $('data-library-remove-cancel'), removeConfirm: $('data-library-remove-confirm'),
    };
    let sources = [];
    let pendingRemove = null;
    let lastFocused = null;
    const AUTO_PICKER_QUERY_PARAMETER = 'open_data_picker';
    const PROJECT_DRAFT_QUERY_PARAMETER = 'project_draft';
    let autoPickerLaunchStarted = false;
    let pageActionLayout = null;

    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text != null) element.textContent = text;
        return element;
    }
    async function json(response) { try { return await response.json(); } catch (_error) { return null; } }
    function showError(message) {
        els.loading.hidden = true; els.empty.hidden = true; els.box.hidden = false; els.list.hidden = true;
        els.primaryAction.hidden = true;
        els.errorDetail.textContent = message || 'Please try again.'; els.error.hidden = false;
    }

    function showFeedback(message, isError) {
        els.feedback.hidden = !message;
        els.feedback.textContent = message || '';
        els.feedback.classList.toggle('is-error', Boolean(isError));
        els.feedback.setAttribute('role', isError ? 'alert' : 'status');
        els.feedback.setAttribute('aria-live', isError ? 'assertive' : 'polite');
    }

    function batchFeedback(data) {
        const added = Array.isArray(data.added) ? data.added : [];
        const skipped = Array.isArray(data.skipped) ? data.skipped : [];
        const rejected = Array.isArray(data.rejected) ? data.rejected : [];
        const parts = [];
        if (added.length) parts.push('Added ' + added.length + ' data source' + (added.length === 1 ? '.' : 's.'));
        if (skipped.length) parts.push(skipped.length + ' source' + (skipped.length === 1 ? ' was' : 's were') + ' already registered.');
        if (rejected.length) {
            const detail = rejected.slice(0, 2).map((item) => item.error).filter(Boolean).join(' ');
            parts.push(rejected.length + ' source' + (rejected.length === 1 ? ' could' : 's could') + ' not be added.' + (detail ? ' ' + detail : ''));
        }
        return { message: parts.join(' ') || 'No data sources were added.', isError: rejected.length > 0 && added.length === 0 };
    }

    async function fetchTreeChildren(source, relative, cursor) {
        const url = '/api/data-library/sources/' + encodeURIComponent(source.id) + '/tree?path=' +
            encodeURIComponent(relative || '.') + '&cursor=' + encodeURIComponent(cursor || 0);
        const response = await fetch(url, { credentials: 'include', headers: { Accept: 'application/json' } });
        const data = await json(response);
        if (!(response.ok && data && data.success)) throw new Error((data && data.error) || 'Could not open that folder.');
        return data;
    }

    const dataTree = window.DiscoveryAgentDataTree.createRenderer({
        fetchChildren: fetchTreeChildren,
        removeLabel: (source) => 'Remove ' + source.display_name + ' from DiscoveryAgent Data',
        onRemove: (source, button) => openRemove(source, button),
    });

    function renderSource(source) {
        return dataTree.renderSource(source);
    }
    function dataLibraryPrimaryControl() {
        return sources.length > 0 && !els.primaryAction.hidden ? els.add : els.empty;
    }
    function consumeAutoPickerSignal() {
        const location = new window.URL(window.location.href);
        if (location.searchParams.get(AUTO_PICKER_QUERY_PARAMETER) !== '1') return false;
        location.searchParams.delete(AUTO_PICKER_QUERY_PARAMETER);
        window.history.replaceState(
            window.history.state,
            '',
            location.pathname + location.search + location.hash
        );
        return true;
    }
    function showProjectDraftReturnIfRequested() {
        if (!els.returnProjectDraft) return;
        const location = new window.URL(window.location.href);
        els.returnProjectDraft.hidden = location.searchParams.get(PROJECT_DRAFT_QUERY_PARAMETER) !== '1';
    }
    function afterPagePaint() {
        return new Promise((resolve) => {
            const frame = window.requestAnimationFrame || ((callback) => window.setTimeout(callback, 0));
            frame(() => frame(resolve));
        });
    }
    async function launchAutoPickerAfterRender(initialLoad) {
        if (autoPickerLaunchStarted) return;
        autoPickerLaunchStarted = true;
        await initialLoad;
        await afterPagePaint();
        await add(dataLibraryPrimaryControl());
    }
    function render() {
        els.loading.hidden = true; els.error.hidden = true;
        const empty = sources.length === 0;
        els.primaryAction.hidden = empty;
        els.empty.hidden = !empty; els.box.hidden = empty; els.list.hidden = empty;
        dataTree.renderSources(els.list, sources);
        if (pageActionLayout) pageActionLayout.refresh();
    }
    async function load() {
        els.empty.hidden = true; els.box.hidden = false; els.primaryAction.hidden = true;
        els.loading.hidden = false; els.error.hidden = true; els.list.hidden = true;
        try {
            const response = await fetch('/api/data-library', { credentials: 'include', headers: { Accept: 'application/json' } });
            const data = await json(response);
            if (!(response.ok && data && data.success)) throw new Error((data && data.error) || 'The server returned an error.');
            sources = data.sources || []; render();
        } catch (error) { showError(error.message); }
    }
    async function add(trigger) {
        const picker = window.DiscoveryAgentDataPicker;
        if (!picker) {
            showFeedback('The data chooser is unavailable. Reload DiscoveryAgent and try again.', true);
            return;
        }
        if (picker.isPending()) return;
        const restoreTarget = trigger || document.activeElement;
        showFeedback('', false);
        try {
            const data = await picker.choose({ controls: [els.add, els.empty] });
            if (data.ignored) return;
            if (data.cancelled) return;
            else {
                const added = Array.isArray(data.added) ? data.added : [];
                const skipped = Array.isArray(data.skipped) ? data.skipped : [];
                if (added.length || skipped.length) await load();
                const feedback = batchFeedback(data);
                showFeedback(feedback.message, feedback.isError);
            }
        } catch (error) { showFeedback(error.message, true); }
        finally {
            const preferredTarget = restoreTarget === els.empty && els.empty.hidden
                ? dataLibraryPrimaryControl()
                : restoreTarget;
            const focusTarget = preferredTarget && !preferredTarget.hidden
                ? preferredTarget
                : dataLibraryPrimaryControl();
            if (focusTarget && document.contains(focusTarget) && typeof focusTarget.focus === 'function') {
                setTimeout(() => focusTarget.focus(), 0);
            }
        }
    }
    function openRemove(source, button) {
        pendingRemove = source; lastFocused = button;
        els.removeCopy.textContent = 'DiscoveryAgent will forget “' + source.display_name + '” and detach it from affected current project configurations. The original data will remain untouched.';
        els.removeProjects.replaceChildren();
        if (source.project_count) {
            els.removeProjects.appendChild(node('p', 'v2-muted', source.project_count + ' project' + (source.project_count === 1 ? ' uses' : 's use') + ' this source:'));
            const list = node('ul', 'v2-confirm-list');
            (source.projects || []).forEach((project) => list.appendChild(node('li', '', project.display_name)));
            els.removeProjects.appendChild(list);
        }
        els.removeError.hidden = true; els.removeOverlay.hidden = false; document.body.classList.add('v2-modal-open');
        setTimeout(() => els.removeCancel.focus(), 20);
    }
    function closeRemove() {
        if (els.removeConfirm.disabled) return;
        els.removeOverlay.hidden = true; document.body.classList.remove('v2-modal-open'); pendingRemove = null;
        if (lastFocused) lastFocused.focus();
    }
    async function confirmRemove() {
        if (!pendingRemove) return;
        els.removeConfirm.disabled = true; els.removeCancel.disabled = true; els.removeError.hidden = true;
        try {
            const response = await fetch('/api/data-library/sources/' + encodeURIComponent(pendingRemove.id), {
                method: 'DELETE', credentials: 'include', headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify({ detach_projects: true }),
            });
            const data = await json(response);
            if (!(response.ok && data && data.success)) throw new Error((data && data.error) || 'Could not remove the data source.');
            els.removeOverlay.hidden = true; document.body.classList.remove('v2-modal-open'); pendingRemove = null; await load();
            const focusTarget = dataLibraryPrimaryControl();
            lastFocused = null;
            if (focusTarget && !focusTarget.hidden && document.contains(focusTarget) && typeof focusTarget.focus === 'function') {
                setTimeout(() => focusTarget.focus(), 0);
            }
        } catch (error) { els.removeError.textContent = error.message; els.removeError.hidden = false; }
        finally { els.removeConfirm.disabled = false; els.removeCancel.disabled = false; }
    }
    document.addEventListener('DOMContentLoaded', () => {
        const helper = window.DiscoveryAgentAppPageAction;
        if (helper) pageActionLayout = helper.create({
            root: els.pageRoot, content: els.pageContent, action: els.primaryAction,
        });
        const shouldAutoOpenPicker = consumeAutoPickerSignal();
        showProjectDraftReturnIfRequested();
        els.add.addEventListener('click', (event) => { event.preventDefault(); void add(els.add); });
        els.retry.addEventListener('click', load);
        els.empty.addEventListener('click', (event) => { event.preventDefault(); void add(els.empty); });
        els.removeCancel.addEventListener('click', closeRemove); els.removeConfirm.addEventListener('click', confirmRemove);
        els.removeOverlay.addEventListener('mousedown', (event) => { if (event.target === els.removeOverlay) closeRemove(); });
        document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !els.removeOverlay.hidden) closeRemove(); });
        const initialLoad = load();
        if (shouldAutoOpenPicker) void launchAutoPickerAfterRender(initialLoad);
    });
})();
