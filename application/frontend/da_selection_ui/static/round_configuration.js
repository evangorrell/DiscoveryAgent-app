(function () {
    'use strict';

    var config = window.roundWorkspaceConfig || {};
    var trigger = document.getElementById('rw-project-configuration');
    var overlay = document.getElementById('rw-project-configuration-overlay');
    var close = document.getElementById('rw-project-configuration-close');
    var loading = document.getElementById('rw-project-configuration-loading');
    var error = document.getElementById('rw-project-configuration-error');
    var content = document.getElementById('rw-project-configuration-content');
    var objective = document.getElementById('rw-project-configuration-objective');
    var settings = document.getElementById('rw-project-configuration-settings');
    var dataList = document.getElementById('rw-project-configuration-data');
    var readme = document.getElementById('rw-project-configuration-readme');

    if (!trigger || !overlay) return;

    async function jsonResponse(response, fallback) {
        var payload = await response.json();
        if (!response.ok || payload.success === false) {
            throw new Error(payload.error || payload.message || fallback);
        }
        return payload;
    }

    function setting(label, value) {
        var term = document.createElement('dt');
        var description = document.createElement('dd');
        term.textContent = label;
        description.textContent = value;
        settings.append(term, description);
    }

    function enabled(value) {
        return value === true ? 'Enabled' : 'Disabled';
    }

    function renderDataSources(project) {
        dataList.replaceChildren();
        var sources = project.data_sources && Array.isArray(project.data_sources.sources)
            ? project.data_sources.sources : [];
        if (!sources.length) {
            var empty = document.createElement('li');
            empty.textContent = 'No shared data sources selected.';
            dataList.appendChild(empty);
            return;
        }
        sources.forEach(function (source) {
            var selected = Array.isArray(source.selected_paths) && source.selected_paths.length
                ? source.selected_paths : ['.'];
            var item = document.createElement('li');
            var name = document.createElement('strong');
            var paths = document.createElement('span');
            name.textContent = source.display_name || 'Missing data source';
            paths.textContent = selected.map(function (path) {
                return path === '.' ? 'Entire source' : path;
            }).join(', ');
            item.append(name, paths);
            dataList.appendChild(item);
        });
    }

    function renderProject(project, readmePayload) {
        objective.textContent = project.task_description || 'No research objective saved.';
        document.getElementById('rw-project-configuration-context').textContent =
            project.dataset_context || 'No project context saved.';
        settings.replaceChildren();
        setting('Reasoning model', project.reasoning_model || 'Unavailable');
        setting('Execution model', project.execution_model || 'Unavailable');
        setting('Queries for Round 1+', String(project.query_count));
        setting('Deep Research', enabled(project.deep_research));
        setting('Web Search', enabled(project.web_search));
        renderDataSources(project);
        readme.textContent = readmePayload && readmePayload.success
            ? readmePayload.content
            : 'README unavailable: ' + ((readmePayload && readmePayload.error) || 'The file could not be read.');
    }

    async function loadConfiguration() {
        loading.hidden = false;
        error.hidden = true;
        content.hidden = true;
        try {
            var results = await Promise.all([
                fetch('/api/projects/' + encodeURIComponent(config.projectId) + '?selection_only=1', {
                    credentials: 'include', headers: { 'Accept': 'application/json' },
                }).then(function (response) {
                    return jsonResponse(response, 'Project configuration could not be loaded.');
                }),
                fetch('/api/readme', {
                    credentials: 'include', headers: { 'Accept': 'application/json' },
                }).then(function (response) {
                    return response.json().catch(function () { return { success: false, error: 'The file could not be read.' }; });
                }),
            ]);
            renderProject(results[0].project, results[1]);
            content.hidden = false;
        } catch (loadError) {
            error.textContent = loadError && loadError.message
                ? loadError.message : 'Project configuration could not be loaded.';
            error.hidden = false;
        } finally {
            loading.hidden = true;
        }
    }

    function openConfiguration() {
        overlay.hidden = false;
        document.body.classList.add('v2-modal-open');
        close.focus();
        void loadConfiguration();
    }

    function closeConfiguration() {
        overlay.hidden = true;
        document.body.classList.remove('v2-modal-open');
        trigger.focus();
    }

    trigger.addEventListener('click', openConfiguration);
    close.addEventListener('click', closeConfiguration);
    overlay.addEventListener('mousedown', function (event) {
        if (event.target === overlay) closeConfiguration();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !overlay.hidden) closeConfiguration();
        // Include the scrollable details for keyboard scrolling.
        if (event.key === 'Tab' && !overlay.hidden) {
            var targets = overlay.querySelectorAll('button, [tabindex="0"]');
            var first = targets[0], last = targets[targets.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault(); last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault(); first.focus();
            }
        }
    });
})();
