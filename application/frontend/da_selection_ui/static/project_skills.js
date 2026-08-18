(function () {
    'use strict';

    const config = window.projectSkillsConfig || {};
    const projectId = config.projectId;
    const elements = {
        open: document.getElementById('skills-import-open'),
        loading: document.getElementById('skills-loading'),
        error: document.getElementById('skills-error'),
        errorDetail: document.getElementById('skills-error-detail'),
        retry: document.getElementById('skills-retry'),
        empty: document.getElementById('skills-empty'),
        list: document.getElementById('skills-list'),
        runningNote: document.getElementById('skills-running-note'),
        overlay: document.getElementById('skill-import-overlay'),
        close: document.getElementById('skill-import-close'),
        cancel: document.getElementById('skill-import-cancel'),
        dropzone: document.getElementById('skill-import-dropzone'),
        file: document.getElementById('skill-import-file'),
        preview: document.getElementById('skill-import-preview'),
        previewName: document.getElementById('skill-preview-name'),
        previewDescription: document.getElementById('skill-preview-description'),
        previewFilename: document.getElementById('skill-preview-filename'),
        importError: document.getElementById('skill-import-error'),
        confirm: document.getElementById('skill-import-confirm'),
    };
    let running = false;
    let statusResolved = false;
    let selectedFile = null;
    let previewSequence = 0;
    let dragDepth = 0;

    class ApiError extends Error {
        constructor(message, code, status) {
            super(message);
            this.name = 'ApiError';
            this.code = code || null;
            this.status = status;
        }
    }

    async function readJson(response) {
        let payload = null;
        try { payload = await response.json(); } catch (_error) { /* handled below */ }
        if (!response.ok || !payload || payload.success === false) {
            throw new ApiError(
                (payload && (payload.error || payload.message)) || 'The request could not be completed.',
                payload && payload.code,
                response.status
            );
        }
        return payload;
    }

    function updateMutationControls() {
        const disabled = !statusResolved || running;
        elements.runningNote.hidden = !running;
        elements.open.disabled = disabled;
        elements.list.querySelectorAll('[data-remove-skill]').forEach((button) => { button.disabled = disabled; });
    }

    function setMutationState(isRunning, resolved) {
        running = Boolean(isRunning);
        statusResolved = Boolean(resolved);
        updateMutationControls();
    }

    function makeSkillRow(skill) {
        const row = document.createElement('article');
        row.className = 'v2-skill-row';
        const copy = document.createElement('div');
        copy.className = 'v2-skill-row-copy';
        const title = document.createElement('h2');
        title.textContent = skill.name;
        const description = document.createElement('p');
        description.textContent = skill.description;
        const remove = document.createElement('button');
        remove.className = 'v2-skill-remove';
        remove.type = 'button';
        remove.textContent = 'Remove';
        remove.dataset.removeSkill = skill.id;
        remove.disabled = !statusResolved || running;
        copy.append(title, description);
        row.append(copy, remove);
        return row;
    }

    function renderSkills(skills) {
        elements.list.replaceChildren();
        skills.forEach((skill) => elements.list.append(makeSkillRow(skill)));
        elements.empty.hidden = skills.length !== 0;
        elements.list.hidden = skills.length === 0;
    }

    async function loadSkills() {
        setMutationState(running, false);
        elements.loading.hidden = false;
        elements.error.hidden = true;
        elements.empty.hidden = true;
        elements.list.hidden = true;
        try {
            const response = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/skills', {
                credentials: 'include', headers: { Accept: 'application/json' },
            });
            const payload = await readJson(response);
            setMutationState(payload.running, true);
            renderSkills(payload.skills || []);
        } catch (error) {
            elements.errorDetail.textContent = error.message;
            elements.error.hidden = false;
        } finally {
            elements.loading.hidden = true;
        }
    }

    function setImportError(message) {
        elements.importError.textContent = message || '';
        elements.importError.hidden = !message;
    }

    function resetImport() {
        selectedFile = null;
        previewSequence += 1;
        elements.file.value = '';
        elements.preview.hidden = true;
        elements.confirm.disabled = true;
        elements.confirm.classList.remove('is-busy');
        elements.confirm.textContent = 'Import Skill';
        dragDepth = 0;
        elements.dropzone.classList.remove('is-dragover');
        setImportError(null);
    }

    function openImport() {
        if (!statusResolved || running) return;
        resetImport();
        elements.overlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        elements.dropzone.focus();
    }

    function closeImport() {
        elements.overlay.hidden = true;
        document.body.classList.remove('v2-modal-open');
        resetImport();
        elements.open.focus();
    }

    async function reconcileRunningState(error) {
        if (!error || error.code !== 'round_running') return false;
        if (!elements.overlay.hidden) closeImport();
        setMutationState(true, true);
        elements.errorDetail.textContent = 'Skills cannot be changed while a research round is running.';
        elements.error.hidden = false;
        await loadSkills();
        return true;
    }

    async function previewFile(file) {
        const sequence = ++previewSequence;
        selectedFile = null;
        elements.preview.hidden = true;
        elements.confirm.disabled = true;
        setImportError(null);
        if (!file) return;
        if (!file.name.toLowerCase().endsWith('.zip')) {
            setImportError('Choose a .zip skill package.');
            return;
        }
        const body = new FormData();
        body.append('skill', file, file.name);
        try {
            const response = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/skills/preview', {
                method: 'POST', credentials: 'include', body,
            });
            const payload = await readJson(response);
            if (sequence !== previewSequence) return;
            selectedFile = file;
            elements.previewName.textContent = payload.skill.name;
            elements.previewDescription.textContent = payload.skill.description;
            elements.previewFilename.textContent = payload.filename;
            elements.preview.hidden = false;
            elements.confirm.disabled = false;
        } catch (error) {
            if (sequence !== previewSequence) return;
            if (await reconcileRunningState(error)) return;
            setImportError(error.message);
        }
    }

    function previewSelectedFiles(fileList) {
        const files = Array.from(fileList || []);
        elements.file.value = '';
        if (files.length !== 1) {
            selectedFile = null;
            previewSequence += 1;
            elements.preview.hidden = true;
            elements.confirm.disabled = true;
            setImportError('Choose exactly one .zip skill package.');
            return;
        }
        void previewFile(files[0]);
    }

    function beginFileDrag(event) {
        event.preventDefault();
        event.stopPropagation();
        dragDepth += 1;
        elements.dropzone.classList.add('is-dragover');
    }

    function continueFileDrag(event) {
        event.preventDefault();
        event.stopPropagation();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        elements.dropzone.classList.add('is-dragover');
    }

    function endFileDrag(event) {
        event.preventDefault();
        event.stopPropagation();
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) elements.dropzone.classList.remove('is-dragover');
    }

    function dropFiles(event) {
        event.preventDefault();
        event.stopPropagation();
        dragDepth = 0;
        elements.dropzone.classList.remove('is-dragover');
        previewSelectedFiles(event.dataTransfer && event.dataTransfer.files);
    }

    async function importSkill() {
        if (!selectedFile || running) return;
        elements.confirm.disabled = true;
        elements.confirm.classList.add('is-busy');
        elements.confirm.textContent = 'Importing…';
        setImportError(null);
        const body = new FormData();
        body.append('skill', selectedFile, selectedFile.name);
        try {
            const response = await fetch('/api/projects/' + encodeURIComponent(projectId) + '/skills', {
                method: 'POST', credentials: 'include', body,
            });
            await readJson(response);
            closeImport();
            await loadSkills();
        } catch (error) {
            if (await reconcileRunningState(error)) return;
            setImportError(error.message);
            elements.confirm.disabled = false;
            elements.confirm.classList.remove('is-busy');
            elements.confirm.textContent = 'Import Skill';
        }
    }

    async function removeSkill(skillId, skillName) {
        if (running) return;
        const confirmed = await window.V2Dialog.confirm({
            title: 'Remove skill?',
            message: skillName + ' will no longer be available to the execution agent for this project.',
            confirmLabel: 'Remove', cancelLabel: 'Cancel', danger: true,
        });
        if (!confirmed) return;
        try {
            const response = await fetch(
                '/api/projects/' + encodeURIComponent(projectId) + '/skills/' + encodeURIComponent(skillId),
                { method: 'DELETE', credentials: 'include', headers: { Accept: 'application/json' } }
            );
            await readJson(response);
            await loadSkills();
        } catch (error) {
            if (await reconcileRunningState(error)) return;
            elements.errorDetail.textContent = error.message;
            elements.error.hidden = false;
        }
    }

    elements.open.addEventListener('click', openImport);
    elements.close.addEventListener('click', closeImport);
    elements.cancel.addEventListener('click', closeImport);
    elements.retry.addEventListener('click', loadSkills);
    elements.dropzone.addEventListener('click', (event) => {
        if (event.target !== elements.file) elements.file.click();
    });
    elements.dropzone.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        elements.file.click();
    });
    elements.file.addEventListener('change', () => previewSelectedFiles(elements.file.files));
    elements.dropzone.addEventListener('dragenter', beginFileDrag);
    elements.dropzone.addEventListener('dragover', continueFileDrag);
    elements.dropzone.addEventListener('dragleave', endFileDrag);
    elements.dropzone.addEventListener('dragend', endFileDrag);
    elements.dropzone.addEventListener('drop', dropFiles);
    elements.overlay.addEventListener('dragover', (event) => {
        if (!elements.overlay.hidden) event.preventDefault();
    });
    elements.overlay.addEventListener('drop', (event) => {
        if (!elements.overlay.hidden) event.preventDefault();
    });
    elements.confirm.addEventListener('click', importSkill);
    elements.overlay.addEventListener('click', (event) => { if (event.target === elements.overlay) closeImport(); });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !elements.overlay.hidden) closeImport();
    });
    elements.list.addEventListener('click', (event) => {
        const button = event.target.closest('[data-remove-skill]');
        if (!button) return;
        const row = button.closest('.v2-skill-row');
        const name = row && row.querySelector('h2') ? row.querySelector('h2').textContent : 'This skill';
        void removeSkill(button.dataset.removeSkill, name);
    });

    void loadSkills();
})();
