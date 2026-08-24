// Shared trusted-report figure presentation for Project Home and Round Workspace.
(function (root) {
    'use strict';

    const node = (tag, className, text) => {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text != null) element.textContent = text;
        return element;
    };

    const escapeHtml = (value) => String(value || '').replace(
        /[&<>"']/g,
        (character) => character === '&' ? '&amp;'
            : character === '<' ? '&lt;'
                : character === '>' ? '&gt;'
                    : character === '"' ? '&quot;' : '&#39;',
    );

    function renderFallbackInline(value) {
        return escapeHtml(value)
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    }

    function fallbackListItem(line) {
        const match = String(line || '').match(/^(\s*)([-+*]|\d+[.)])\s+(.+)$/);
        if (!match) return null;
        return {
            indent: match[1].replace(/\t/g, '    ').length,
            type: /^\d/.test(match[2]) ? 'ol' : 'ul',
            start: /^\d/.test(match[2]) ? Number.parseInt(match[2], 10) : null,
            text: match[3],
        };
    }

    // The app can run without CDN access. Keep that safe fallback deliberately
    // small, but recognize standard Markdown list syntax so a valid report does
    // not degrade into literal hyphen/number-prefixed prose.
    function renderFallbackMarkdown(value, options = {}) {
        const lines = String(value || '').replace(/\r\n?/g, '\n').split('\n');
        const headingOffset = Number.isInteger(options.headingOffset) ? options.headingOffset : 0;
        let index = 0;

        const renderList = (baseIndent, type) => {
            const firstItem = fallbackListItem(lines[index]);
            const start = type === 'ol' && firstItem && firstItem.start !== 1
                ? ' start="' + firstItem.start + '"'
                : '';
            let html = '<' + type + start + '>';
            while (index < lines.length) {
                const item = fallbackListItem(lines[index]);
                if (!item || item.indent !== baseIndent || item.type !== type) break;
                index += 1;
                let body = renderFallbackInline(item.text);
                let children = '';

                while (index < lines.length && lines[index].trim()) {
                    const nested = fallbackListItem(lines[index]);
                    if (nested) {
                        if (nested.indent <= baseIndent) break;
                        children += renderList(nested.indent, nested.type);
                        continue;
                    }
                    if (/^\s*#{1,6}\s+/.test(lines[index]) || /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(lines[index])) break;
                    body += '<br>' + renderFallbackInline(lines[index].trim());
                    index += 1;
                }
                html += '<li>' + body + children + '</li>';

                if (index >= lines.length || lines[index].trim()) continue;
                let next = index;
                while (next < lines.length && !lines[next].trim()) next += 1;
                const nextItem = fallbackListItem(lines[next]);
                if (!nextItem || nextItem.indent !== baseIndent || nextItem.type !== type) break;
                index = next;
            }
            return html + '</' + type + '>';
        };

        let html = '';
        while (index < lines.length) {
            const line = lines[index];
            if (!line.trim()) {
                index += 1;
                continue;
            }
            const listItem = fallbackListItem(line);
            if (listItem) {
                html += renderList(listItem.indent, listItem.type);
                continue;
            }
            const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);
            if (heading) {
                const level = Math.min(heading[1].length + headingOffset, 6);
                html += '<h' + level + '>' + renderFallbackInline(heading[2]) + '</h' + level + '>';
                index += 1;
                continue;
            }
            if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
                html += '<hr>';
                index += 1;
                continue;
            }
            const paragraph = [];
            while (index < lines.length && lines[index].trim()) {
                if (paragraph.length && (
                    fallbackListItem(lines[index])
                    || /^\s*#{1,6}\s+/.test(lines[index])
                    || /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(lines[index])
                )) break;
                paragraph.push(lines[index].trim());
                index += 1;
            }
            html += '<p>' + paragraph.map(renderFallbackInline).join('<br>') + '</p>';
        }
        return html;
    }

    function attachFigureResizer(shell, stage, options = {}) {
        if (!shell || !stage) return null;
        const handle = node('button', 'rw-figure-resize-handle');
        handle.type = 'button';
        handle.draggable = false;
        handle.setAttribute('role', 'slider');
        handle.setAttribute('aria-label', 'Resize figure');
        handle.title = 'Drag to resize figure';
        const icon = node('span', 'rw-figure-resize-icon');
        icon.setAttribute('aria-hidden', 'true');
        icon.append(
            node('span', 'rw-figure-resize-arrow is-northwest'),
            node('span', 'rw-figure-resize-arrow is-southeast'),
        );
        handle.append(icon);
        stage.append(handle);

        const horizontalGutter = Math.max(0, Number(options.horizontalGutter) || 0);
        const limits = () => {
            const parent = shell.parentElement;
            const available = parent
                ? parent.getBoundingClientRect().width
                : shell.getBoundingClientRect().width;
            const max = Math.max(1, available - horizontalGutter);
            return { min: Math.min(240, max), max };
        };
        let destroyed = false;
        const applyWidth = (requested) => {
            if (destroyed) return;
            const range = limits();
            const width = Math.max(range.min, Math.min(range.max, requested));
            shell.style.width = width >= range.max - 1 ? '100%' : Math.round(width) + 'px';
            handle.setAttribute('aria-valuemin', String(Math.round(range.min)));
            handle.setAttribute('aria-valuemax', String(Math.round(range.max)));
            handle.setAttribute('aria-valuenow', String(Math.round(width)));
            handle.setAttribute(
                'aria-valuetext',
                Math.round((width / range.max) * 100) + '% of available width',
            );
        };
        let drag = null;
        const onPointerDown = (event) => {
            if (event.pointerType === 'mouse' && event.button !== 0) return;
            event.preventDefault();
            event.stopPropagation();
            const rect = shell.getBoundingClientRect();
            drag = {
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                startWidth: rect.width,
            };
            shell.classList.add('is-resizing');
            if (handle.setPointerCapture) handle.setPointerCapture(event.pointerId);
        };
        const onPointerMove = (event) => {
            if (!drag || event.pointerId !== drag.pointerId) return;
            event.preventDefault();
            event.stopPropagation();
            const diagonalDelta = (
                (event.clientX - drag.startX) + (event.clientY - drag.startY)
            ) / 2;
            applyWidth(drag.startWidth + diagonalDelta);
        };
        const finishResize = (event) => {
            if (!drag || event.pointerId !== drag.pointerId) return;
            drag = null;
            shell.classList.remove('is-resizing');
        };
        const onKeyDown = (event) => {
            const range = limits();
            const current = shell.getBoundingClientRect().width;
            let next = null;
            if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next = current + 24;
            if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next = current - 24;
            if (event.key === 'Home') next = range.min;
            if (event.key === 'End') next = range.max;
            if (next === null) return;
            event.preventDefault();
            event.stopPropagation();
            applyWidth(next);
        };
        const stopClick = (event) => event.stopPropagation();
        const stopNativeDrag = (event) => {
            event.preventDefault();
            event.stopPropagation();
        };
        handle.addEventListener('pointerdown', onPointerDown);
        handle.addEventListener('pointermove', onPointerMove);
        handle.addEventListener('pointerup', finishResize);
        handle.addEventListener('pointercancel', finishResize);
        handle.addEventListener('lostpointercapture', finishResize);
        handle.addEventListener('keydown', onKeyDown);
        handle.addEventListener('click', stopClick);
        handle.addEventListener('dragstart', stopNativeDrag);

        const animationFrame = typeof root.requestAnimationFrame === 'function'
            ? root.requestAnimationFrame(() => {
                if (shell.getBoundingClientRect().width) {
                    applyWidth(shell.getBoundingClientRect().width);
                }
            })
            : null;
        if (animationFrame === null && shell.getBoundingClientRect().width) {
            applyWidth(shell.getBoundingClientRect().width);
        }

        return {
            handle,
            limits,
            resize: applyWidth,
            destroy() {
                if (destroyed) return;
                destroyed = true;
                if (
                    animationFrame !== null
                    && typeof root.cancelAnimationFrame === 'function'
                ) {
                    root.cancelAnimationFrame(animationFrame);
                }
                if (
                    drag
                    && handle.hasPointerCapture
                    && handle.hasPointerCapture(drag.pointerId)
                    && handle.releasePointerCapture
                ) {
                    try { handle.releasePointerCapture(drag.pointerId); } catch (_error) {}
                }
                drag = null;
                shell.classList.remove('is-resizing');
                handle.removeEventListener('pointerdown', onPointerDown);
                handle.removeEventListener('pointermove', onPointerMove);
                handle.removeEventListener('pointerup', finishResize);
                handle.removeEventListener('pointercancel', finishResize);
                handle.removeEventListener('lostpointercapture', finishResize);
                handle.removeEventListener('keydown', onKeyDown);
                handle.removeEventListener('click', stopClick);
                handle.removeEventListener('dragstart', stopNativeDrag);
            },
        };
    }

    function attachEmbeddedFigureResizer(shell, stage) {
        return attachFigureResizer(shell, stage);
    }


    function collapseEmbeddedFigures(element) {
        const candidates = Array.from(element.querySelectorAll('figure, img')).filter(
            (item) => item.matches('figure') || !item.closest('figure'),
        );
        candidates.forEach((content, index) => {
            if (content.closest('details.rw-embedded-figure')) return;
            const image = content.matches('img') ? content : content.querySelector('img');
            if (!image && !content.classList.contains('is-unavailable')) return;
            const caption = content.matches('figure') ? content.querySelector('figcaption') : null;
            const title = (
                (caption && caption.textContent.trim())
                || (image && image.alt && image.alt.trim())
                || ''
            );
            const details = node('details', 'rw-embedded-figure');
            const summary = node(
                'summary',
                'rw-embedded-figure-summary',
                title ? 'Figure: ' + title : 'Figure ' + (index + 1),
            );
            details.open = false;
            const parent = content.parentNode;
            if (!parent) return;
            parent.insertBefore(details, content);
            if (image) {
                const shell = node('div', 'rw-figure-resize-shell');
                const stage = node('div', 'rw-figure-image-stage');
                if (content.matches('figure')) {
                    content.insertBefore(stage, image);
                    stage.append(image);
                    shell.append(content);
                } else {
                    stage.append(content);
                    shell.append(stage);
                }
                attachEmbeddedFigureResizer(shell, stage);
                details.append(summary, shell);
            } else {
                details.append(summary, content);
            }
        });
    }

    function attachAuditFigureDragging(element, figures, options) {
        const drag = options && options.auditFigureDrag;
        if (!drag || typeof drag.start !== 'function') return;
        const rendered = Array.from(element.querySelectorAll('details.rw-embedded-figure'));
        rendered.forEach((details, index) => {
            const figure = Array.isArray(figures) ? figures[index] : null;
            if (!figure || !figure.available || !figure.artifact) return;
            const stage = details.querySelector('.rw-figure-image-stage');
            if (!stage) return;
            const caption = details.querySelector('figcaption');
            const image = stage.querySelector('img');
            details.classList.add('is-audit-artifact-draggable');
            const start = (event) => {
                if (!event.dataTransfer || drag.start(event, figure.artifact) === false) {
                    event.preventDefault();
                    return;
                }
                event.stopPropagation();
            };
            const end = () => {
                if (typeof drag.end === 'function') drag.end();
            };
            [image, stage, caption].filter(Boolean).forEach((source) => {
                source.draggable = true;
                source.addEventListener('dragstart', start);
                source.addEventListener('dragend', end);
            });
            details.querySelectorAll('summary,button,a,.rw-figure-resize-handle').forEach((control) => {
                control.draggable = false;
                control.addEventListener('dragstart', (event) => event.preventDefault());
            });
        });
    }

    function renderMarkdown(element, text) {
        if (!root.marked) {
            element.innerHTML = renderFallbackMarkdown(text);
            return;
        }
        const html = root.marked.parse(text || '', { gfm: true, breaks: true });
        element.innerHTML = root.DOMPurify ? root.DOMPurify.sanitize(html) : html;
        collapseEmbeddedFigures(element);
    }

    function renderReportMarkdown(element, text, figures, options = {}) {
        if (!root.marked) {
            element.innerHTML = renderFallbackMarkdown(text);
            return;
        }
        const figureMap = new Map((figures || []).map((figure) => [figure.token, figure]));
        const renderer = new root.marked.Renderer();
        renderer.image = function (href, title, alt) {
            if (href && typeof href === 'object') {
                alt = href.text || '';
                title = href.title || '';
                href = href.href || '';
            }
            const prefix = 'rw-report-figure:';
            if (typeof href !== 'string' || !href.startsWith(prefix)) return '';
            const figure = figureMap.get(href.slice(prefix.length));
            if (!figure) return '';
            const safeAlt = escapeHtml(figure.alt || alt || '');
            if (!figure.available) {
                return '<figure class="rw-report-figure is-unavailable">'
                    + '<div class="rw-report-figure-unavailable"><strong>Figure unavailable</strong>'
                    + (safeAlt ? '<span>' + safeAlt + '</span>' : '')
                    + '<small>The referenced artifact is not available in this synchronized project copy.</small>'
                    + '</div></figure>';
            }
            const caption = safeAlt ? '<figcaption>' + safeAlt + '</figcaption>' : '';
            return '<figure class="rw-report-figure"><img src="' + figure.url
                + '" alt="' + safeAlt + '" loading="lazy">' + caption + '</figure>';
        };
        const html = root.marked.parse(text || '', { gfm: true, breaks: true, renderer });
        element.innerHTML = root.DOMPurify ? root.DOMPurify.sanitize(html) : html;
        collapseEmbeddedFigures(element);
        attachAuditFigureDragging(element, figures, options);
    }

    root.DiscoveryReportFigures = {
        attachFigureResizer,
        attachEmbeddedFigureResizer,
        attachAuditFigureDragging,
        collapseEmbeddedFigures,
        renderFallbackMarkdown,
        renderMarkdown,
        renderReportMarkdown,
    };
}(window));
