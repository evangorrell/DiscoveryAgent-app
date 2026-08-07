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
        overlay: $('np-overlay'), modal: $('np-modal'), close: $('np-close'),
        cancel: $('np-cancel'), form: $('np-form'), name: $('np-name'), objective: $('np-objective'), context: $('np-context'),
        dropzone: $('np-dropzone'), browse: $('np-browse'),
        fileInput: $('np-files'),
        fileList: $('np-file-list'), formError: $('np-form-error'), submit: $('np-submit'),
        eyebrow: $('np-eyebrow'), title: $('np-title'),
    };

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
        menu.appendChild(dangerMenuItem('Delete', () => deleteProject(project)));

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
        head.appendChild(buildMenu(p));  // Edit + Settings (every project is editable)
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

    let selectedFiles = [];       // newly added, removable
    let existingFiles = [];       // persisted source files
    let lastFocused = null;
    let modalMode = 'create';
    let editingProjectId = null;
    let pendingEditProjectId = null;

    function humanSize(bytes) {
        if (bytes == null) return '';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let n = bytes, i = 0;
        while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
        const val = (i === 0) ? n : (n < 10 ? n.toFixed(1) : Math.round(n));
        return val + ' ' + units[i];
    }

    function uploadPath(file) {
        return (file && file.__v2RelativePath)
            || (file && file.webkitRelativePath)
            || (file && file.name)
            || '';
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
        existingFiles.forEach((file, index) => {
            const li = el('li', 'v2-file-row v2-file-row-existing');
            li.appendChild(el('span', 'v2-file-icon', fileIcon(file.name)));
            li.appendChild(el('span', 'v2-file-name', esc(file.name)));
            li.appendChild(el('span', 'v2-file-size', esc(humanSize(file.size))));
            const rm = el('button', 'v2-file-remove', '&times;');
            rm.type = 'button';
            rm.setAttribute('aria-label', 'Remove ' + file.name);
            rm.addEventListener('click', () => removeExistingFile(file, index, rm));
            li.appendChild(rm);
            els.fileList.appendChild(li);
        });
        // Newly added files: removable before save.
        selectedFiles.forEach((file, index) => {
            const displayName = uploadPath(file) || file.name;
            const li = el('li', 'v2-file-row');
            li.appendChild(el('span', 'v2-file-icon', fileIcon(file.name)));
            li.appendChild(el('span', 'v2-file-name', esc(displayName)));
            li.appendChild(el('span', 'v2-file-size', esc(humanSize(file.size))));
            const rm = el('button', 'v2-file-remove', '&times;');
            rm.type = 'button';
            rm.setAttribute('aria-label', 'Remove ' + displayName);
            rm.addEventListener('click', () => { selectedFiles.splice(index, 1); renderFileList(); });
            li.appendChild(rm);
            els.fileList.appendChild(li);
        });
    }

    async function removeExistingFile(file, index, button) {
        if (!editingProjectId || !file || !file.name) return;
        button.disabled = true;
        setFormError(null);
        try {
            const endpoint = '/api/projects/' + encodeURIComponent(editingProjectId)
                + '/files?path=' + encodeURIComponent(file.name);
            const resp = await fetch(endpoint, {
                method: 'DELETE',
                credentials: 'include',
                headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                setFormError((data && data.error) || ('Could not remove ' + file.name + '.'));
                button.disabled = false;
                return;
            }
            existingFiles.splice(index, 1);
            renderFileList();
        } catch (err) {
            setFormError(err && err.message ? err.message : ('Could not remove ' + file.name + '.'));
            button.disabled = false;
        }
    }

    function addFiles(fileList) {
        Array.from(fileList || []).forEach((f) => {
            const rel = uploadPath(f);
            const duplicate = selectedFiles.some((s) =>
                uploadPath(s) === rel && s.size === f.size && s.lastModified === f.lastModified);
            if (!duplicate) selectedFiles.push(f);
        });
        renderFileList();
    }

    function readDirectoryEntries(directoryEntry) {
        const reader = directoryEntry.createReader();
        const entries = [];
        return new Promise((resolve, reject) => {
            function readBatch() {
                reader.readEntries((batch) => {
                    if (!batch.length) { resolve(entries); return; }
                    entries.push(...batch);
                    readBatch();
                }, reject);
            }
            readBatch();
        });
    }

    async function filesFromEntry(entry, parentPath) {
        const relativePath = parentPath ? parentPath + '/' + entry.name : entry.name;
        if (entry.isFile) {
            const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
            Object.defineProperty(file, '__v2RelativePath', {
                value: relativePath, configurable: true,
            });
            return [file];
        }
        if (!entry.isDirectory) return [];
        const children = await readDirectoryEntries(entry);
        const nested = await Promise.all(children.map((child) => filesFromEntry(child, relativePath)));
        return nested.flat();
    }

    async function addDroppedItems(dataTransfer) {
        const items = Array.from((dataTransfer && dataTransfer.items) || []);
        const entries = items
            .map((item) => item.webkitGetAsEntry ? item.webkitGetAsEntry() : null)
            .filter(Boolean);
        if (!entries.length) {
            addFiles(dataTransfer && dataTransfer.files);
            return;
        }
        try {
            const groups = await Promise.all(entries.map((entry) => filesFromEntry(entry, '')));
            const files = groups.flat().sort((a, b) => uploadPath(a).localeCompare(uploadPath(b)));
            addFiles(files);
        } catch (err) {
            setFormError('Could not read the dropped folder. Please use “Choose a folder” instead.');
        }
    }

    function applyMode() {
        if (modalMode === 'edit') {
            els.eyebrow.textContent = 'EDIT PROJECT';
            els.title.textContent = 'Edit DiscoveryAgent project';
            els.submit.innerHTML = 'Save Changes';
        } else {
            els.eyebrow.textContent = 'NEW PROJECT';
            els.title.textContent = 'Create a new DiscoveryAgent project';
            els.submit.innerHTML = 'Create Project <span class="v2-arrow" aria-hidden="true">&rarr;</span>';
        }
    }

    function openCreate() {
        modalMode = 'create'; editingProjectId = null;
        selectedFiles = []; existingFiles = [];
        els.form.reset();
        applyMode();
        renderFileList();
        setFormError(null);
        showModal();
    }

    async function openEdit(project) {
        modalMode = 'edit'; editingProjectId = project.id;
        selectedFiles = []; existingFiles = [];
        els.form.reset();
        applyMode();
        setFormError(null);
        // Prefill from the row immediately, then hydrate full detail (context + files).
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
            }
        } catch (err) { /* keep the row prefill on failure */ }
    }

    async function deleteProject(project) {
        if (!project || !project.id) return;
        const name = project.display_name || 'this project';
        const confirmed = window.confirm(
            'Delete "' + name + '"?\n\nThis removes the project and its local DiscoveryAgent files. This cannot be undone.'
        );
        if (!confirmed) return;
        try {
            const resp = await fetch('/api/projects/' + encodeURIComponent(project.id), {
                method: 'DELETE',
                credentials: 'include',
                headers: { 'Accept': 'application/json' },
            });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                showError((data && data.error) || 'Could not delete this project.');
                return;
            }
            loadProjects();
        } catch (err) {
            showError(err && err.message ? err.message : 'Network error while deleting this project.');
        }
    }

    function showModal() {
        lastFocused = document.activeElement;
        els.overlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        setTimeout(() => els.name.focus(), 30);
    }

    function closeModal() {
        els.overlay.hidden = true;
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

    async function uploadSelectedFiles(projectId) {
        const total = selectedFiles.length;
        let nextIndex = 0;
        let completed = 0;
        let firstError = null;
        const concurrency = Math.min(4, total);

        async function uploadWorker() {
            while (nextIndex < total && !firstError) {
                const index = nextIndex++;
                const file = selectedFiles[index];
                const displayName = uploadPath(file) || file.name;
                const fd = new FormData();
                fd.append('relative_path', displayName);
                fd.append('file', file, file.name);
                try {
                    const resp = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/files', {
                        method: 'POST', credentials: 'include', body: fd,
                    });
                    const data = await readJson(resp);
                    if (!(resp.ok && data && data.success)) {
                        throw new Error((data && data.error) || ('Could not upload ' + displayName + '.'));
                    }
                    completed++;
                    els.submit.textContent = 'Uploading ' + completed + ' of ' + total + '…';
                } catch (err) {
                    firstError = err;
                }
            }
        }

        els.submit.textContent = 'Uploading 0 of ' + total + '…';
        await Promise.all(Array.from({ length: concurrency }, () => uploadWorker()));
        if (firstError) throw firstError;
    }

    function clearInvalid(el) {
        if (el) el.classList.remove('v2-input-invalid');
    }

    async function submitForm(e) {
        e.preventDefault();
        const name = els.name.value.trim();
        const objective = els.objective.value.trim();
        const context = els.context.value.trim();

        // Project name, research objective, and project context are required —
        // every project must carry the information DiscoveryAgent needs before it
        // can generate hypotheses (there is no separate "provide it later" gate).
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
        els.submit.textContent = modalMode === 'edit' ? 'Saving…' : 'Creating…';

        try {
            const fd = new FormData();
            fd.append('project_name', name);
            fd.append('task_description', objective);
            fd.append('dataset_context', context);
            const endpoint = modalMode === 'edit'
                ? '/api/projects/' + encodeURIComponent(editingProjectId) + '/edit'
                : '/api/projects';
            const resp = await fetch(endpoint, { method: 'POST', credentials: 'include', body: fd });
            const data = await readJson(resp);
            if (!(resp.ok && data && data.success)) {
                setFormError((data && data.error) ||
                    (modalMode === 'edit' ? 'Could not save changes.' : 'Could not create the project.'));
            } else {
                const projectId = data.project_id || editingProjectId;
                if (selectedFiles.length) await uploadSelectedFiles(projectId);
                if (modalMode === 'edit') { closeModal(); loadProjects(); return; }
                if (data.destination) { window.location.href = data.destination; return; }
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
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (!els.overlay.hidden) { closeModal(); return; }
            closeAllMenus();
        });
        document.addEventListener('click', () => closeAllMenus());
        els.form.addEventListener('submit', submitForm);
        [els.name, els.objective, els.context].forEach((el) => {
            if (el) el.addEventListener('input', () => clearInvalid(el));
        });

        els.browse.addEventListener('click', () => els.fileInput.click());
        els.dropzone.addEventListener('click', (e) => {
            if (e.target === els.browse) return;
            els.fileInput.click();
        });
        els.dropzone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); els.fileInput.click(); } });
        els.fileInput.addEventListener('change', () => { addFiles(els.fileInput.files); els.fileInput.value = ''; });

        ['dragenter', 'dragover'].forEach((evt) => els.dropzone.addEventListener(evt, (e) => {
            e.preventDefault(); e.stopPropagation(); els.dropzone.classList.add('is-dragover');
        }));
        ['dragleave', 'dragend'].forEach((evt) => els.dropzone.addEventListener(evt, (e) => {
            e.preventDefault(); e.stopPropagation(); els.dropzone.classList.remove('is-dragover');
        }));
        els.dropzone.addEventListener('drop', async (e) => {
            e.preventDefault(); e.stopPropagation(); els.dropzone.classList.remove('is-dragover');
            await addDroppedItems(e.dataTransfer);
        });
    }

    document.addEventListener('DOMContentLoaded', () => { wire(); loadProjects(); });
})();
