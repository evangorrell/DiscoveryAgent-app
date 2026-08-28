// Projects (V2). Lists real persisted/legacy projects, opens the New Project
// modal (create OR edit mode), and routes Continue to the SERVER-decided
// destination. No project state is inferred here — the server returns status,
// metadata, editability, and destinations.
(function () {
    'use strict';

    if (!window.__authFetchWrapped) {
        const nativeFetch = window.fetch;
        window.fetch = async (...args) => {
            const response = await nativeFetch(...args);
            if (response.status === 401) window.location.href = '/';
            return response;
        };
        window.__authFetchWrapped = true;
    }

    const $ = (id) => document.getElementById(id);
    const els = {
        loading: $('pj-loading'), error: $('pj-error'), errorDetail: $('pj-error-detail'),
        retry: $('pj-retry'), empty: $('pj-empty'), list: $('pj-list'),
        pageRoot: $('projects-root'), pageContent: $('projects-content'),
        primaryAction: $('projects-primary-action'), openBtn: $('open-new-project'),
        importBtn: $('open-import-project'), importEmptyBtn: $('open-import-project-empty'),
        importError: $('pj-import-error'), importEmptyError: $('pj-import-error-empty'),
        overlay: $('np-overlay'), modal: $('np-modal'), modalBody: $('np-modal-body'), close: $('np-close'),
        cancel: $('np-cancel'), form: $('np-form'), name: $('np-name'), objective: $('np-objective'),
        importSummary: $('np-import-summary'), importRoot: $('np-import-root'),
        importWarnings: $('np-import-warnings'),
        reasoningModel: $('np-reasoning-model'), executionModel: $('np-execution-model'),
        queryCount: $('np-query-count'), deepResearch: $('np-deep-research'), webSearch: $('np-web-search'),
        context: $('np-context'),
        advancedSettingsToggle: $('np-advanced-settings-toggle'),
        advancedSettingsPanel: $('np-advanced-settings-panel'),
        projectLocationPath: $('np-project-location-path'),
        projectLocationStatus: $('np-project-location-status'),
        projectLocationActions: $('np-project-location-actions'),
        locateProject: $('np-locate-project'), chooseLocation: $('np-choose-location'),
        useDefaultLocation: $('np-use-default-location'),
        formError: $('np-form-error'), submit: $('np-submit'),
        title: $('np-title'), projectState: $('np-project-state'), projectFooter: $('np-project-footer'),
        projectDataCard: $('np-project-data-card'), selectData: $('np-select-data'),
        selectedDataEmptySelect: $('np-selected-data-empty-select'),
        selectedDataTree: $('np-selected-data-tree'),
        projectDataStatus: $('np-project-data-status'),
        dataLibraryOverlay: $('np-data-library-overlay'),
        dataLibraryCancel: $('np-data-library-cancel'), dataLibrarySave: $('np-data-library-save'),
        dataLibraryBrowser: $('np-data-library-browser'),
        dataLibraryLoading: $('np-data-library-loading'),
        dataLibraryEmpty: $('np-data-library-empty'), dataLibrarySources: $('np-data-library-sources'),
        dataLibraryError: $('np-data-library-error'),
        deleteOverlay: $('delete-project-overlay'), deleteTitle: $('delete-project-title'),
        deleteDescription: $('delete-project-description'),
        deleteName: $('delete-project-name'), deleteNormalCopy: $('delete-project-normal-copy'),
        deleteImportedCopy: $('delete-project-imported-copy'),
        deleteUnavailableCopy: $('delete-project-unavailable-copy'),
        deleteError: $('delete-project-error'), deleteCancel: $('delete-project-cancel'),
        deleteConfirm: $('delete-project-confirm'),
        relocationOverlay: $('project-relocation-overlay'),
        relocationDialog: $('project-relocation-dialog'),
        relocationDestination: $('project-relocation-destination'),
        relocationCancel: $('project-relocation-cancel'),
        relocationConfirm: $('project-relocation-confirm'),
        unsavedOverlay: $('project-unsaved-overlay'),
        unsavedDialog: $('project-unsaved-dialog'),
        unsavedDiscard: $('project-unsaved-discard'),
        unsavedSave: $('project-unsaved-save'),
        recoveryOverlay: $('project-recovery-overlay'),
        recoveryDialog: $('project-recovery-dialog'),
        recoveryPath: $('project-recovery-path'),
        recoveryError: $('project-recovery-error'),
        recoveryCancel: $('project-recovery-cancel'),
        recoveryLocate: $('project-recovery-locate'),
    };

    let pendingDeleteProject = null;
    let pendingDeleteUnavailable = false;
    let pendingDeleteImported = false;
    let deleteLastFocused = null;
    let pageActionLayout = null;
    const initialProjectsEmpty = Boolean(
        els.pageRoot && els.pageRoot.dataset.projectsEmpty === 'true'
    );

    function esc(t) { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }
    function el(tag, cls, html) { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }

    function relativeTime(iso) {
        if (!iso) return null;
        const then = new Date(iso).getTime();
        if (isNaN(then)) return null;
        const s = Math.max(0, (Date.now() - then) / 1000);
        if (s < 90) return 'moments ago';
        const m = Math.round(s / 60); if (m < 60) return m + (m === 1 ? ' minute ago' : ' minutes ago');
        const h = Math.round(m / 60); if (h < 24) return h + (h === 1 ? ' hour ago' : ' hours ago');
        const d = Math.round(h / 24); if (d < 30) return d + (d === 1 ? ' day ago' : ' days ago');
        const mo = Math.round(d / 30); if (mo < 12) return mo + (mo === 1 ? ' month ago' : ' months ago');
        const y = Math.round(mo / 12); return y + (y === 1 ? ' year ago' : ' years ago');
    }

    // Metadata line: activity time only (no round-count).
    function metadataLine(p) {
        if (p.show_updated) {
            const rel = relativeTime(p.updated_at);
            return rel ? 'Updated ' + rel : 'Updated recently';
        }
        return 'Created recently';
    }

    // ---- action menu ---------------------------------------------------------

    function closeAllMenus() {
        document.querySelectorAll('.v2-menu.is-open').forEach((menu) => {
            menu.classList.remove('is-open');
            const btn = menu.previousElementSibling;
            if (btn && btn.classList.contains('v2-row-menu')) btn.setAttribute('aria-expanded', 'false');
        });
    }

    function menuItem(labelText, onActivate) {
        const item = el('button', 'v2-menu-item', esc(labelText));
        item.type = 'button';
        item.setAttribute('role', 'menuitem');
        item.addEventListener('click', (e) => { e.stopPropagation(); closeAllMenus(); if (onActivate) onActivate(); });
        return item;
    }

    function dangerMenuItem(labelText, onActivate) {
        const item = menuItem(labelText, onActivate);
        item.classList.add('v2-menu-item-danger');
        return item;
    }

    // Every project row menu contains Edit and its ownership-appropriate removal action.
    const DOTS_SVG = '<svg class="v2-dots" viewBox="0 0 4 18" width="4" height="18" aria-hidden="true">'
        + '<circle cx="2" cy="2" r="1.8"/><circle cx="2" cy="9" r="1.8"/><circle cx="2" cy="16" r="1.8"/></svg>';

    function buildMenu(project) {
        const wrap = el('div', 'v2-menu-wrap');
        const btn = el('button', 'v2-row-menu', DOTS_SVG);
        btn.type = 'button';
        btn.setAttribute('aria-label', 'Project actions');
        btn.setAttribute('aria-haspopup', 'menu');
        btn.setAttribute('aria-expanded', 'false');
        const menu = el('div', 'v2-menu', '');
        menu.setAttribute('role', 'menu');

        menu.appendChild(menuItem('Edit', () => openEdit(project)));
        menu.appendChild(dangerMenuItem(
            project.imported_in_place ? 'Remove Project' : 'Delete',
            () => openDeleteConfirmation(project),
        ));

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const willOpen = !menu.classList.contains('is-open');
            closeAllMenus();
            if (willOpen) {
                menu.classList.add('is-open');
                btn.setAttribute('aria-expanded', 'true');
                const first = menu.querySelector('.v2-menu-item');
                if (first) first.focus();
            }
        });
        wrap.appendChild(btn);
        wrap.appendChild(menu);
        return wrap;
    }

    // ---- project rows --------------------------------------------------------

    function renderRow(p) {
        const row = el('article', 'v2-project-row');

        const head = el('div', 'v2-project-rowhead');
        // The title remains a canonical deep link. Continue revalidates through
        // the server so a moved project can offer recovery without navigating.
        const homeUrl = p.home_url || ('/projects/' + encodeURIComponent(p.url_slug || p.id));
        const title = el('a', 'v2-project-title', esc(p.display_name || 'Untitled project'));
        title.href = homeUrl;
        head.appendChild(title);
        head.appendChild(buildMenu(p));
        row.appendChild(head);

        if (p.overview) row.appendChild(el('p', 'v2-project-overview', esc(p.overview)));

        const footer = el('div', 'v2-project-footer');
        const meta = el('div', 'v2-project-meta');
        meta.appendChild(el('p', 'v2-project-status v2-tone-' + esc(p.status_tone || 'neutral'),
            '<span class="v2-status-dot" aria-hidden="true"></span>' + esc(p.status_label || 'Not Started')));
        meta.appendChild(el('p', 'v2-project-activity', esc(metadataLine(p))));
        footer.appendChild(meta);

        const cont = el('a', 'v2-project-continue', '<span class="v2-project-continue-label">Continue</span> <span class="v2-arrow" aria-hidden="true">&rarr;</span>');
        cont.href = homeUrl;
        cont.addEventListener('click', (event) => {
            event.preventDefault();
            continueProject(p, cont);
        });
        footer.appendChild(cont);

        row.appendChild(footer);
        return row;
    }

    function renderList(projects) {
        const empty = projects.length === 0;
        // The server emits distinct first-run and populated DOM hierarchies.
        // If another tab changes the registry between HTML and API responses,
        // reload once so headings, action placement, and keyboard order remain
        // truthful rather than synthesizing a parallel client-only layout.
        if (empty !== initialProjectsEmpty) {
            window.location.reload();
            return;
        }
        els.list.innerHTML = '';
        projects.forEach((p) => els.list.appendChild(renderRow(p)));
        els.loading.hidden = true; els.error.hidden = true;
        if (els.primaryAction) els.primaryAction.hidden = empty;
        if (empty) {
            if (els.empty) els.empty.hidden = false;
            els.list.hidden = true;
        } else {
            if (els.empty) els.empty.hidden = true;
            els.list.hidden = false;
        }
        if (pageActionLayout) pageActionLayout.refresh();
        if (pendingEditProjectId) {
            const project = projects.find((p) => p.id === pendingEditProjectId);
            pendingEditProjectId = null;
            if (project) {
                openEdit(project);
                window.history.replaceState({}, '', '/projects');
            }
        }
        if (pendingRecoveryProjectId) {
            const project = projects.find((p) => p.id === pendingRecoveryProjectId);
            pendingRecoveryProjectId = null;
            if (project) {
                openProjectRecovery(project, { action: 'continue' });
                window.history.replaceState({}, '', '/projects');
            }
        }
    }

    function showError(message) {
        els.loading.hidden = true; els.list.hidden = true;
        if (els.empty) els.empty.hidden = true;
        if (els.primaryAction) els.primaryAction.hidden = true;
        els.errorDetail.textContent = message || 'Please try again in a moment.';
        els.error.hidden = false;
    }

    async function loadProjects() {
        els.loading.hidden = initialProjectsEmpty;
        els.error.hidden = true;
        els.list.hidden = true;
        if (els.empty) els.empty.hidden = !initialProjectsEmpty;
        if (els.primaryAction) els.primaryAction.hidden = true;
        try {
            const resp = await fetch('/api/projects', { credentials: 'include', headers: { 'Accept': 'application/json' } });
            if (!resp.ok) { showError('The server returned an error (' + resp.status + ').'); return; }
            const data = await resp.json();
            if (!data || data.success === false) { showError((data && data.error) || 'Unknown error.'); return; }
            renderList(data.projects || []);
        } catch (err) {
            showError(err && err.message ? err.message : 'Network error.');
        }
    }

    async function continueProject(project, invoker) {
        if (!project || !project.id) return;
        try {
            const resp = await fetch('/api/projects/' + encodeURIComponent(project.id) + '/continue', {
                method: 'POST', credentials: 'include', headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (resp.ok && data && data.success) {
                window.location.href = data.destination;
                return;
            }
            if (data && data.code === 'project_location_unavailable') {
                openProjectRecovery(Object.assign({}, project, {
                    registered_path: data.registered_path || project.registered_path,
                }), { action: 'continue', invoker: invoker });
                return;
            }
            throw new Error((data && (data.error || data.message)) || 'Could not open this project.');
        } catch (error) {
            showError(error && error.message ? error.message : 'Could not open this project.');
        }
    }

    // ---- modal (create + edit) ----------------------------------------------

    let lastFocused = null;
    let selectorLastFocused = null;
    let modalMode = 'create';
    let editingProjectId = null;
    let editingProjectImportedInPlace = false;
    let pendingEditProjectId = null;
    let pendingRecoveryProjectId = null;
    let selectedDataSources = [];
    let draftDataSources = [];
    let librarySources = [];
    let pendingProjectParent = '';
    let pendingCanonicalDefaultLocation = false;
    let currentProjectRoot = '';
    let currentProjectParent = '';
    let projectLocationAvailable = true;
    let projectLocationRunning = false;
    let projectHasActiveRound = false;
    let pendingRelocationBody = null;
    let relocationProcessing = false;
    let recoveryProcessing = false;
    let pendingRecovery = null;
    let recoveryLastFocused = null;
    let persistedEditSnapshot = null;
    let unsavedCloseInvoker = null;
    let unsavedProcessing = false;
    let importProjectRoot = '';
    let importPreviewToken = '';
    let importInvoker = null;
    let projectImportRunning = false;
    let projectSubmissionRunning = false;

    // ---- app-wide input data selector ---------------------------------------

    function copySelection(value) { return JSON.parse(JSON.stringify(value || [])); }

    function normalizeEditablePaths(values) {
        return Array.from(new Set((values || []).map((value) => String(value || '').trim()).filter(Boolean)))
            .sort((left, right) => left.split('/').length - right.split('/').length || left.localeCompare(right));
    }

    function normalizeEditableDataSelection(value) {
        return (value || []).map((item) => {
            const selected = normalizeEditablePaths(item && item.selected_paths);
            const compactSelected = selected.filter((path, index) =>
                !selected.slice(0, index).some((parent) => isSameOrChild(path, parent)));
            const excluded = normalizeEditablePaths(item && item.excluded_paths)
                .filter((path) => compactSelected.some((parent) => isSameOrChild(path, parent)));
            const compactExcluded = excluded.filter((path, index) =>
                !excluded.slice(0, index).some((parent) => isSameOrChild(path, parent)));
            return {
                dataset_id: String((item && item.dataset_id) || ''),
                selected_paths: compactSelected,
                excluded_paths: compactExcluded,
            };
        }).filter((item) => item.dataset_id && item.selected_paths.length)
            .sort((left, right) => left.dataset_id.localeCompare(right.dataset_id));
    }

    function normalizeEditableLocation(value) {
        const path = String(value || '').trim();
        return path === '/' ? path : path.replace(/\/+$/, '');
    }

    function normalizeEditableProjectState(state) {
        const rawQueryCount = String(state.query_count == null ? '' : state.query_count).trim();
        const queryCount = /^\d+$/.test(rawQueryCount) ? String(Number(rawQueryCount)) : rawQueryCount;
        return {
            project_name: String(state.project_name || '').trim(),
            research_objective: String(state.research_objective || '').trim(),
            project_context: String(state.project_context || '').trim(),
            data_sources: normalizeEditableDataSelection(state.data_sources),
            query_count: queryCount,
            deep_research: Boolean(state.deep_research),
            web_search: Boolean(state.web_search),
            reasoning_model: String(state.reasoning_model || ''),
            execution_model: String(state.execution_model || ''),
            project_location: normalizeEditableLocation(state.project_location),
        };
    }

    function currentEditableProjectState() {
        return normalizeEditableProjectState({
            project_name: els.name.value,
            research_objective: els.objective.value,
            project_context: els.context.value,
            data_sources: selectedDataSources,
            query_count: els.queryCount.value,
            deep_research: els.deepResearch.checked,
            web_search: els.webSearch.checked,
            reasoning_model: els.reasoningModel.value,
            execution_model: els.executionModel.value,
            project_location: projectDestination(
                pendingProjectParent, els.name.value, pendingCanonicalDefaultLocation
            ),
        });
    }

    function capturePersistedEditSnapshot() {
        persistedEditSnapshot = currentEditableProjectState();
    }

    function editHasUnsavedChanges() {
        return modalMode === 'edit' && persistedEditSnapshot !== null &&
            JSON.stringify(currentEditableProjectState()) !== JSON.stringify(persistedEditSnapshot);
    }

    function selectionItem(datasetId, create) {
        let item = draftDataSources.find((entry) => entry.dataset_id === datasetId);
        if (!item && create) {
            item = { dataset_id: datasetId, selected_paths: [], excluded_paths: [] };
            draftDataSources.push(item);
        }
        return item;
    }
    function isSameOrChild(path, parent) {
        return parent === '.' || path === parent || path.indexOf(parent + '/') === 0;
    }
    function pathState(datasetId, path) {
        const item = selectionItem(datasetId, false);
        if (!item) return { checked: false, indeterminate: false };
        const selectedAncestor = (item.selected_paths || []).some((selected) => isSameOrChild(path, selected));
        const excludedAncestor = (item.excluded_paths || []).some((excluded) => isSameOrChild(path, excluded));
        const selectedBelow = (item.selected_paths || []).some((selected) => selected !== path && isSameOrChild(selected, path));
        const excludedBelow = (item.excluded_paths || []).some((excluded) => excluded !== path && isSameOrChild(excluded, path));
        const checked = selectedAncestor && !excludedAncestor;
        return { checked: checked && !excludedBelow, indeterminate: (checked && excludedBelow) || (!checked && selectedBelow) };
    }
    function normalizeDraft() {
        draftDataSources = draftDataSources.map((item) => {
            const selected = Array.from(new Set(item.selected_paths || [])).sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b));
            const compact = selected.filter((path, index) => !selected.slice(0, index).some((parent) => isSameOrChild(path, parent)));
            const excluded = Array.from(new Set(item.excluded_paths || []))
                .filter((path) => compact.some((parent) => isSameOrChild(path, parent)))
                .sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b));
            const compactExcluded = excluded.filter((path, index) => !excluded.slice(0, index).some((parent) => isSameOrChild(path, parent)));
            return { dataset_id: item.dataset_id, selected_paths: compact, excluded_paths: compactExcluded };
        }).filter((item) => item.selected_paths.length);
    }
    function toggleDraftPath(datasetId, path, shouldSelect) {
        const item = selectionItem(datasetId, true);
        const state = pathState(datasetId, path);
        if (shouldSelect) {
            // Restoring an excluded branch restores that branch as a whole; the
            // runtime cannot hard-deny descendants beneath an allowed parent.
            item.excluded_paths = (item.excluded_paths || []).filter((excluded) =>
                !(isSameOrChild(path, excluded) || isSameOrChild(excluded, path)));
            if (!state.checked && !(item.selected_paths || []).some((selected) => isSameOrChild(path, selected))) {
                item.selected_paths.push(path);
            }
        } else {
            const coveringParent = (item.selected_paths || []).some((selected) => selected !== path && isSameOrChild(path, selected));
            item.selected_paths = (item.selected_paths || []).filter((selected) => !isSameOrChild(selected, path));
            if (coveringParent && !(item.excluded_paths || []).includes(path)) item.excluded_paths.push(path);
        }
        normalizeDraft();
        refreshSelectorChecks();
    }
    function refreshSelectorChecks() {
        els.dataLibrarySources.querySelectorAll('input[data-dataset-id][data-path]').forEach((checkbox) => {
            const state = pathState(checkbox.dataset.datasetId, checkbox.dataset.path);
            checkbox.checked = state.checked;
            checkbox.indeterminate = state.indeterminate;
            checkbox.setAttribute('aria-checked', state.indeterminate ? 'mixed' : String(state.checked));
        });
    }
    function selectedPreviewPathVisible(selection, path) {
        if (!selection || !path || path === '.') return Boolean(selection);
        const excluded = selection.excluded_paths || [];
        if (excluded.some((denied) => isSameOrChild(path, denied))) return false;
        return (selection.selected_paths || []).some((selected) =>
            isSameOrChild(path, selected) || isSameOrChild(selected, path));
    }
    async function fetchSelectedPreviewPage(entry, relative, cursor) {
        const source = entry.source;
        const resp = await fetch('/api/data-library/sources/' + encodeURIComponent(source.id) + '/tree?path=' +
            encodeURIComponent(relative || '.') + '&cursor=' + encodeURIComponent(cursor || 0), {
            credentials: 'include', headers: { 'Accept': 'application/json' },
        });
        const data = await readJson(resp);
        if (!(resp.ok && data && data.success)) {
            const error = new Error((data && data.error) || 'Could not open that folder.');
            error.code = data && data.code;
            throw error;
        }
        return data;
    }
    const projectDataTree = window.DiscoveryAgentDataTree.createRenderer({
        getSource: (entry) => entry.source,
        fetchChildren: fetchSelectedPreviewPage,
        includeChild: (entry, child) => child.kind !== 'symlink' &&
            selectedPreviewPathVisible(entry.selection, child.path),
        sourceClass: 'v2-selected-data-source',
        removeClass: 'v2-project-data-source-remove',
        removeLabel: (source) => 'Remove ' + source.display_name + ' from this project',
        onRemove: (entry) => removeSelectedPreviewSource(entry.source.id, entry.source.display_name),
        handleChildError: ({ error, item }) => {
            if (error.code !== 'data_path_unavailable' && error.code !== 'dataset_not_found') return false;
            item.remove();
            return true;
        },
        handleSourceError: ({ error, item }) => {
            if (error.code !== 'dataset_not_found') return false;
            item.remove();
            return true;
        },
    });
    function announceProjectDataChange(message) {
        if (!els.projectDataStatus) return;
        // Clear first so removing the same source twice in separate edits is
        // announced again by assistive technology.
        els.projectDataStatus.textContent = '';
        window.setTimeout(() => { els.projectDataStatus.textContent = message; }, 0);
    }
    function focusAfterProjectSourceRemoval(removedIndex) {
        window.setTimeout(() => {
            const removeButtons = Array.from(els.selectedDataTree.querySelectorAll(
                '.v2-project-data-source-remove'
            ));
            const target = removeButtons[removedIndex] || removeButtons[removedIndex - 1] || projectDataSelectionControl();
            if (target && target.focus) target.focus({ preventScroll: true });
        }, 0);
    }
    function removeSelectedPreviewSource(datasetId, friendlyName) {
        const removedIndex = selectedDataSources.findIndex((item) => item.dataset_id === datasetId);
        if (removedIndex < 0) return;
        // The complete source selection is one object, so filtering by its
        // stable dataset ID also removes every selected path and exclusion.
        selectedDataSources = selectedDataSources.filter((item) => item.dataset_id !== datasetId);
        draftDataSources = draftDataSources.filter((item) => item.dataset_id !== datasetId);
        renderSelectedDataHierarchy();
        announceProjectDataChange(friendlyName + ' removed from this project. Save changes to apply.');
        focusAfterProjectSourceRemoval(removedIndex);
    }
    function renderSelectedPreviewSource(selection) {
        const source = librarySources.find((candidate) => candidate.id === selection.dataset_id);
        return source ? projectDataTree.renderSource({ source, selection }) : null;
    }
    function hasSelectedProjectData(selection) {
        return (selection || []).some((item) =>
            item && Array.isArray(item.selected_paths) && item.selected_paths.length > 0
        );
    }
    function projectDataSelectionControl() {
        return hasSelectedProjectData(selectedDataSources)
            ? els.selectData
            : els.selectedDataEmptySelect;
    }
    function renderSelectedDataHierarchy() {
        const empty = !hasSelectedProjectData(selectedDataSources);
        els.selectData.hidden = empty;
        els.projectDataCard.hidden = empty;
        els.selectedDataEmptySelect.hidden = !empty;
        els.selectedDataTree.hidden = empty;
        projectDataTree.renderSources(els.selectedDataTree, empty ? [] : selectedDataSources, renderSelectedPreviewSource);
        if (empty) return;
    }
    async function fetchSelectorTreePage(source, relative, cursor) {
        const resp = await fetch('/api/data-library/sources/' + encodeURIComponent(source.id) + '/tree?path=' +
            encodeURIComponent(relative || '.') + '&cursor=' + encodeURIComponent(cursor || 0), {
            credentials: 'include', headers: { 'Accept': 'application/json' },
        });
        const data = await readJson(resp);
        if (!(resp.ok && data && data.success)) throw new Error((data && data.error) || 'Could not open that folder.');
        return data;
    }
    const selectorDataTree = window.DiscoveryAgentDataTree.createRenderer({
        fetchChildren: fetchSelectorTreePage,
        sourceClass: 'v2-data-selector-source',
        selection: {
            getState: (source, path) => pathState(source.id, path),
            label: (_source, path, name) => (path === '.' ? 'Select all of ' : 'Select ') + name,
            isDisabled: (_source, _path, value) => value && value.kind === 'symlink',
            onChange: (source, path, checked) => toggleDraftPath(source.id, path, checked),
        },
    });
    function renderSelectorSources() {
        els.dataLibraryEmpty.hidden = librarySources.length > 0;
        selectorDataTree.renderSources(els.dataLibrarySources, librarySources);
        refreshSelectorChecks();
    }
    function captureSelectorView() {
        return {
            scrollTop: els.dataLibraryBrowser.scrollTop,
            expandedDatasetIds: Array.from(els.dataLibrarySources.querySelectorAll(
                '.v2-data-selector-source[aria-expanded="true"]'
            )).map((card) => card.dataset.datasetId).filter(Boolean),
        };
    }
    function restoreSelectorView(viewState) {
        if (!viewState) return;
        (viewState.expandedDatasetIds || []).forEach((datasetId) => {
            const card = Array.from(els.dataLibrarySources.querySelectorAll('.v2-data-selector-source'))
                .find((candidate) => candidate.dataset.datasetId === datasetId);
            const disclosure = card && card.querySelector('.v2-data-source-disclosure');
            if (disclosure && disclosure.getAttribute('aria-expanded') !== 'true') disclosure.click();
        });
        const restoreScroll = () => {
            const maximum = Math.max(0, els.dataLibraryBrowser.scrollHeight - els.dataLibraryBrowser.clientHeight);
            els.dataLibraryBrowser.scrollTop = Math.min(viewState.scrollTop || 0, maximum);
        };
        if (window.requestAnimationFrame) window.requestAnimationFrame(restoreScroll);
        else window.setTimeout(restoreScroll, 0);
    }
    async function loadLibrarySources(options) {
        const config = Object.assign({ preserveProjectSelection: false, viewState: null, throwOnError: false }, options || {});
        els.dataLibraryLoading.hidden = false; els.dataLibraryError.hidden = true;
        try {
            const resp = await fetch('/api/data-library', { credentials: 'include', headers: { 'Accept': 'application/json' } });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) throw new Error((data && data.error) || 'Could not load DiscoveryAgent Data.');
            librarySources = data.sources || [];
            const validIds = new Set(librarySources.map((source) => source.id));
            draftDataSources = draftDataSources.filter((item) => validIds.has(item.dataset_id));
            if (!config.preserveProjectSelection) {
                selectedDataSources = selectedDataSources.filter((item) => validIds.has(item.dataset_id));
            }
            renderSelectedDataHierarchy(); renderSelectorSources();
            restoreSelectorView(config.viewState);
        } catch (error) {
            els.dataLibraryError.textContent = error.message; els.dataLibraryError.hidden = false;
            if (config.throwOnError) throw error;
        } finally { els.dataLibraryLoading.hidden = true; }
    }
    async function openDataLibrarySelector() {
        selectorLastFocused = document.activeElement;
        draftDataSources = copySelection(selectedDataSources);
        await loadLibrarySources();
        els.dataLibraryOverlay.hidden = false;
        setTimeout(() => {
            const firstChoice = els.dataLibrarySources.querySelector('input:not([disabled])');
            (firstChoice || els.dataLibraryCancel).focus();
        }, 20);
    }
    function closeDataLibrarySelector(options) {
        const config = options && options.restoreToProjectData === true
            ? options
            : { restoreToProjectData: false };
        draftDataSources = copySelection(selectedDataSources);
        els.dataLibraryOverlay.hidden = true;
        const preferredTarget = config.restoreToProjectData
            ? projectDataSelectionControl()
            : selectorLastFocused;
        const preferredVisible = preferredTarget && !preferredTarget.hidden &&
            !(preferredTarget.closest && preferredTarget.closest('[hidden]'));
        const focusTarget = preferredVisible ? preferredTarget : projectDataSelectionControl();
        if (focusTarget && focusTarget.focus) focusTarget.focus();
        selectorLastFocused = null;
    }
    function saveSelectedData() {
        normalizeDraft(); selectedDataSources = copySelection(draftDataSources);
        renderSelectedDataHierarchy();
        closeDataLibrarySelector({ restoreToProjectData: true });
    }

    function applyMode() {
        els.projectState.hidden = false;
        els.projectFooter.hidden = false;
        const isEdit = modalMode === 'edit';
        const isImport = modalMode === 'import';
        els.title.textContent = isEdit
            ? 'Edit DiscoveryAgent project'
            : isImport
            ? 'Import existing project'
            : 'Create a new DiscoveryAgent project';
        els.submit.innerHTML = isEdit
            ? 'Save'
            : isImport
            ? 'Import project <span class="v2-arrow" aria-hidden="true">&rarr;</span>'
            : 'Create project <span class="v2-arrow" aria-hidden="true">&rarr;</span>';
        els.importSummary.hidden = !isImport;
        els.projectLocationActions.hidden = isImport;
    }

    // ADVANCED_SETTINGS_SCROLL_BEGIN
    let advancedSettingsScrollFrame = null;
    let advancedSettingsScrollCancel = null;
    let advancedSettingsScrollRequest = 0;
    const ADVANCED_SETTINGS_SCROLL_PADDING = 18;

    function cancelAdvancedSettingsScroll() {
        advancedSettingsScrollRequest += 1;
        if (advancedSettingsScrollFrame !== null && advancedSettingsScrollCancel) {
            advancedSettingsScrollCancel(advancedSettingsScrollFrame);
        }
        advancedSettingsScrollFrame = null;
        advancedSettingsScrollCancel = null;
    }

    function scrollAdvancedSettingsContentIntoView(requestId) {
        advancedSettingsScrollFrame = null;
        advancedSettingsScrollCancel = null;
        if (
            requestId !== advancedSettingsScrollRequest ||
            els.overlay.hidden ||
            els.advancedSettingsPanel.hidden ||
            els.advancedSettingsToggle.getAttribute('aria-expanded') !== 'true'
        ) return;

        const target = els.advancedSettingsPanel.querySelector('.v2-advanced-query-field') ||
            els.advancedSettingsPanel.firstElementChild || els.advancedSettingsPanel;
        const containerRect = els.modalBody.getBoundingClientRect();
        const targetRect = target.getBoundingClientRect();
        const visibleTop = containerRect.top + ADVANCED_SETTINGS_SCROLL_PADDING;
        const visibleBottom = containerRect.bottom - ADVANCED_SETTINGS_SCROLL_PADDING;
        const visibleHeight = Math.max(0, visibleBottom - visibleTop);

        // Expanding near the top of a tall viewport should not create a jump.
        if (targetRect.top >= visibleTop && targetRect.bottom <= visibleBottom) return;

        let delta = 0;
        if (targetRect.height > visibleHeight || targetRect.top < visibleTop) {
            delta = targetRect.top - visibleTop;
        } else if (targetRect.bottom > visibleBottom) {
            delta = targetRect.bottom - visibleBottom;
        }
        const maximum = Math.max(0, els.modalBody.scrollHeight - els.modalBody.clientHeight);
        const destination = Math.min(maximum, Math.max(0, els.modalBody.scrollTop + delta));
        if (Math.abs(destination - els.modalBody.scrollTop) < 1) return;

        const reduceMotion = typeof window.matchMedia === 'function' &&
            window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (typeof els.modalBody.scrollTo === 'function') {
            els.modalBody.scrollTo({ top: destination, behavior: reduceMotion ? 'auto' : 'smooth' });
        } else {
            els.modalBody.scrollTop = destination;
        }
    }

    function scheduleAdvancedSettingsScroll() {
        cancelAdvancedSettingsScroll();
        const requestId = advancedSettingsScrollRequest;
        const requestFrame = typeof window.requestAnimationFrame === 'function'
            ? window.requestAnimationFrame.bind(window)
            : (callback) => window.setTimeout(callback, 0);
        const cancelFrame = typeof window.cancelAnimationFrame === 'function'
            ? window.cancelAnimationFrame.bind(window)
            : window.clearTimeout.bind(window);
        advancedSettingsScrollCancel = cancelFrame;
        // Two frames let the newly un-hidden panel participate in layout before
        // its geometry is measured, including in narrow-height modal layouts.
        advancedSettingsScrollFrame = requestFrame(() => {
            if (requestId !== advancedSettingsScrollRequest) return;
            advancedSettingsScrollCancel = cancelFrame;
            advancedSettingsScrollFrame = requestFrame(() => {
                scrollAdvancedSettingsContentIntoView(requestId);
            });
        });
    }

    function setAdvancedSettingsExpanded(expanded) {
        const isExpanded = Boolean(expanded);
        els.advancedSettingsToggle.setAttribute('aria-expanded', String(isExpanded));
        els.advancedSettingsPanel.hidden = !isExpanded;
        if (isExpanded) scheduleAdvancedSettingsScroll();
        else cancelAdvancedSettingsScroll();
    }
    // ADVANCED_SETTINGS_SCROLL_END

    function showModelValidationError(message) {
        setAdvancedSettingsExpanded(true);
        els.reasoningModel.classList.add('v2-input-invalid');
        els.executionModel.classList.add('v2-input-invalid');
        setFormError(message || 'Choose a supported replacement model before saving this project.');
        window.setTimeout(() => {
            try { els.reasoningModel.focus({ preventScroll: true }); }
            catch (error) { els.reasoningModel.focus(); }
        }, 0);
    }

    function showProjectSettingsValidationError(message) {
        setAdvancedSettingsExpanded(true);
        const target = /query count/i.test(message || '') ? els.queryCount : els.reasoningModel;
        target.classList.add('v2-input-invalid');
        setFormError(message || 'Review the Advanced Settings before saving this project.');
        window.setTimeout(() => {
            try { target.focus({ preventScroll: true }); }
            catch (error) { target.focus(); }
        }, 0);
    }

    function showProjectNameValidationError(message) {
        els.name.classList.add('v2-input-invalid');
        setFormError(message || 'Choose a unique project name.');
        window.setTimeout(() => {
            try { els.name.focus({ preventScroll: true }); }
            catch (error) { els.name.focus(); }
        }, 0);
    }

    function showProjectLocationValidationError(message) {
        setAdvancedSettingsExpanded(true);
        setFormError(message || 'Choose a different project name or location.');
        window.setTimeout(() => {
            try { els.chooseLocation.focus({ preventScroll: true }); }
            catch (error) { els.chooseLocation.focus(); }
        }, 0);
    }

    const PROJECT_DIRECTORY_SLUG_MAX_LENGTH = 48;

    function projectDirectorySlug(name) {
        const normalized = String(name || '').normalize('NFKC').toLowerCase();
        const parts = [];
        let separatorPending = false;
        Array.from(normalized).forEach((character) => {
            if (/^[\p{L}\p{N}]$/u.test(character)) {
                if (separatorPending && parts.length) parts.push('-');
                parts.push(character);
                separatorPending = false;
            } else {
                separatorPending = parts.length > 0;
            }
        });
        const slug = Array.from(parts.join('').replace(/^[ ._-]+|[ ._-]+$/g, ''))
            .slice(0, PROJECT_DIRECTORY_SLUG_MAX_LENGTH).join('')
            .replace(/[ ._-]+$/g, '');
        return slug;
    }

    function projectDirectoryName(projectName) {
        return projectDirectorySlug(projectName);
    }

    function projectDestination(parent, projectName, useCanonicalLeaf) {
        if (!parent) return '';
        if (
            modalMode === 'edit' && currentProjectRoot && parent === currentProjectParent &&
            !useCanonicalLeaf && editingProjectImportedInPlace
        ) {
            return currentProjectRoot;
        }
        return parent.replace(/\/$/, '') + '/' + projectDirectoryName(
            projectName == null ? els.name.value : projectName
        );
    }

    function renderProjectLocation() {
        if (modalMode === 'import') {
            els.projectLocationPath.textContent = importProjectRoot;
            els.projectLocationStatus.textContent = 'This existing folder remains the exact project root.';
            els.projectLocationStatus.classList.remove('is-error');
            els.projectLocationStatus.setAttribute('role', 'status');
            els.locateProject.hidden = true;
            els.chooseLocation.disabled = true;
            els.useDefaultLocation.disabled = true;
            els.projectLocationActions.hidden = true;
            return;
        }
        els.projectLocationActions.hidden = false;
        const destination = projectDestination(
            pendingProjectParent, els.name.value, pendingCanonicalDefaultLocation
        );
        els.projectLocationPath.textContent = destination;
        const relocating = modalMode === 'edit' && currentProjectRoot && destination !== currentProjectRoot;
        const managedNameRename = relocating && !editingProjectImportedInPlace &&
            pendingProjectParent === currentProjectParent && !pendingCanonicalDefaultLocation;
        const unavailable = !projectLocationAvailable;
        els.projectLocationStatus.textContent = unavailable
            ? 'The saved project location is unavailable. Locate the project before saving changes.'
            : projectHasActiveRound
            ? 'Stop the active round before changing this project location.'
            : managedNameRename
            ? 'The managed project folder will be renamed when you save changes.'
            : relocating
            ? 'The complete project will move when you save changes.'
            : (modalMode === 'create' ? 'This folder will be created when you create the project.' : 'Current project location.');
        els.projectLocationStatus.classList.toggle('is-error', unavailable);
        els.projectLocationStatus.setAttribute('role', unavailable ? 'alert' : 'status');
        els.locateProject.hidden = modalMode !== 'edit' || projectLocationAvailable;
        els.locateProject.disabled = recoveryProcessing || projectHasActiveRound;
        els.chooseLocation.disabled = projectLocationRunning || projectHasActiveRound || !projectLocationAvailable;
        const canonicalDefaultDestination = projectDestination(
            els.form.dataset.defaultProjectParent, els.name.value, true
        );
        const defaultLocationSelected = pendingProjectParent === els.form.dataset.defaultProjectParent && (
            modalMode === 'create' || pendingCanonicalDefaultLocation ||
            canonicalDefaultDestination === currentProjectRoot
        );
        els.useDefaultLocation.disabled = projectLocationRunning || projectHasActiveRound ||
            !projectLocationAvailable || defaultLocationSelected;
    }

    async function chooseProjectLocation() {
        if (projectLocationRunning) return;
        projectLocationRunning = true;
        renderProjectLocation();
        try {
            const resp = await fetch('/api/project-location/choose', {
                method: 'POST', credentials: 'include', headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) throw new Error((data && data.error) || 'Could not choose a project location.');
            if (!data.cancelled) {
                pendingProjectParent = data.parent;
                pendingCanonicalDefaultLocation = false;
            }
        } catch (error) {
            setFormError(error && error.message ? error.message : 'Could not choose a project location.');
        } finally {
            projectLocationRunning = false;
            renderProjectLocation();
        }
    }

    function resetSubmitState() {
        projectSubmissionRunning = false;
        els.submit.disabled = false;
        els.submit.classList.remove('is-busy');
        applyMode();
    }

    function relocationFocusableElements() {
        return Array.from(els.relocationOverlay.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), ' +
            'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )).filter((element) => !element.hidden && !element.closest('[hidden]'));
    }

    function setRelocationBusy(busy) {
        relocationProcessing = Boolean(busy);
        els.relocationCancel.disabled = relocationProcessing;
        els.relocationConfirm.disabled = relocationProcessing;
        els.relocationConfirm.classList.toggle('is-busy', relocationProcessing);
        if (relocationProcessing) {
            els.relocationDialog.setAttribute('aria-busy', 'true');
            els.relocationConfirm.setAttribute('aria-busy', 'true');
            els.relocationConfirm.textContent = 'Moving…';
        } else {
            els.relocationDialog.removeAttribute('aria-busy');
            els.relocationConfirm.removeAttribute('aria-busy');
            els.relocationConfirm.textContent = 'Move Project';
        }
    }

    function openRelocationConfirmation(body, destination) {
        pendingRelocationBody = body;
        els.relocationDestination.textContent = destination;
        setRelocationBusy(false);
        // Keep the pending Edit form mounted, but remove it from pointer,
        // keyboard, and accessibility interaction while its confirmation is open.
        els.overlay.setAttribute('inert', '');
        els.relocationOverlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        window.setTimeout(() => {
            if (!els.relocationOverlay.hidden && !relocationProcessing) {
                els.relocationCancel.focus();
            }
        }, 30);
    }

    function closeRelocationConfirmation(options) {
        const config = Object.assign({ force: false, restoreFocus: true }, options || {});
        if (relocationProcessing && !config.force) return;
        setRelocationBusy(false);
        els.relocationOverlay.hidden = true;
        els.overlay.removeAttribute('inert');
        pendingRelocationBody = null;
        // The Edit modal is still open, so body scroll remains locked.
        document.body.classList.add('v2-modal-open');
        if (config.restoreFocus && els.submit && typeof els.submit.focus === 'function') {
            els.submit.focus();
        }
    }

    function trapRelocationFocus(event) {
        const focusable = relocationFocusableElements();
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    function recoveryFocusableElements() {
        return Array.from(els.recoveryOverlay.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), ' +
            'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )).filter((element) => !element.hidden && !element.closest('[hidden]'));
    }

    function trapRecoveryFocus(event) {
        const focusable = recoveryFocusableElements();
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
        }
    }

    function setRecoveryBusy(busy) {
        recoveryProcessing = Boolean(busy);
        els.recoveryCancel.disabled = recoveryProcessing;
        els.recoveryLocate.disabled = recoveryProcessing;
        els.recoveryLocate.classList.toggle('is-busy', recoveryProcessing);
        if (recoveryProcessing) {
            els.recoveryDialog.setAttribute('aria-busy', 'true');
            els.recoveryLocate.setAttribute('aria-busy', 'true');
            els.recoveryLocate.textContent = 'Locating…';
        } else {
            els.recoveryDialog.removeAttribute('aria-busy');
            els.recoveryLocate.removeAttribute('aria-busy');
            els.recoveryLocate.textContent = 'Locate Project';
        }
        renderProjectLocation();
    }

    function openProjectRecovery(project, options) {
        if (!project || !project.id) return;
        const config = Object.assign({ action: 'continue', body: null, invoker: null }, options || {});
        recoveryLastFocused = config.invoker || document.activeElement;
        pendingRecovery = { project: project, action: config.action, body: config.body };
        els.recoveryPath.textContent = project.registered_path || project.project_root || currentProjectRoot || '';
        els.recoveryError.textContent = '';
        els.recoveryError.hidden = true;
        setRecoveryBusy(false);
        if (!els.overlay.hidden) els.overlay.setAttribute('inert', '');
        else $('projects-root').setAttribute('inert', '');
        els.recoveryOverlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        window.setTimeout(() => {
            if (!els.recoveryOverlay.hidden && !recoveryProcessing) els.recoveryCancel.focus();
        }, 30);
    }

    function closeProjectRecovery(options) {
        const config = Object.assign({ force: false, restoreFocus: true }, options || {});
        if (recoveryProcessing && !config.force) return;
        setRecoveryBusy(false);
        els.recoveryOverlay.hidden = true;
        els.overlay.removeAttribute('inert');
        $('projects-root').removeAttribute('inert');
        pendingRecovery = null;
        if (els.overlay.hidden) document.body.classList.remove('v2-modal-open');
        else document.body.classList.add('v2-modal-open');
        if (config.restoreFocus && recoveryLastFocused && recoveryLastFocused.focus) {
            recoveryLastFocused.focus();
        }
        recoveryLastFocused = null;
    }

    async function requestProjectLocationRecovery(projectId) {
        const resp = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/locate', {
            method: 'POST', credentials: 'include', headers: { 'Accept': 'application/json' },
        });
        const data = await readJson(resp);
        if (!(resp.ok && data && data.success)) {
            throw new Error((data && data.error) || 'Could not reconnect this project.');
        }
        return data;
    }

    function applyRecoveredEditLocation(projectId, data) {
        if (modalMode !== 'edit' || editingProjectId !== projectId) return false;
        const pendingUsedSavedParent = pendingProjectParent === currentProjectParent;
        currentProjectRoot = data.root;
        currentProjectParent = data.parent;
        projectLocationAvailable = true;
        if (pendingUsedSavedParent) pendingProjectParent = data.parent;
        renderProjectLocation();
        return pendingUsedSavedParent;
    }

    async function locateMissingProject() {
        if (recoveryProcessing || !pendingRecovery) return;
        const requestState = pendingRecovery;
        setRecoveryBusy(true);
        els.recoveryError.hidden = true;
        try {
            const data = await requestProjectLocationRecovery(requestState.project.id);
            if (data.cancelled) {
                setRecoveryBusy(false);
                els.recoveryCancel.focus();
                return;
            }

            const pendingUsedSavedParent = applyRecoveredEditLocation(requestState.project.id, data);
            closeProjectRecovery({ force: true, restoreFocus: false });

            if (requestState.action === 'continue') {
                window.location.href = data.destination;
                return;
            }
            if (requestState.action === 'edit-save' && requestState.body) {
                const body = new URLSearchParams(requestState.body.toString());
                if (pendingUsedSavedParent) body.set('project_parent_dir', data.parent);
                await submitPreparedProject(body);
                return;
            }
            await loadProjects();
            if (els.locateProject && !els.locateProject.hidden) els.locateProject.focus();
        } catch (error) {
            els.recoveryError.textContent = error && error.message ? error.message : 'Could not reconnect this project.';
            els.recoveryError.hidden = false;
            setRecoveryBusy(false);
            els.recoveryCancel.focus();
        }
    }

    function setInlineLocationRecoveryBusy(busy) {
        recoveryProcessing = Boolean(busy);
        renderProjectLocation();
        els.locateProject.classList.toggle('is-busy', recoveryProcessing);
        if (recoveryProcessing) {
            els.locateProject.setAttribute('aria-busy', 'true');
            els.locateProject.textContent = 'Locating…';
        } else {
            els.locateProject.removeAttribute('aria-busy');
            els.locateProject.textContent = 'Locate Project';
        }
    }

    async function locateEditProjectInline() {
        if (
            recoveryProcessing || modalMode !== 'edit' || !editingProjectId ||
            projectLocationAvailable || projectHasActiveRound
        ) return;
        const projectId = editingProjectId;
        const invoker = els.locateProject;
        setFormError(null);
        setInlineLocationRecoveryBusy(true);
        try {
            const data = await requestProjectLocationRecovery(projectId);
            if (data.cancelled) return;
            applyRecoveredEditLocation(projectId, data);
            setFormError(null);
        } catch (error) {
            setFormError(error && error.message ? error.message : 'Could not reconnect this project.');
        } finally {
            setInlineLocationRecoveryBusy(false);
            const focusTarget = projectLocationAvailable ? els.chooseLocation : invoker;
            if (focusTarget && typeof focusTarget.focus === 'function') focusTarget.focus();
        }
    }

    function importErrorForInvoker(invoker) {
        return invoker === els.importEmptyBtn ? els.importEmptyError : els.importError;
    }

    function setImportInlineError(invoker, message) {
        const target = importErrorForInvoker(invoker);
        if (!target) return;
        target.textContent = message || '';
        target.hidden = !message;
    }

    function setImportPickerBusy(invoker, busy) {
        projectImportRunning = Boolean(busy);
        if (!invoker) return;
        invoker.disabled = projectImportRunning;
        if (projectImportRunning) invoker.setAttribute('aria-busy', 'true');
        else invoker.removeAttribute('aria-busy');
    }

    function renderImportWarnings(warnings) {
        els.importWarnings.innerHTML = '';
        (warnings || []).forEach((warning) => {
            const item = document.createElement('li');
            item.textContent = warning;
            els.importWarnings.appendChild(item);
        });
    }

    function openImportForm(root, metadata, previewToken, invoker) {
        modalMode = 'import'; editingProjectId = null; persistedEditSnapshot = null;
        editingProjectImportedInPlace = true;
        importProjectRoot = String(root || '');
        importPreviewToken = String(previewToken || '');
        importInvoker = invoker;
        els.form.reset();
        clearInvalid(els.reasoningModel); clearInvalid(els.executionModel); clearInvalid(els.queryCount);
        setAdvancedSettingsExpanded(false);
        if (els.projectDataStatus) els.projectDataStatus.textContent = '';
        els.reasoningModel.value = els.reasoningModel.getAttribute('data-default-model') || els.reasoningModel.value;
        els.executionModel.value = els.executionModel.getAttribute('data-default-model') || els.executionModel.value;
        els.queryCount.value = els.queryCount.getAttribute('data-default-query-count') || '12';
        els.deepResearch.checked = true; els.webSearch.checked = true;
        els.name.value = (metadata && metadata.project_name) || '';
        els.objective.value = (metadata && metadata.task_description) || '';
        els.context.value = (metadata && metadata.dataset_context) || '';
        els.importRoot.textContent = importProjectRoot;
        renderImportWarnings(metadata && metadata.warnings);
        pendingProjectParent = importProjectRoot.replace(/\/[^/]+\/?$/, '') || '/';
        pendingCanonicalDefaultLocation = false;
        currentProjectRoot = importProjectRoot;
        currentProjectParent = pendingProjectParent;
        projectLocationAvailable = true;
        projectHasActiveRound = false;
        selectedDataSources = [];
        librarySources = [];
        renderSelectedDataHierarchy();
        resetSubmitState();
        setFormError(null);
        renderProjectLocation();
        showModal();
        loadLibrarySources();
    }

    async function chooseImportProject(invoker) {
        if (projectImportRunning || !invoker) return;
        setImportInlineError(invoker, null);
        setImportPickerBusy(invoker, true);
        let restoreFocus = false;
        try {
            const resp = await fetch('/api/project-import/choose', {
                method: 'POST', credentials: 'include', headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                throw new Error((data && data.error) || 'Could not inspect the selected project folder.');
            }
            if (data.cancelled) {
                restoreFocus = true;
                return;
            }
            openImportForm(data.root, data.metadata || {}, data.preview_token, invoker);
        } catch (error) {
            setImportInlineError(
                invoker,
                error && error.message ? error.message : 'Could not inspect the selected project folder.'
            );
            restoreFocus = true;
        } finally {
            setImportPickerBusy(invoker, false);
            if (restoreFocus) invoker.focus();
        }
    }

    function openCreate() {
        modalMode = 'create'; editingProjectId = null; persistedEditSnapshot = null; importInvoker = null; importPreviewToken = '';
        editingProjectImportedInPlace = false;
        els.form.reset();
        clearInvalid(els.reasoningModel); clearInvalid(els.executionModel); clearInvalid(els.queryCount);
        setAdvancedSettingsExpanded(false);
        if (els.projectDataStatus) els.projectDataStatus.textContent = '';
        els.reasoningModel.value = els.reasoningModel.getAttribute('data-default-model') || els.reasoningModel.value;
        els.executionModel.value = els.executionModel.getAttribute('data-default-model') || els.executionModel.value;
        els.queryCount.value = els.queryCount.getAttribute('data-default-query-count') || '12';
        els.deepResearch.checked = true; els.webSearch.checked = true;
        pendingProjectParent = els.form.dataset.defaultProjectParent;
        pendingCanonicalDefaultLocation = false;
        currentProjectRoot = ''; currentProjectParent = ''; projectLocationAvailable = true;
        projectHasActiveRound = false; renderProjectLocation();
        selectedDataSources = [];
        librarySources = [];
        renderSelectedDataHierarchy();
        resetSubmitState();
        setFormError(null);
        showModal();
        loadLibrarySources();
    }

    async function openEdit(project) {
        modalMode = 'edit'; editingProjectId = project.id; persistedEditSnapshot = null; importInvoker = null; importPreviewToken = '';
        editingProjectImportedInPlace = project.imported_in_place === true;
        els.form.reset();
        clearInvalid(els.reasoningModel); clearInvalid(els.executionModel); clearInvalid(els.queryCount);
        setAdvancedSettingsExpanded(false);
        if (els.projectDataStatus) els.projectDataStatus.textContent = '';
        selectedDataSources = [];
        librarySources = [];
        renderSelectedDataHierarchy();
        resetSubmitState();
        setFormError(null);
        // Prefill from the row immediately, then hydrate full project detail.
        els.name.value = project.display_name || '';
        els.objective.value = '';
        els.reasoningModel.value = els.reasoningModel.getAttribute('data-default-model') || els.reasoningModel.value;
        els.executionModel.value = els.executionModel.getAttribute('data-default-model') || els.executionModel.value;
        els.queryCount.value = els.queryCount.getAttribute('data-default-query-count') || '12';
        els.deepResearch.checked = true; els.webSearch.checked = true;
        pendingProjectParent = els.form.dataset.defaultProjectParent;
        pendingCanonicalDefaultLocation = false;
        currentProjectRoot = ''; currentProjectParent = ''; projectLocationAvailable = project.location_available !== false;
        projectHasActiveRound = false; renderProjectLocation();
        els.context.value = '';
        showModal();
        try {
            const resp = await fetch('/api/projects/' + encodeURIComponent(project.id) + '?selection_only=1', {
                credentials: 'include', headers: { 'Accept': 'application/json' },
            });
            const data = await resp.json();
            if (resp.ok && data && data.success && data.project && editingProjectId === project.id) {
                editingProjectImportedInPlace = data.project.imported_in_place === true;
                els.name.value = data.project.display_name || '';
                els.objective.value = data.project.task_description || '';
                els.reasoningModel.value = data.project.reasoning_model || els.reasoningModel.getAttribute('data-default-model') || '';
                els.executionModel.value = data.project.execution_model || els.executionModel.getAttribute('data-default-model') || '';
                els.queryCount.value = data.project.query_count == null
                    ? (els.queryCount.getAttribute('data-default-query-count') || '12')
                    : String(data.project.query_count);
                els.deepResearch.checked = data.project.deep_research !== false;
                els.webSearch.checked = data.project.web_search !== false;
                els.context.value = data.project.dataset_context || '';
                if (data.project.project_models_valid === false) {
                    showModelValidationError(data.project.project_models_error);
                }
                currentProjectRoot = data.project.project_location.root;
                currentProjectParent = data.project.project_location.parent;
                pendingProjectParent = currentProjectParent;
                pendingCanonicalDefaultLocation = false;
                projectHasActiveRound = data.project.project_location.running === true;
                projectLocationAvailable = data.project.project_location.available !== false;
                renderProjectLocation();
                const inputData = data.project.data_sources || {};
                selectedDataSources = inputData.selection || [];
                renderSelectedDataHierarchy();
            }
        } catch (err) { /* keep the row prefill on failure */ }
        await loadLibrarySources();
        if (modalMode === 'edit' && editingProjectId === project.id) {
            capturePersistedEditSnapshot();
        }
    }

    function openDeleteConfirmation(project) {
        if (!project || !project.id) return;
        pendingDeleteProject = project;
        pendingDeleteUnavailable = project.location_available === false;
        pendingDeleteImported = project.imported_in_place === true;
        deleteLastFocused = document.activeElement;
        renderDeleteConfirmationCopy();
        els.deleteError.textContent = '';
        els.deleteError.hidden = true;
        els.deleteOverlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        setTimeout(() => els.deleteCancel.focus(), 30);
    }

    function renderDeleteConfirmationCopy() {
        const projectName = pendingDeleteProject && pendingDeleteProject.display_name
            ? pendingDeleteProject.display_name : 'This project';
        if (pendingDeleteImported) {
            els.deleteTitle.textContent = ['Remove', 'this project?'].join(' ');
            els.deleteNormalCopy.hidden = true;
            els.deleteImportedCopy.hidden = false;
            els.deleteUnavailableCopy.hidden = true;
            els.deleteConfirm.textContent = 'Remove Project';
        } else if (pendingDeleteUnavailable) {
            els.deleteTitle.textContent = 'Remove unavailable project?';
            els.deleteNormalCopy.hidden = true;
            els.deleteImportedCopy.hidden = true;
            els.deleteUnavailableCopy.hidden = false;
            els.deleteConfirm.textContent = 'Remove Project';
        } else {
            els.deleteTitle.textContent = 'Delete this project?';
            els.deleteName.textContent = projectName;
            els.deleteNormalCopy.hidden = false;
            els.deleteImportedCopy.hidden = true;
            els.deleteUnavailableCopy.hidden = true;
            els.deleteConfirm.textContent = 'Delete';
        }
    }

    function closeDeleteConfirmation() {
        if (els.deleteConfirm.disabled) return;
        els.deleteOverlay.hidden = true;
        document.body.classList.remove('v2-modal-open');
        pendingDeleteProject = null;
        pendingDeleteUnavailable = false;
        pendingDeleteImported = false;
        if (deleteLastFocused && deleteLastFocused.focus) deleteLastFocused.focus();
    }

    async function confirmDeleteProject() {
        const project = pendingDeleteProject;
        if (!project || !project.id) return;
        els.deleteConfirm.disabled = true;
        els.deleteConfirm.classList.add('is-busy');
        els.deleteConfirm.setAttribute('aria-busy', 'true');
        const registryOnly = pendingDeleteImported || pendingDeleteUnavailable;
        els.deleteConfirm.textContent = registryOnly ? 'Removing…' : 'Moving to Trash…';
        els.deleteCancel.disabled = true;
        try {
            const endpoint = '/api/projects/' + encodeURIComponent(project.id) +
                (pendingDeleteUnavailable && !pendingDeleteImported ? '?confirm_unavailable=true' : '');
            const resp = await fetch(endpoint, {
                method: 'DELETE',
                credentials: 'include',
                headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                if (data && data.code === 'project_unavailable_delete_confirmation_required') {
                    pendingDeleteUnavailable = true;
                    renderDeleteConfirmationCopy();
                    els.deleteError.textContent = '';
                    els.deleteError.hidden = true;
                    return;
                }
                els.deleteError.textContent = (data && data.error) ||
                    (registryOnly ? 'Could not remove this project.' : 'Could not move this project to Trash.');
                els.deleteError.hidden = false;
                return;
            }
            els.deleteOverlay.hidden = true;
            document.body.classList.remove('v2-modal-open');
            pendingDeleteProject = null;
            pendingDeleteUnavailable = false;
            pendingDeleteImported = false;
            loadProjects();
        } catch (err) {
            els.deleteError.textContent = err && err.message ? err.message :
                (registryOnly ? 'Network error while removing this project.' : 'Network error while moving this project to Trash.');
            els.deleteError.hidden = false;
        } finally {
            els.deleteConfirm.disabled = false;
            els.deleteConfirm.classList.remove('is-busy');
            els.deleteConfirm.removeAttribute('aria-busy');
            if (pendingDeleteProject) renderDeleteConfirmationCopy();
            els.deleteCancel.disabled = false;
        }
    }

    function resetProjectModalScroll() {
        // The body is the project shell's only normal scroll boundary. Hidden
        // elements retain scrollTop, so explicitly clear both it and the
        // overlay fallback before every Create/Edit opening.
        els.overlay.scrollTop = 0;
        els.modalBody.scrollTop = 0;
    }

    function focusProjectNameAtTop() {
        resetProjectModalScroll();
        try {
            els.name.focus({ preventScroll: true });
        } catch (err) {
            els.name.focus();
        }
        // The fallback focus call may scroll an ancestor in older browsers.
        resetProjectModalScroll();
    }

    function showModal() {
        lastFocused = importInvoker || document.activeElement;
        importInvoker = null;
        resetProjectModalScroll();
        els.overlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        resetProjectModalScroll();
        setTimeout(focusProjectNameAtTop, 30);
    }

    function unsavedFocusableElements() {
        return Array.from(els.unsavedOverlay.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), ' +
            'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )).filter((element) => !element.hidden && !element.closest('[hidden]'));
    }

    function setUnsavedChangesBusy(busy) {
        unsavedProcessing = Boolean(busy);
        els.unsavedDiscard.disabled = unsavedProcessing;
        els.unsavedSave.disabled = unsavedProcessing;
        els.unsavedSave.classList.toggle('is-busy', unsavedProcessing);
        if (unsavedProcessing) {
            els.unsavedDialog.setAttribute('aria-busy', 'true');
            els.unsavedSave.setAttribute('aria-busy', 'true');
            els.unsavedSave.textContent = 'Saving…';
        } else {
            els.unsavedDialog.removeAttribute('aria-busy');
            els.unsavedSave.removeAttribute('aria-busy');
            els.unsavedSave.textContent = 'Save Changes';
        }
    }

    function openUnsavedChangesConfirmation(invoker) {
        if (!els.unsavedOverlay.hidden) return;
        unsavedCloseInvoker = invoker && invoker !== els.overlay ? invoker : document.activeElement;
        setUnsavedChangesBusy(false);
        els.overlay.setAttribute('inert', '');
        els.unsavedOverlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        window.setTimeout(() => {
            if (!els.unsavedOverlay.hidden && !unsavedProcessing) {
                els.unsavedSave.focus();
            }
        }, 30);
    }

    function closeUnsavedChangesConfirmation(options) {
        const config = Object.assign({ force: false, restoreFocus: true, preserveBusy: false }, options || {});
        if (unsavedProcessing && !config.force) return;
        if (!config.preserveBusy) setUnsavedChangesBusy(false);
        els.unsavedOverlay.hidden = true;
        els.overlay.removeAttribute('inert');
        document.body.classList.add('v2-modal-open');
        const focusTarget = unsavedCloseInvoker && !unsavedCloseInvoker.hidden
            ? unsavedCloseInvoker
            : els.cancel;
        unsavedCloseInvoker = null;
        if (config.restoreFocus && focusTarget && typeof focusTarget.focus === 'function') {
            focusTarget.focus();
        }
    }

    function trapUnsavedChangesFocus(event) {
        const focusable = unsavedFocusableElements();
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
        }
    }

    function closeModal(options) {
        const supplied = options && options.currentTarget
            ? { invoker: options.currentTarget }
            : (options || {});
        const config = Object.assign({ force: false, invoker: document.activeElement }, supplied);
        if (els.submit.classList.contains('is-busy')) return false;
        if (!config.force && editHasUnsavedChanges()) {
            openUnsavedChangesConfirmation(config.invoker);
            return false;
        }
        cancelAdvancedSettingsScroll();
        els.overlay.hidden = true;
        els.overlay.removeAttribute('inert');
        resetProjectModalScroll();
        document.body.classList.remove('v2-modal-open');
        persistedEditSnapshot = null;
        if (lastFocused && lastFocused.focus) lastFocused.focus();
        return true;
    }

    function discardUnsavedChanges() {
        if (unsavedProcessing) return;
        persistedEditSnapshot = null;
        closeUnsavedChangesConfirmation({ force: true, restoreFocus: false });
        closeModal({ force: true });
    }

    async function saveUnsavedChanges() {
        if (unsavedProcessing) return;
        setUnsavedChangesBusy(true);
        closeUnsavedChangesConfirmation({ force: true, restoreFocus: false, preserveBusy: true });
        try {
            await submitCurrentProject();
        } finally {
            setUnsavedChangesBusy(false);
        }
    }

    function setFormError(msg) {
        if (!msg) { els.formError.hidden = true; els.formError.textContent = ''; return; }
        els.formError.textContent = msg;
        els.formError.hidden = false;
    }

    async function readJson(resp) {
        try { return await resp.json(); } catch (err) { return null; }
    }

    function trapDataSelectorFocus(event) {
        const focusable = Array.from(els.dataLibraryOverlay.querySelectorAll(
            'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
        )).filter((element) => !element.hidden && !element.closest('[hidden]'));
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
        }
    }

    function clearInvalid(el) {
        if (el) el.classList.remove('v2-input-invalid');
    }

    function setSubmitBusy() {
        projectSubmissionRunning = true;
        els.submit.disabled = true;
        els.submit.classList.add('is-busy');
        els.submit.textContent = modalMode === 'create'
            ? 'Creating…'
            : modalMode === 'import'
            ? 'Importing…'
            : 'Saving…';
    }

    async function persistProject(body, options) {
        if (projectSubmissionRunning) return false;
        const config = Object.assign({ relocation: false }, options || {});
        setSubmitBusy();
        try {
            const endpoint = modalMode === 'edit'
                ? '/api/projects/' + encodeURIComponent(editingProjectId) + '/edit'
                : modalMode === 'import'
                ? '/api/projects/import'
                : '/api/projects';
            const resp = await fetch(endpoint, { method: 'POST', credentials: 'include', body: body });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                const message = (data && data.error) ||
                    (modalMode === 'edit' ? 'Could not save changes.' : modalMode === 'import'
                        ? 'Could not import the project.' : 'Could not create the project.');
                if (data && data.code === 'project_location_recovery_required') {
                    const retryBody = new URLSearchParams(body.toString());
                    retryBody.delete('confirm_project_relocation');
                    resetSubmitState();
                    if (config.relocation) {
                        closeRelocationConfirmation({ force: true, restoreFocus: false });
                    }
                    projectLocationAvailable = false;
                    renderProjectLocation();
                    openProjectRecovery({
                        id: editingProjectId,
                        registered_path: data.registered_path || currentProjectRoot,
                    }, { action: 'edit-save', body: retryBody, invoker: els.submit });
                    return false;
                }
                const projectNameError = data && [
                    'duplicate_project_name', 'invalid_project_name',
                ].includes(data.code);
                const projectLocationError = data && [
                    'duplicate_project_slug', 'invalid_project_location',
                ].includes(data.code);
                if (data && data.code === 'unsupported_project_model') showModelValidationError(message);
                else if (data && data.code === 'invalid_project_settings') showProjectSettingsValidationError(message);
                else if (!projectNameError && !projectLocationError) setFormError(message);
                resetSubmitState();
                if (config.relocation) {
                    closeRelocationConfirmation({
                        force: true, restoreFocus: !projectNameError && !projectLocationError,
                    });
                }
                if (projectNameError) showProjectNameValidationError(message);
                else if (projectLocationError) showProjectLocationValidationError(message);
                return false;
            }

            if (modalMode === 'edit') {
                if (config.relocation) {
                    closeRelocationConfirmation({ force: true, restoreFocus: false });
                }
                resetSubmitState();
                closeModal({ force: true });
                loadProjects();
                return true;
            }

            if (modalMode === 'import') {
                if (typeof data.projects_url !== 'string' || !data.projects_url) {
                    setFormError('The project was imported, but DiscoveryAgent could not return to Projects. Reload this page to continue.');
                    resetSubmitState();
                    return false;
                }
                resetSubmitState();
                closeModal({ force: true });
                window.location.replace(data.projects_url);
                return true;
            }

            if (typeof data.projects_url !== 'string' || !data.projects_url) {
                setFormError('The project was created, but DiscoveryAgent could not return to Projects. Reload this page to continue.');
                resetSubmitState();
                return false;
            }

            // Clear the completed workflow before replacing this history entry.
            // A fresh Projects response then renders the new project in normal order.
            resetSubmitState();
            closeModal({ force: true });
            window.location.replace(data.projects_url);
            return true;
        } catch (err) {
            setFormError(err && err.message ? err.message : (modalMode === 'edit'
                ? 'Could not save changes. Please try again.'
                : modalMode === 'import'
                ? 'Could not import the project. Please try again.'
                : 'Could not create the project. Please try again.'));
            resetSubmitState();
            if (config.relocation) {
                closeRelocationConfirmation({ force: true, restoreFocus: true });
            }
            return false;
        }
    }

    async function confirmProjectRelocation() {
        if (relocationProcessing || !pendingRelocationBody) return;
        const body = new URLSearchParams(pendingRelocationBody.toString());
        body.append('confirm_project_relocation', 'true');
        setRelocationBusy(true);
        await persistProject(body, { relocation: true });
    }

    async function submitPreparedProject(body) {
        if (modalMode === 'import') return await persistProject(body);
        if (modalMode === 'edit' && !projectLocationAvailable) {
            if (projectHasActiveRound) {
                setFormError('Stop the active round before reconnecting this project location.');
                return;
            }
            openProjectRecovery({ id: editingProjectId, registered_path: currentProjectRoot }, {
                action: 'edit-save', body: body, invoker: els.submit,
            });
            return;
        }

        const requestedParent = body.get('project_parent_dir') || pendingProjectParent;
        const destination = projectDestination(
            requestedParent,
            body.get('project_name'),
            body.get('use_canonical_default_location') === 'true'
        );
        const relocating = modalMode === 'edit' && currentProjectRoot && destination !== currentProjectRoot;
        const automaticManagedRename = relocating && !editingProjectImportedInPlace &&
            requestedParent === currentProjectParent &&
            body.get('use_canonical_default_location') !== 'true';
        if (relocating) {
            if (projectHasActiveRound) {
                const message = 'Stop the active round before changing this project location.';
                if (automaticManagedRename) showProjectNameValidationError(message);
                else setFormError(message);
                return;
            }
            if (!automaticManagedRename) {
                openRelocationConfirmation(body, destination);
                return;
            }
        }
        return await persistProject(body);
    }

    async function submitCurrentProject() {
        const name = els.name.value.trim();
        const objective = els.objective.value.trim();
        const reasoningModel = els.reasoningModel.value;
        const executionModel = els.executionModel.value;
        const context = els.context.value.trim();
        const queryCount = els.queryCount.value.trim();

        // All three fields are required before DiscoveryAgent can generate
        // hypotheses and retain the dataset's persistent context.
        const required = [
            { el: els.name, value: name, label: 'Project name' },
            { el: els.objective, value: objective, label: 'Research objective' },
            { el: els.context, value: context, label: 'Project context' },
        ];
        required.forEach((f) => clearInvalid(f.el));
        clearInvalid(els.reasoningModel); clearInvalid(els.executionModel); clearInvalid(els.queryCount);
        const missing = required.filter((f) => !f.value);
        if (missing.length) {
            missing.forEach((f) => f.el.classList.add('v2-input-invalid'));
            setFormError('Please complete the required fields: ' +
                missing.map((f) => f.label).join(', ') + '.');
            missing[0].el.focus();
            return;
        }
        if (!projectDirectorySlug(name)) {
            showProjectNameValidationError('Project name must contain at least one letter or number.');
            return;
        }
        if (!queryCount || !/^\d+$/.test(queryCount) || Number(queryCount) < 1 || Number(queryCount) > 50) {
            showProjectSettingsValidationError(
                'Query Count must be a whole number from 1 to 50.'
            );
            return;
        }
        setFormError(null);
        // Project metadata and normalized Data Library selections remain
        // pending until the outer Create/Edit form succeeds.
        const body = new URLSearchParams();
        body.append('project_name', name);
        body.append('task_description', objective);
        body.append('reasoning_model', reasoningModel);
        body.append('execution_model', executionModel);
        body.append('query_count', queryCount);
        body.append('deep_research', String(els.deepResearch.checked));
        body.append('web_search', String(els.webSearch.checked));
        if (modalMode === 'import') {
            body.append('project_root', importProjectRoot);
            body.append('preview_token', importPreviewToken);
        }
        else body.append('project_parent_dir', pendingProjectParent);
        if (modalMode === 'edit' && pendingCanonicalDefaultLocation) {
            body.append('use_canonical_default_location', 'true');
        }
        if (modalMode === 'create') body.append('project_id', els.form.dataset.pendingProjectId);
        body.append('dataset_context', context);
        body.append('data_sources', JSON.stringify(selectedDataSources));

        return await submitPreparedProject(body);
    }

    async function submitForm(e) {
        e.preventDefault();
        return await submitCurrentProject();
    }

    // ---- wiring --------------------------------------------------------------

    function wire() {
        const initialParams = new URLSearchParams(window.location.search);
        pendingEditProjectId = initialParams.get('edit');
        pendingRecoveryProjectId = initialParams.get('recover');
        if (els.openBtn) els.openBtn.addEventListener('click', openCreate);
        if (els.importBtn) els.importBtn.addEventListener('click', (event) => chooseImportProject(event.currentTarget));
        if (els.importEmptyBtn) els.importEmptyBtn.addEventListener('click', (event) => chooseImportProject(event.currentTarget));
        if (els.retry) els.retry.addEventListener('click', loadProjects);
        els.close.addEventListener('click', (e) => closeModal({ invoker: e.currentTarget }));
        els.cancel.addEventListener('click', (e) => closeModal({ invoker: e.currentTarget }));
        els.overlay.addEventListener('mousedown', (e) => {
            if (e.target === els.overlay) closeModal({ invoker: document.activeElement });
        });
        els.deleteCancel.addEventListener('click', closeDeleteConfirmation);
        els.deleteConfirm.addEventListener('click', confirmDeleteProject);
        els.deleteOverlay.addEventListener('mousedown', (e) => {
            if (e.target === els.deleteOverlay) closeDeleteConfirmation();
        });
        els.unsavedDiscard.addEventListener('click', discardUnsavedChanges);
        els.unsavedSave.addEventListener('click', saveUnsavedChanges);
        els.unsavedOverlay.addEventListener('mousedown', (e) => {
            if (e.target === els.unsavedOverlay) closeUnsavedChangesConfirmation();
        });
        els.unsavedOverlay.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Escape') {
                e.preventDefault();
                closeUnsavedChangesConfirmation();
            } else if (e.key === 'Tab') {
                trapUnsavedChangesFocus(e);
            }
        });
        els.relocationCancel.addEventListener('click', () => closeRelocationConfirmation());
        els.relocationConfirm.addEventListener('click', confirmProjectRelocation);
        els.relocationOverlay.addEventListener('mousedown', (e) => {
            if (e.target === els.relocationOverlay) closeRelocationConfirmation();
        });
        els.relocationOverlay.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Escape') {
                e.preventDefault();
                closeRelocationConfirmation();
            } else if (e.key === 'Tab') {
                trapRelocationFocus(e);
            }
        });
        els.recoveryCancel.addEventListener('click', () => closeProjectRecovery());
        els.recoveryLocate.addEventListener('click', locateMissingProject);
        els.recoveryOverlay.addEventListener('mousedown', (e) => {
            if (e.target === els.recoveryOverlay) closeProjectRecovery();
        });
        els.recoveryOverlay.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Escape') {
                e.preventDefault();
                closeProjectRecovery();
            } else if (e.key === 'Tab') {
                trapRecoveryFocus(e);
            }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (!els.dataLibraryOverlay.hidden) { closeDataLibrarySelector(); return; }
            if (!els.unsavedOverlay.hidden) { closeUnsavedChangesConfirmation(); return; }
            if (!els.recoveryOverlay.hidden) { closeProjectRecovery(); return; }
            if (!els.relocationOverlay.hidden) { closeRelocationConfirmation(); return; }
            if (!els.deleteOverlay.hidden) { closeDeleteConfirmation(); return; }
            if (!els.overlay.hidden) { closeModal({ invoker: document.activeElement }); return; }
            closeAllMenus();
        });
        document.addEventListener('click', () => closeAllMenus());
        els.form.addEventListener('submit', submitForm);
        els.advancedSettingsToggle.addEventListener('click', () => {
            setAdvancedSettingsExpanded(
                els.advancedSettingsToggle.getAttribute('aria-expanded') !== 'true'
            );
        });
        els.advancedSettingsToggle.addEventListener('keydown', (event) => {
            if (!['Enter', ' ', 'Spacebar'].includes(event.key)) return;
            event.preventDefault();
            setAdvancedSettingsExpanded(
                els.advancedSettingsToggle.getAttribute('aria-expanded') !== 'true'
            );
        });
        els.locateProject.addEventListener('click', locateEditProjectInline);
        els.chooseLocation.addEventListener('click', chooseProjectLocation);
        els.useDefaultLocation.addEventListener('click', () => {
            pendingProjectParent = els.form.dataset.defaultProjectParent;
            pendingCanonicalDefaultLocation = modalMode === 'edit';
            renderProjectLocation();
        });
        els.selectData.addEventListener('click', openDataLibrarySelector);
        els.selectedDataEmptySelect.addEventListener('click', openDataLibrarySelector);
        els.dataLibraryCancel.addEventListener('click', closeDataLibrarySelector);
        els.dataLibrarySave.addEventListener('click', saveSelectedData);
        els.dataLibraryOverlay.addEventListener('mousedown', (e) => {
            if (e.target === els.dataLibraryOverlay) closeDataLibrarySelector();
        });
        els.dataLibraryOverlay.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Escape') {
                e.preventDefault(); closeDataLibrarySelector();
            } else if (e.key === 'Tab') {
                trapDataSelectorFocus(e);
            } else if (e.key === 'Enter' && e.target.matches('input[type="checkbox"]')) {
                e.preventDefault();
            }
        });
        [els.name, els.objective, els.context].forEach((el) => {
            if (el) el.addEventListener('input', () => clearInvalid(el));
        });
        els.name.addEventListener('input', () => {
            setFormError(null);
            renderProjectLocation();
        });
        [els.reasoningModel, els.executionModel, els.queryCount].forEach((control) => control.addEventListener('change', () => {
            clearInvalid(control);
            setFormError(null);
        }));
        els.queryCount.addEventListener('input', () => {
            clearInvalid(els.queryCount);
            setFormError(null);
        });
        els.queryCount.addEventListener('invalid', (event) => {
            event.preventDefault();
            showProjectSettingsValidationError(
                'Query Count must be a whole number from 1 to 50.'
            );
        });

    }

    document.addEventListener('DOMContentLoaded', () => {
        const helper = window.DiscoveryAgentAppPageAction;
        if (helper && els.primaryAction) pageActionLayout = helper.create({
            root: els.pageRoot, content: els.pageContent, action: els.primaryAction,
        });
        wire();
        loadProjects();
    });
})();
