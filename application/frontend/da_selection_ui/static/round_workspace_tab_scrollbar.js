(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.RoundWorkspaceTabScrollbar = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const OVERFLOW_TOLERANCE = 1;
    const MINIMUM_THUMB_WIDTH = 24;

    function clamp(value, minimum, maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }

    function geometry(scrollWidth, clientWidth, scrollLeft, trackWidth) {
        const total = Math.max(0, Number(scrollWidth) || 0);
        const visible = Math.max(0, Number(clientWidth) || 0);
        const track = Math.max(0, Number(trackWidth) || 0);
        const maximum = Math.max(0, total - visible);
        const current = clamp(Number(scrollLeft) || 0, 0, maximum);
        const overflow = total > visible + OVERFLOW_TOLERANCE && visible > 0;
        if (!overflow || track <= 0) {
            return {
                overflow: false,
                maximum,
                current,
                thumbWidth: 0,
                thumbOffset: 0,
                thumbTravel: 0,
            };
        }
        const thumbWidth = Math.min(
            track,
            Math.max(MINIMUM_THUMB_WIDTH, track * (visible / total)),
        );
        const thumbTravel = Math.max(0, track - thumbWidth);
        const thumbOffset = maximum > 0 ? thumbTravel * (current / maximum) : 0;
        return { overflow, maximum, current, thumbWidth, thumbOffset, thumbTravel };
    }

    function scrollForTrackPoint(metrics, point) {
        if (!metrics || !metrics.overflow || metrics.thumbTravel <= 0) return 0;
        const centered = clamp(
            Number(point) - metrics.thumbWidth / 2,
            0,
            metrics.thumbTravel,
        );
        return metrics.maximum * (centered / metrics.thumbTravel);
    }

    function scrollForKey(key, current, maximum, pageSize) {
        const value = clamp(Number(current) || 0, 0, Math.max(0, maximum));
        const page = Math.max(40, (Number(pageSize) || 0) * 0.8);
        if (key === 'ArrowLeft') return clamp(value - 40, 0, maximum);
        if (key === 'ArrowRight') return clamp(value + 40, 0, maximum);
        if (key === 'PageUp') return clamp(value - page, 0, maximum);
        if (key === 'PageDown') return clamp(value + page, 0, maximum);
        if (key === 'Home') return 0;
        if (key === 'End') return Math.max(0, maximum);
        return null;
    }

    function create(scroller, track, thumb) {
        if (!scroller || !track || !thumb) return null;
        let frame = 0;
        let resizeObserver = null;
        let mutationObserver = null;
        let drag = null;
        let activeElement = null;
        let activeFrame = 0;
        let showing = false;
        let destroyed = false;
        let fontSet = null;
        let fontReadyCancelled = false;

        function requestFrame(callback) {
            const request = typeof requestAnimationFrame === 'function'
                ? requestAnimationFrame
                : function (next) { return setTimeout(next, 0); };
            return request(callback);
        }

        function cancelFrame(handle) {
            if (!handle) return;
            if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(handle);
            else clearTimeout(handle);
        }

        function setVisible(visible) {
            showing = visible;
            track.hidden = !visible;
            track.tabIndex = visible ? 0 : -1;
            if (visible) track.removeAttribute('aria-hidden');
            else track.setAttribute('aria-hidden', 'true');
            if ('inert' in track) track.inert = !visible;
            const host = track.parentElement;
            if (host && host.classList) {
                if (visible) host.classList.add('has-tab-overflow');
                else host.classList.remove('has-tab-overflow');
            }
        }

        function collapse() {
            if (drag) {
                const pointerId = drag.pointerId;
                drag = null;
                if (
                    thumb.hasPointerCapture
                    && thumb.hasPointerCapture(pointerId)
                    && thumb.releasePointerCapture
                ) thumb.releasePointerCapture(pointerId);
            }
            track.classList.remove('is-dragging');
            setVisible(false);
            if (scroller.scrollLeft !== 0) scroller.scrollLeft = 0;
            track.setAttribute('aria-valuemin', '0');
            track.setAttribute('aria-valuemax', '0');
            track.setAttribute('aria-valuenow', '0');
            thumb.style.width = '';
            thumb.style.transform = '';
        }

        function revealActive() {
            if (!activeElement || !activeElement.isConnected) return;
            if (typeof activeElement.scrollIntoView !== 'function') return;
            activeElement.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }

        function measure() {
            frame = 0;
            if (destroyed || !scroller.isConnected || !track.isConnected) return;
            const overflow = scroller.scrollWidth > scroller.clientWidth + OVERFLOW_TOLERANCE
                && scroller.clientWidth > 0;
            if (!overflow) {
                collapse();
                return;
            }
            const becameVisible = !showing;
            setVisible(true);
            if (becameVisible) revealActive();
            const metrics = geometry(
                scroller.scrollWidth,
                scroller.clientWidth,
                scroller.scrollLeft,
                track.clientWidth,
            );
            if (scroller.scrollLeft !== metrics.current) scroller.scrollLeft = metrics.current;
            track.setAttribute('aria-valuemin', '0');
            track.setAttribute('aria-valuemax', String(Math.round(metrics.maximum)));
            track.setAttribute('aria-valuenow', String(Math.round(metrics.current)));
            thumb.style.width = metrics.thumbWidth + 'px';
            thumb.style.transform = 'translateX(' + metrics.thumbOffset + 'px)';
        }

        function schedule() {
            if (destroyed || frame) return;
            frame = requestFrame(measure);
        }

        function currentGeometry() {
            return geometry(
                scroller.scrollWidth,
                scroller.clientWidth,
                scroller.scrollLeft,
                track.clientWidth,
            );
        }

        function onTrackPointerDown(event) {
            if (event.target === thumb || event.button !== 0) return;
            const metrics = currentGeometry();
            if (!metrics.overflow) return;
            const bounds = track.getBoundingClientRect();
            scroller.scrollLeft = scrollForTrackPoint(metrics, event.clientX - bounds.left);
            track.focus({ preventScroll: true });
            event.preventDefault();
            schedule();
        }

        function finishDrag(event) {
            if (!drag || (event && event.pointerId !== drag.pointerId)) return;
            if (thumb.hasPointerCapture && thumb.hasPointerCapture(drag.pointerId)) {
                thumb.releasePointerCapture(drag.pointerId);
            }
            drag = null;
            track.classList.remove('is-dragging');
            schedule();
        }

        function onThumbPointerDown(event) {
            if (event.button !== 0) return;
            const metrics = currentGeometry();
            if (!metrics.overflow) return;
            drag = {
                pointerId: event.pointerId,
                startX: event.clientX,
                startScroll: metrics.current,
                maximum: metrics.maximum,
                thumbTravel: metrics.thumbTravel,
            };
            if (thumb.setPointerCapture) thumb.setPointerCapture(event.pointerId);
            track.classList.add('is-dragging');
            track.focus({ preventScroll: true });
            event.preventDefault();
            event.stopPropagation();
        }

        function onThumbPointerMove(event) {
            if (!drag || event.pointerId !== drag.pointerId || drag.thumbTravel <= 0) return;
            const delta = event.clientX - drag.startX;
            scroller.scrollLeft = clamp(
                drag.startScroll + delta * (drag.maximum / drag.thumbTravel),
                0,
                drag.maximum,
            );
            event.preventDefault();
            schedule();
        }

        function onKeyDown(event) {
            const metrics = currentGeometry();
            const next = scrollForKey(
                event.key,
                metrics.current,
                metrics.maximum,
                scroller.clientWidth,
            );
            if (next === null) return;
            scroller.scrollLeft = next;
            event.preventDefault();
            schedule();
        }

        function observeChildren() {
            if (!resizeObserver) return;
            resizeObserver.disconnect();
            resizeObserver.observe(scroller);
            resizeObserver.observe(track);
            Array.from(scroller.children).forEach((child) => resizeObserver.observe(child));
        }

        scroller.addEventListener('scroll', schedule, { passive: true });
        track.addEventListener('pointerdown', onTrackPointerDown);
        track.addEventListener('keydown', onKeyDown);
        thumb.addEventListener('pointerdown', onThumbPointerDown);
        thumb.addEventListener('pointermove', onThumbPointerMove);
        thumb.addEventListener('pointerup', finishDrag);
        thumb.addEventListener('pointercancel', finishDrag);
        if (typeof ResizeObserver === 'function') {
            resizeObserver = new ResizeObserver(schedule);
            observeChildren();
        }
        if (typeof MutationObserver === 'function') {
            mutationObserver = new MutationObserver(function () {
                observeChildren();
                schedule();
            });
            mutationObserver.observe(scroller, {
                childList: true,
                subtree: true,
                characterData: true,
            });
        }
        if (typeof document !== 'undefined' && document.fonts) {
            fontSet = document.fonts;
            if (fontSet.ready && typeof fontSet.ready.then === 'function') {
                fontSet.ready.then(function () {
                    if (!fontReadyCancelled) schedule();
                }).catch(function () {});
            }
            if (typeof fontSet.addEventListener === 'function') {
                fontSet.addEventListener('loadingdone', schedule);
            }
        }
        if (typeof addEventListener === 'function') addEventListener('resize', schedule);
        collapse();
        schedule();

        return {
            update: schedule,
            scrollActive: function (element) {
                activeElement = element;
                if (!element || typeof element.scrollIntoView !== 'function') return;
                if (activeFrame) return;
                activeFrame = requestFrame(function () {
                    activeFrame = 0;
                    if (destroyed) return;
                    revealActive();
                    schedule();
                });
            },
            destroy: function () {
                if (destroyed) return;
                destroyed = true;
                cancelFrame(frame);
                cancelFrame(activeFrame);
                frame = 0;
                activeFrame = 0;
                fontReadyCancelled = true;
                if (resizeObserver) resizeObserver.disconnect();
                if (mutationObserver) mutationObserver.disconnect();
                if (fontSet && typeof fontSet.removeEventListener === 'function') {
                    fontSet.removeEventListener('loadingdone', schedule);
                }
                if (typeof removeEventListener === 'function') removeEventListener('resize', schedule);
                scroller.removeEventListener('scroll', schedule);
                track.removeEventListener('pointerdown', onTrackPointerDown);
                track.removeEventListener('keydown', onKeyDown);
                thumb.removeEventListener('pointerdown', onThumbPointerDown);
                thumb.removeEventListener('pointermove', onThumbPointerMove);
                thumb.removeEventListener('pointerup', finishDrag);
                thumb.removeEventListener('pointercancel', finishDrag);
                finishDrag();
                collapse();
                activeElement = null;
            },
        };
    }

    return {
        OVERFLOW_TOLERANCE,
        MINIMUM_THUMB_WIDTH,
        geometry,
        scrollForTrackPoint,
        scrollForKey,
        create,
    };
}));
