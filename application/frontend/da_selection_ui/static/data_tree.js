(function () {
    'use strict';

    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text != null) element.textContent = text;
        return element;
    }

    function glyph(kind) {
        const value = node('span', 'v2-data-tree-glyph v2-data-tree-glyph-' + kind);
        value.setAttribute('aria-hidden', 'true');
        if (kind === 'folder') {
            value.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M1.7 3.4h4l1.2 1.4h7.4v7.8H1.7z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';
        } else if (kind === 'file') {
            value.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M3.2 1.8h6.1l3.5 3.5v8.9H3.2zM9.3 1.8v3.5h3.5" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';
        } else {
            value.innerHTML = '<svg viewBox="0 0 12 12" width="10" height="10"><path d="M4 2.5 7.5 6 4 9.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        }
        return value;
    }

    function formatSize(sizeBytes) {
        if (sizeBytes == null) return "--";
        const value = Number(sizeBytes);
        if (!Number.isFinite(value) || value < 0) return "--";
        const bytes = Math.floor(value);
        if (bytes < 1024) return bytes + " " + (bytes === 1 ? "byte" : "bytes");
        const units = ["KB", "MB", "GB", "TB"];
        let scaled = bytes;
        let unit = -1;
        do { scaled /= 1024; unit += 1; } while (scaled >= 1024 && unit < units.length - 1);
        const rounded = scaled.toFixed(1);
        return (rounded.endsWith(".0") ? rounded.slice(0, -2) : rounded) + " " + units[unit];
    }

    function sizeCell(sizeBytes) {
        const label = formatSize(sizeBytes);
        const value = node("span", "v2-data-tree-size", label);
        value.setAttribute("aria-hidden", "true");
        return { label, value };
    }

    function labelItemSize(item, name, sizeLabel) {
        item.setAttribute("aria-label", name + ". Size " + sizeLabel + ".");
    }

    function setDisclosure(button, item, opening, name) {
        button.setAttribute('aria-expanded', String(opening));
        item.setAttribute('aria-expanded', String(opening));
        button.setAttribute('aria-label', (opening ? 'Collapse ' : 'Expand ') + name);
        button.querySelector('.v2-data-tree-glyph-chevron').classList.toggle('is-open', opening);
    }

    function createRenderer(options) {
        if (!options || typeof options.fetchChildren !== 'function') {
            throw new TypeError('A data-tree child loader is required.');
        }
        const getSource = options.getSource || ((entry) => entry);
        const includeChild = options.includeChild || (() => true);
        const selection = options.selection || null;
        const hasActions = Boolean(options.onRemove);

        function createSelectionCheckbox(entry, path, name, value) {
            const source = getSource(entry);
            const slot = node('label', 'v2-data-tree-checkbox-slot');
            const checkbox = node('input', 'v2-data-tree-checkbox');
            checkbox.type = 'checkbox';
            checkbox.dataset.datasetId = source.id;
            checkbox.dataset.path = path;
            const state = selection.getState ? selection.getState(entry, path, value) : {};
            checkbox.checked = Boolean(state && state.checked);
            checkbox.indeterminate = Boolean(state && state.indeterminate);
            checkbox.setAttribute('aria-checked', checkbox.indeterminate ? 'mixed' : String(checkbox.checked));
            checkbox.setAttribute('aria-label', selection.label
                ? selection.label(entry, path, name, value)
                : 'Select ' + name);
            checkbox.disabled = Boolean(selection.isDisabled && selection.isDisabled(entry, path, value));
            checkbox.addEventListener('click', (event) => event.stopPropagation());
            slot.addEventListener('click', (event) => event.stopPropagation());
            checkbox.addEventListener('change', () => {
                if (selection.onChange) selection.onChange(entry, path, checkbox.checked, value);
            });
            slot.appendChild(checkbox);
            return slot;
        }

        function appendError(parent, error) {
            parent.appendChild(node('li', 'v2-data-tree-error', error && error.message
                ? error.message
                : 'Could not open that folder.'));
        }

        async function handleLoadError(kind, error, item, children, entry) {
            const handler = kind === 'source' ? options.handleSourceError : options.handleChildError;
            if (handler && await handler({ error, item, children, entry })) return;
            appendError(children, error);
        }

        function wireDirectory(item, row, button, children, name, load) {
            item.tabIndex = 0;
            const toggle = async () => {
                const opening = button.getAttribute('aria-expanded') !== 'true';
                setDisclosure(button, item, opening, name);
                children.hidden = !opening;
                if (opening && !children.dataset.loaded) {
                    children.dataset.loaded = 'true';
                    await load();
                }
            };
            row.addEventListener('click', () => toggle());
            item.addEventListener('keydown', (event) => {
                if (event.target !== item || !['Enter', ' ', 'Spacebar'].includes(event.key)) return;
                event.preventDefault();
                return toggle();
            });
        }

        function renderChild(entry, child, depth) {
            if (!includeChild(entry, child)) return null;
            const item = node('li', 'v2-data-tree-item');
            item.setAttribute('role', 'treeitem');
            const rowDepth = Math.max(1, Number(depth) || 1);
            if (child.kind === 'directory') {
                item.setAttribute('aria-expanded', 'false');
                const row = node('div', 'v2-data-tree-row v2-data-tree-folder-row');
                row.style.setProperty('--v2-data-tree-indent', String(rowDepth * 20) + 'px');
                const disclosure = node('button', 'v2-data-tree-disclosure');
                disclosure.type = 'button';
                disclosure.setAttribute('aria-expanded', 'false');
                disclosure.setAttribute('aria-label', 'Expand ' + child.name);
                disclosure.append(glyph('chevron'));
                row.appendChild(disclosure);
                if (selection) row.appendChild(createSelectionCheckbox(entry, child.path, child.name, child));
                const size = sizeCell(null);
                row.append(glyph('folder'), node('span', 'v2-data-tree-name', child.name), size.value);
                labelItemSize(item, child.name, size.label);
                const children = node('ul', 'v2-data-tree-children');
                children.hidden = true;
                children.setAttribute('role', 'group');
                wireDirectory(item, row, disclosure, children, child.name, async () => {
                    try { await loadChildren(entry, children, child.path, 0, false, rowDepth + 1); }
                    catch (error) { await handleLoadError('child', error, item, children, entry); }
                });
                item.append(row, children);
            } else {
                const row = node('span', 'v2-data-tree-row v2-data-tree-file');
                row.style.setProperty('--v2-data-tree-indent', String(rowDepth * 20) + 'px');
                row.appendChild(node('span', 'v2-data-tree-chevron-spacer'));
                if (selection) row.appendChild(createSelectionCheckbox(entry, child.path, child.name, child));
                const size = sizeCell(child.size_bytes);
                row.append(glyph('file'), node('span', 'v2-data-tree-name', child.name), size.value);
                labelItemSize(item, child.name, size.label);
                if (child.kind === 'symlink') row.title = 'Symbolic links are not followed';
                item.appendChild(row);
            }
            return item;
        }

        async function loadChildren(entry, parent, relative, cursor, append, depth) {
            const data = await options.fetchChildren(entry, relative, cursor);
            if (!append) parent.replaceChildren();
            (data.children || []).forEach((child) => {
                const rendered = renderChild(entry, child, depth);
                if (rendered) parent.appendChild(rendered);
            });
            if (data.next_cursor != null) {
                const moreItem = node('li', 'v2-data-tree-item');
                const more = node('button', 'v2-data-tree-more', 'Load more');
                more.type = 'button';
                more.addEventListener('click', async () => {
                    more.disabled = true;
                    moreItem.remove();
                    try { await loadChildren(entry, parent, relative, data.next_cursor, true, depth); }
                    catch (error) { appendError(parent, error); }
                });
                moreItem.appendChild(more);
                parent.appendChild(moreItem);
            }
        }

        function renderSource(entry) {
            const source = getSource(entry);
            const sourceIsFile = source.kind === 'file';
            const item = node('li', 'v2-data-source' + (sourceIsFile ? ' v2-data-source-file' : '') +
                (options.sourceClass ? ' ' + options.sourceClass : ''));
            if (source.id != null) item.dataset.datasetId = source.id;
            item.setAttribute('role', 'treeitem');
            if (!sourceIsFile) item.setAttribute('aria-expanded', 'false');
            const row = node('div', 'v2-data-source-row');
            row.style.setProperty('--v2-data-tree-indent', '0px');
            let disclosure = null;
            if (sourceIsFile) {
                row.appendChild(node('span', 'v2-data-tree-chevron-spacer'));
            } else {
                disclosure = node('button', 'v2-data-source-disclosure');
                disclosure.type = 'button';
                disclosure.setAttribute('aria-expanded', 'false');
                disclosure.setAttribute('aria-label', 'Expand ' + source.display_name);
                disclosure.append(glyph('chevron'));
                row.appendChild(disclosure);
            }
            if (selection) row.appendChild(createSelectionCheckbox(entry, '.', source.display_name, source));
            const sourceSize = sourceIsFile ? sizeCell(source.size_bytes) : sizeCell(null);
            if (sourceIsFile) {
                row.append(glyph('file'), node('span', 'v2-data-source-name', source.display_name), sourceSize.value);
            } else {
                row.append(glyph('folder'), node('span', 'v2-data-source-name', source.display_name), sourceSize.value);
            }
            labelItemSize(item, source.display_name, sourceSize.label);

            if (options.onRemove) {
                const classes = 'v2-icon-button v2-data-source-remove' +
                    (options.removeClass ? ' ' + options.removeClass : '');
                const remove = node('button', classes);
                remove.type = 'button';
                remove.setAttribute('aria-label', options.removeLabel
                    ? options.removeLabel(source, entry)
                    : 'Remove ' + source.display_name);
                remove.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="m4 4 8 8m0-8-8 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
                remove.addEventListener('click', (event) => {
                    event.stopPropagation();
                    options.onRemove(entry, remove);
                });
                row.appendChild(remove);
            }

            if (sourceIsFile) {
                item.appendChild(row);
            } else {
                const tree = node('ul', 'v2-data-tree');
                tree.hidden = true;
                tree.setAttribute('role', 'group');
                wireDirectory(item, row, disclosure, tree, source.display_name, async () => {
                    try { await loadChildren(entry, tree, '.', 0, false, 1); }
                    catch (error) { await handleLoadError('source', error, item, tree, entry); }
                });
                item.append(row, tree);
            }
            return item;
        }

        function ensureHeader(list) {
            const container = list.closest ? list.closest('.v2-data-tree-container') : list.parentElement;
            if (!container) return null;
            container.classList.toggle('v2-data-tree-selection-mode', Boolean(selection));
            container.classList.toggle('v2-data-tree-action-mode', hasActions);
            let header = Array.from(container.children).find((child) =>
                child.classList && child.classList.contains('v2-data-tree-header'));
            if (!header) {
                header = node('div', 'v2-data-tree-header');
                header.append(
                    node('span', 'v2-data-tree-header-name', 'Name'),
                    node('span', 'v2-data-tree-header-size', 'Size')
                );
                container.insertBefore(header, list);
            }
            return header;
        }

        function renderSources(list, entries, renderEntry) {
            list.replaceChildren();
            const render = renderEntry || renderSource;
            (entries || []).forEach((entry) => {
                const rendered = render(entry);
                if (rendered) list.appendChild(rendered);
            });
            const header = ensureHeader(list);
            if (header) header.hidden = list.children.length === 0;
            return list;
        }

        return { renderSource, renderSources, loadChildren, formatSize };
    }

    window.DiscoveryAgentDataTree = { createRenderer, formatSize };
}());
