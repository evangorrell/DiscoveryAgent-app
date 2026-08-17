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
        retry: $('pj-retry'), empty: $('pj-empty'), list: $('pj-list'), header: $('pj-header'),
        openBtn: $('open-new-project'), openBtnEmpty: $('open-new-project-empty'),
        overlay: $('np-overlay'), modal: $('np-modal'), modalBody: $('np-modal-body'), close: $('np-close'),
        cancel: $('np-cancel'), form: $('np-form'), name: $('np-name'), objective: $('np-objective'), context: $('np-context'),
        fileList: $('np-file-list'), formError: $('np-form-error'), submit: $('np-submit'),
        title: $('np-title'), projectState: $('np-project-state'), projectFooter: $('np-project-footer'),
        editDataField: $('np-edit-data-field'), editOpenDataFolder: $('np-edit-open-data-folder'),
        editDataEmpty: $('np-edit-data-empty'), editDataError: $('np-edit-data-error'),
        editDataPathRow: $('np-edit-data-path-row'), editDataPath: $('np-edit-data-path'),
        editCopyDataPath: $('np-edit-copy-data-path'), editDataNote: $('np-edit-data-note'),
        createDataField: $('np-create-data-field'), dataFolder: $('np-data-folder'),
        dataFolderChoose: $('np-data-folder-choose'), dataFolderPreview: $('np-data-folder-preview'),
        dataFolderList: $('np-data-folder-list'), dataFolderEmpty: $('np-data-folder-empty'),
        dataFolderCount: $('np-data-folder-count'), dataFolderError: $('np-data-folder-error'),
        deleteOverlay: $('delete-project-overlay'), deleteName: $('delete-project-name'),
        deleteError: $('delete-project-error'), deleteCancel: $('delete-project-cancel'),
        deleteConfirm: $('delete-project-confirm'),
    };

    let pendingDeleteProject = null;
    let deleteLastFocused = null;

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

    // Every project row menu contains Edit and Delete actions.
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
        menu.appendChild(dangerMenuItem('Delete', () => openDeleteConfirmation(project)));

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
        // Title and Continue are plain navigations to the same canonical,
        // slug-based Project Home URL (server-generated) -- no POST/fetch
        // dance, no state-aware bypass. Project Home is the orientation
        // layer; it decides what to show from there.
        const homeUrl = p.home_url || ('/projects/' + encodeURIComponent(p.url_slug || p.id));
        const title = el('a', 'v2-project-title', esc(p.display_name || 'Untitled project'));
        title.href = homeUrl;
        head.appendChild(title);
        head.appendChild(buildMenu(p));  // Edit + Delete (every project is editable)
        row.appendChild(head);

        if (p.overview) row.appendChild(el('p', 'v2-project-overview', esc(p.overview)));

        const footer = el('div', 'v2-project-footer');
        const meta = el('div', 'v2-project-meta');
        meta.appendChild(el('p', 'v2-project-status v2-tone-' + esc(p.status_tone || 'neutral'),
            '<span class="v2-status-dot" aria-hidden="true"></span>' + esc(p.status_label || 'Not Started')));
        meta.appendChild(el('p', 'v2-project-activity', esc(metadataLine(p))));
        footer.appendChild(meta);

        const cont = el('a', 'v2-project-continue', 'Continue <span class="v2-arrow" aria-hidden="true">&rarr;</span>');
        cont.href = homeUrl;
        footer.appendChild(cont);

        row.appendChild(footer);
        return row;
    }

    function renderList(projects) {
        els.list.innerHTML = '';
        projects.forEach((p) => els.list.appendChild(renderRow(p)));
        els.loading.hidden = true; els.error.hidden = true;
        if (!projects.length) {
            // Zero projects = onboarding mode: hide the whole populated header
            // (Projects title + subtitle + New Project) and show the onboarding.
            els.empty.hidden = false; els.list.hidden = true;
            if (els.header) els.header.hidden = true;
        } else {
            // One or more projects = project-management mode: restore the header.
            els.empty.hidden = true; els.list.hidden = false;
            if (els.header) els.header.hidden = false;
        }
        if (pendingEditProjectId) {
            const project = projects.find((p) => p.id === pendingEditProjectId);
            pendingEditProjectId = null;
            if (project) {
                openEdit(project);
                window.history.replaceState({}, '', '/projects');
            }
        }
    }

    function showError(message) {
        els.loading.hidden = true; els.list.hidden = true; els.empty.hidden = true;
        if (els.header) els.header.hidden = true;
        els.errorDetail.textContent = message || 'Please try again in a moment.';
        els.error.hidden = false;
    }

    async function loadProjects() {
        els.loading.hidden = false; els.error.hidden = true; els.list.hidden = true; els.empty.hidden = true;
        if (els.header) els.header.hidden = true;  // revealed only once projects are known to exist
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

    // ---- modal (create + edit) ----------------------------------------------

    let existingFiles = [];       // persisted source files
    let lastFocused = null;
    let modalMode = 'create';
    let editingProjectId = null;
    let pendingEditProjectId = null;
    let dataScanTimer = null;
    let dataFolderToken = 0;      // discards stale folder-inspection responses
    const DATA_FOLDER_PREVIEW_LIMIT = 25;

    function humanSize(bytes) {
        if (bytes == null) return '';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let n = bytes, i = 0;
        while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
        const val = (i === 0) ? n : (n < 10 ? n.toFixed(1) : Math.round(n));
        return val + ' ' + units[i];
    }

    function fileIcon(name) {
        const ext = (name.split('.').pop() || '').toLowerCase();
        if (['csv', 'tsv', 'xlsx', 'xls', 'parquet'].includes(ext)) {
            return '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M3.5 9.5h17M3.5 14.5h17M9 4.5v15M15 4.5v15" stroke="currentColor" stroke-width="1.2"/></svg>';
        }
        if (['h5ad', 'h5', 'hdf5', 'npz', 'npy', 'rds', 'mtx', 'loom'].includes(ext)) {
            return '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><ellipse cx="12" cy="6.5" rx="7" ry="2.6" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M5 6.5v11c0 1.44 3.13 2.6 7 2.6s7-1.16 7-2.6v-11M5 12c0 1.44 3.13 2.6 7 2.6s7-1.16 7-2.6" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
        }
        return '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 3.5h7l5 5v12a0 0 0 0 1 0 0H6a0 0 0 0 1 0 0V3.5z" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M13 3.5V9h5M8.5 13h7M8.5 16.5h7" stroke="currentColor" stroke-width="1.3" fill="none"/></svg>';
    }

    function renderFileList() {
        els.fileList.innerHTML = '';
        const datasetFiles = existingFiles.filter((file) => !file.managed);
        els.editDataEmpty.hidden = datasetFiles.length > 0;
        existingFiles.forEach((file) => {
            const li = el('li', 'v2-file-row v2-file-row-existing' + (file.managed ? ' v2-file-row-managed' : ''));
            li.appendChild(el('span', 'v2-file-icon', fileIcon(file.name)));
            li.appendChild(el('span', 'v2-file-name', esc(file.name)));
            if (file.managed) li.appendChild(el('span', 'v2-file-managed', 'Project Context'));
            li.appendChild(el('span', 'v2-file-size', esc(humanSize(file.size))));
            els.fileList.appendChild(li);
        });
    }

    // ---- data folder chosen at creation --------------------------------------

    function setDataFolderError(message) {
        els.dataFolderError.textContent = message || '';
        els.dataFolderError.hidden = !message;
    }

    function renderDataFolderPreview(files) {
        if (files == null) {
            els.dataFolderPreview.hidden = true;
            els.dataFolderList.innerHTML = '';
            els.dataFolderCount.textContent = '';
            return;
        }
        els.dataFolderList.innerHTML = '';
        els.dataFolderPreview.hidden = false;
        els.dataFolderEmpty.hidden = files.length > 0;
        els.dataFolderCount.textContent = files.length
            ? files.length + (files.length === 1 ? ' file' : ' files')
            : '';
        files.slice(0, DATA_FOLDER_PREVIEW_LIMIT).forEach((file) => {
            const li = el('li', 'v2-file-row');
            li.appendChild(el('span', 'v2-file-icon', fileIcon(file.name)));
            li.appendChild(el('span', 'v2-file-name', esc(file.name)));
            li.appendChild(el('span', 'v2-file-size', esc(humanSize(file.size))));
            els.dataFolderList.appendChild(li);
        });
        if (files.length > DATA_FOLDER_PREVIEW_LIMIT) {
            const more = el('li', 'v2-file-row v2-file-row-managed');
            more.appendChild(el('span', 'v2-file-name',
                esc('and ' + (files.length - DATA_FOLDER_PREVIEW_LIMIT) + ' more')));
            els.dataFolderList.appendChild(more);
        }
    }

    // The server validates the path and lists it; the browser never reads disk.
    async function inspectDataFolder() {
        const raw = els.dataFolder.value.trim();
        dataFolderToken += 1;
        const token = dataFolderToken;
        if (!raw) {
            setDataFolderError(null);
            renderDataFolderPreview(null);
            return;
        }
        try {
            const resp = await fetch('/api/data-folder/inspect', {
                method: 'POST', credentials: 'include',
                headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: raw }),
            });
            const data = await readJson(resp);
            if (token !== dataFolderToken) return;
            if (!(resp.ok && data && data.success)) {
                renderDataFolderPreview(null);
                setDataFolderError((data && data.error) || 'That folder could not be used.');
                return;
            }
            setDataFolderError(null);
            renderDataFolderPreview(data.files || []);
        } catch (err) {
            if (token !== dataFolderToken) return;
            renderDataFolderPreview(null);
            setDataFolderError(err && err.message ? err.message : 'That folder could not be checked.');
        }
    }

    async function chooseDataFolder() {
        els.dataFolderChoose.disabled = true;
        try {
            const resp = await fetch('/api/data-folder/choose', {
                method: 'POST', credentials: 'include', headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                throw new Error((data && data.error) || 'The folder chooser could not be opened.');
            }
            if (data.cancelled) return;
            els.dataFolder.value = data.path || '';
            dataFolderToken += 1;
            setDataFolderError(null);
            renderDataFolderPreview(data.files || []);
        } catch (err) {
            setDataFolderError(err && err.message ? err.message : 'The folder chooser could not be opened.');
        } finally {
            els.dataFolderChoose.disabled = false;
        }
    }

    function resetDataFolderField() {
        dataFolderToken += 1;
        els.dataFolder.value = '';
        setDataFolderError(null);
        renderDataFolderPreview(null);
    }

    // ---- data folder of an existing project ----------------------------------

    function renderEditDataFolder(folder) {
        const path = folder && folder.path;
        els.editDataPathRow.hidden = !path;
        els.editDataPath.textContent = path || '';
        if (!path) return;
        els.editDataNote.textContent = folder.external
            ? 'This is your own folder. DiscoveryAgent reads it in place and never changes what is in it.'
            : 'Copy files into the folder above and they appear here automatically.';
    }

    async function copyEditDataPath() {
        const path = els.editDataPath.textContent || '';
        if (!path) return;
        const original = els.editCopyDataPath.textContent;
        try {
            await navigator.clipboard.writeText(path);
            els.editCopyDataPath.textContent = 'Copied';
        } catch (err) {
            els.editCopyDataPath.textContent = 'Press ⌘C';
            const range = document.createRange();
            range.selectNodeContents(els.editDataPath);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }
        setTimeout(() => { els.editCopyDataPath.textContent = original; }, 1600);
    }

    function activeDataProjectId() {
        return (modalMode === 'edit' && editingProjectId) ? editingProjectId : null;
    }

    function setActiveDataError(message) {
        els.editDataError.textContent = message || '';
        els.editDataError.hidden = !message;
    }

    async function rescanDataFolder() {
        const projectId = activeDataProjectId();
        if (!projectId || els.overlay.hidden) return;
        try {
            const resp = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/files', {
                credentials: 'include', headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                throw new Error((data && data.error) || 'Could not scan the data folder.');
            }
            existingFiles = data.files || [];
            renderFileList();
            setActiveDataError(null);
        } catch (err) {
            setActiveDataError(err && err.message ? err.message : 'Could not scan the data folder.');
        }
    }

    function stopDataPolling() {
        if (dataScanTimer) window.clearInterval(dataScanTimer);
        dataScanTimer = null;
    }

    function startDataPolling() {
        stopDataPolling();
        rescanDataFolder();
        dataScanTimer = window.setInterval(rescanDataFolder, 2000);
    }

    function applyMode() {
        els.projectState.hidden = false;
        els.projectFooter.hidden = false;
        const isEdit = modalMode === 'edit';
        // One page in both modes: creating chooses a data folder, editing shows
        // the folder the project already uses.
        els.editDataField.hidden = !isEdit;
        els.createDataField.hidden = isEdit;
        els.title.textContent = isEdit
            ? 'Edit DiscoveryAgent project'
            : 'Create a new DiscoveryAgent project';
        els.submit.innerHTML = isEdit
            ? 'Save Changes'
            : 'Create project <span class="v2-arrow" aria-hidden="true">&rarr;</span>';
    }

    function resetSubmitState() {
        els.submit.disabled = false;
        els.submit.classList.remove('is-busy');
        applyMode();
    }

    function openCreate() {
        modalMode = 'create'; editingProjectId = null;
        stopDataPolling();
        existingFiles = [];
        els.form.reset();
        resetDataFolderField();
        resetSubmitState();
        renderFileList();
        setFormError(null);
        showModal();
    }

    async function openEdit(project) {
        modalMode = 'edit'; editingProjectId = project.id;
        stopDataPolling();
        existingFiles = [];
        els.form.reset();
        resetDataFolderField();
        resetSubmitState();
        renderEditDataFolder(null);
        setFormError(null);
        setActiveDataError(null);
        // Prefill from the row immediately, then hydrate full detail and files.
        els.name.value = project.display_name || '';
        els.objective.value = '';
        els.context.value = '';
        renderFileList();
        showModal();
        try {
            const resp = await fetch('/api/projects/' + encodeURIComponent(project.id), {
                credentials: 'include', headers: { 'Accept': 'application/json' },
            });
            const data = await resp.json();
            if (resp.ok && data && data.success && data.project && editingProjectId === project.id) {
                els.name.value = data.project.display_name || '';
                els.objective.value = data.project.task_description || '';
                els.context.value = data.project.dataset_context || '';
                existingFiles = data.project.files || [];
                renderFileList();
                renderEditDataFolder(data.project.data_folder);
            }
        } catch (err) { /* keep the row prefill on failure */ }
        if (editingProjectId === project.id && !els.overlay.hidden) startDataPolling();
    }

    function openDeleteConfirmation(project) {
        if (!project || !project.id) return;
        pendingDeleteProject = project;
        deleteLastFocused = document.activeElement;
        els.deleteName.textContent = project.display_name || 'this project';
        els.deleteError.textContent = '';
        els.deleteError.hidden = true;
        els.deleteOverlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        setTimeout(() => els.deleteCancel.focus(), 30);
    }

    function closeDeleteConfirmation() {
        if (els.deleteConfirm.disabled) return;
        els.deleteOverlay.hidden = true;
        document.body.classList.remove('v2-modal-open');
        pendingDeleteProject = null;
        if (deleteLastFocused && deleteLastFocused.focus) deleteLastFocused.focus();
    }

    async function confirmDeleteProject() {
        const project = pendingDeleteProject;
        if (!project || !project.id) return;
        els.deleteConfirm.disabled = true;
        els.deleteConfirm.classList.add('is-busy');
        els.deleteConfirm.textContent = 'Moving to Trash…';
        els.deleteCancel.disabled = true;
        try {
            const resp = await fetch('/api/projects/' + encodeURIComponent(project.id), {
                method: 'DELETE',
                credentials: 'include',
                headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                els.deleteError.textContent = (data && data.error) || 'Could not move this project to Trash.';
                els.deleteError.hidden = false;
                return;
            }
            els.deleteOverlay.hidden = true;
            document.body.classList.remove('v2-modal-open');
            pendingDeleteProject = null;
            loadProjects();
        } catch (err) {
            els.deleteError.textContent = err && err.message ? err.message : 'Network error while moving this project to Trash.';
            els.deleteError.hidden = false;
        } finally {
            els.deleteConfirm.disabled = false;
            els.deleteConfirm.classList.remove('is-busy');
            els.deleteConfirm.textContent = 'Delete';
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
        lastFocused = document.activeElement;
        resetProjectModalScroll();
        els.overlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        resetProjectModalScroll();
        setTimeout(focusProjectNameAtTop, 30);
    }

    function closeModal() {
        if (els.submit.classList.contains('is-busy')) return;
        stopDataPolling();
        els.overlay.hidden = true;
        resetProjectModalScroll();
        document.body.classList.remove('v2-modal-open');
        if (lastFocused && lastFocused.focus) lastFocused.focus();
    }

    function setFormError(msg) {
        if (!msg) { els.formError.hidden = true; els.formError.textContent = ''; return; }
        els.formError.textContent = msg;
        els.formError.hidden = false;
    }

    async function readJson(resp) {
        try { return await resp.json(); } catch (err) { return null; }
    }

    async function openDataFolder(event) {
        const projectId = activeDataProjectId();
        if (!projectId) return;
        const button = event.currentTarget;
        button.disabled = true;
        setActiveDataError(null);
        try {
            const resp = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/open-data-folder', {
                method: 'POST', credentials: 'include', headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                throw new Error((data && data.error) || 'Could not open the data folder.');
            }
        } catch (err) {
            setActiveDataError(err && err.message ? err.message : 'Could not open the data folder.');
        } finally {
            button.disabled = false;
        }
    }

    function clearInvalid(el) {
        if (el) el.classList.remove('v2-input-invalid');
    }

    async function submitForm(e) {
        e.preventDefault();
        const name = els.name.value.trim();
        const objective = els.objective.value.trim();
        const context = els.context.value.trim();

        // All three fields are required before DiscoveryAgent can generate
        // hypotheses and retain the dataset's persistent context.
        const required = [
            { el: els.name, value: name, label: 'Project name' },
            { el: els.objective, value: objective, label: 'Research objective' },
            { el: els.context, value: context, label: 'Project context' },
        ];
        required.forEach((f) => clearInvalid(f.el));
        const missing = required.filter((f) => !f.value);
        if (missing.length) {
            missing.forEach((f) => f.el.classList.add('v2-input-invalid'));
            setFormError('Please complete the required fields: ' +
                missing.map((f) => f.label).join(', ') + '.');
            missing[0].el.focus();
            return;
        }

        setFormError(null);
        els.submit.disabled = true;
        els.submit.classList.add('is-busy');
        const original = els.submit.innerHTML;
        els.submit.textContent = modalMode === 'create' ? 'Creating…' : 'Saving…';

        try {
            // Project metadata is URL-encoded in both modes. Scientific files
            // enter through the OS data folder, never through browser multipart.
            const body = new URLSearchParams();
            body.append('project_name', name);
            body.append('task_description', objective);
            body.append('dataset_context', context);
            if (modalMode === 'create') {
                // Blank means "DiscoveryAgent manages one"; the server decides.
                body.append('data_folder', els.dataFolder.value.trim());
            }
            const endpoint = modalMode === 'edit'
                ? '/api/projects/' + encodeURIComponent(editingProjectId) + '/edit'
                : '/api/projects';
            const resp = await fetch(endpoint, { method: 'POST', credentials: 'include', body: body });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                setFormError((data && data.error) ||
                    (modalMode === 'edit' ? 'Could not save changes.' : 'Could not create the project.'));
            } else {
                if (modalMode === 'edit') {
                    resetSubmitState();
                    closeModal();
                    loadProjects();
                    return;
                }
                // Creation already activated the project: go straight to Project
                // Home, which shows the data folder's exact path.
                stopDataPolling();
                window.location.href = data.destination;
                return;
            }
        } catch (err) {
            setFormError(err && err.message ? err.message : (modalMode === 'edit'
                ? 'Could not save changes. Please try again.'
                : 'Could not create the project. Please try again.'));
        }
        els.submit.disabled = false;
        els.submit.classList.remove('is-busy');
        els.submit.innerHTML = original;
    }

    // ---- wiring --------------------------------------------------------------

    function wire() {
        pendingEditProjectId = new URLSearchParams(window.location.search).get('edit');
        if (els.openBtn) els.openBtn.addEventListener('click', openCreate);
        if (els.openBtnEmpty) els.openBtnEmpty.addEventListener('click', openCreate);
        if (els.retry) els.retry.addEventListener('click', loadProjects);
        els.close.addEventListener('click', closeModal);
        els.cancel.addEventListener('click', closeModal);
        els.overlay.addEventListener('mousedown', (e) => { if (e.target === els.overlay) closeModal(); });
        els.deleteCancel.addEventListener('click', closeDeleteConfirmation);
        els.deleteConfirm.addEventListener('click', confirmDeleteProject);
        els.deleteOverlay.addEventListener('mousedown', (e) => {
            if (e.target === els.deleteOverlay) closeDeleteConfirmation();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (!els.deleteOverlay.hidden) { closeDeleteConfirmation(); return; }
            if (!els.overlay.hidden) { closeModal(); return; }
            closeAllMenus();
        });
        document.addEventListener('click', () => closeAllMenus());
        els.form.addEventListener('submit', submitForm);
        els.editOpenDataFolder.addEventListener('click', openDataFolder);
        els.editCopyDataPath.addEventListener('click', copyEditDataPath);
        els.dataFolderChoose.addEventListener('click', chooseDataFolder);
        let inspectTimer = null;
        els.dataFolder.addEventListener('input', () => {
            if (inspectTimer) window.clearTimeout(inspectTimer);
            inspectTimer = window.setTimeout(inspectDataFolder, 400);
        });
        els.dataFolder.addEventListener('change', inspectDataFolder);
        [els.name, els.objective, els.context].forEach((el) => {
            if (el) el.addEventListener('input', () => clearInvalid(el));
        });

    }

    document.addEventListener('DOMContentLoaded', () => { wire(); loadProjects(); });
})();
