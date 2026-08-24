(function () {
    'use strict';

    function createOverflowAction(options) {
        const config = options || {};
        const root = config.root;
        const content = config.content;
        const action = config.action;
        if (!root || !content || !action) return { refresh() {}, destroy() {} };
        const safetyGap = Math.max(0, Number(config.safetyGap == null ? 12 : config.safetyGap) || 0);

        let frameId = 0;
        let destroyed = false;

        function viewportHeight() {
            const documentHeight = document.documentElement
                ? Number(document.documentElement.clientHeight)
                : 0;
            return documentHeight > 0 ? documentHeight : Number(window.innerHeight) || 0;
        }

        function update() {
            frameId = 0;
            if (destroyed) return;
            if (action.hidden) {
                action.classList.remove('is-sticky');
                root.classList.remove('has-sticky-action');
                root.style.removeProperty('--v2-sticky-action-tray-height');
                root.style.removeProperty('--v2-sticky-action-reserve');
                return;
            }

            const scrollY = Number(window.scrollY || window.pageYOffset) || 0;
            const rootDocumentTop = root.getBoundingClientRect().top + scrollY;
            const availableHeight = Math.max(0, viewportHeight() - rootDocumentTop);
            const naturalHeight = root.scrollHeight;
            const shouldStick = naturalHeight > availableHeight + 1;

            action.classList.toggle('is-sticky', shouldStick);
            root.classList.toggle('has-sticky-action', shouldStick);
            if (shouldStick) {
                const trayHeight = Math.ceil(action.offsetHeight);
                root.style.setProperty('--v2-sticky-action-tray-height', trayHeight + 'px');
                root.style.setProperty('--v2-sticky-action-reserve', trayHeight + safetyGap + 'px');
            } else {
                root.style.removeProperty('--v2-sticky-action-tray-height');
                root.style.removeProperty('--v2-sticky-action-reserve');
            }
        }

        function refresh() {
            if (destroyed || frameId) return;
            frameId = window.requestAnimationFrame(update);
        }

        const resizeObserver = typeof window.ResizeObserver === 'function'
            ? new window.ResizeObserver(refresh)
            : null;
        if (resizeObserver) {
            resizeObserver.observe(root);
            resizeObserver.observe(content);
            resizeObserver.observe(action);
        }

        const mutationObserver = typeof window.MutationObserver === 'function'
            ? new window.MutationObserver(refresh)
            : null;
        if (mutationObserver) {
            mutationObserver.observe(root, {
                subtree: true,
                childList: true,
                attributes: true,
                attributeFilter: ['hidden', 'aria-expanded'],
            });
        }

        window.addEventListener('resize', refresh, { passive: true });
        window.addEventListener('focus', refresh);
        window.addEventListener('pageshow', refresh);
        document.addEventListener('visibilitychange', refresh);
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(refresh, () => {});
        refresh();

        return {
            refresh,
            destroy() {
                destroyed = true;
                if (frameId) window.cancelAnimationFrame(frameId);
                if (resizeObserver) resizeObserver.disconnect();
                if (mutationObserver) mutationObserver.disconnect();
                window.removeEventListener('resize', refresh);
                window.removeEventListener('focus', refresh);
                window.removeEventListener('pageshow', refresh);
                document.removeEventListener('visibilitychange', refresh);
            },
        };
    }

    window.DiscoveryAgentAppPageAction = Object.freeze({ create: createOverflowAction });
})();
