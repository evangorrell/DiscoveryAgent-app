(function () {
    'use strict';

    function make(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text != null) element.textContent = text;
        return element;
    }

    function confirm(options) {
        const config = Object.assign({
            title: 'Confirm action?',
            message: '',
            confirmLabel: 'Confirm',
            cancelLabel: 'Cancel',
            danger: false,
        }, options || {});

        return new Promise((resolve) => {
            const previouslyFocused = document.activeElement;
            const overlay = make('div', 'v2-modal-overlay v2-confirm-overlay');
            const dialog = make('div', 'v2-modal v2-modal-compact v2-confirm-modal');
            const header = make('div', 'v2-modal-head');
            const title = make('h2', 'v2-modal-title', config.title);
            const body = make('div', 'v2-modal-body');
            const copy = make('p', 'v2-modal-copy v2-confirm-copy', config.message);
            const footer = make('div', 'v2-modal-footer v2-confirm-actions');
            const cancel = make('button', 'v2-btn v2-btn-secondary', config.cancelLabel);
            const accept = make(
                'button',
                'v2-btn ' + (config.danger ? 'v2-btn-danger' : 'v2-btn-primary'),
                config.confirmLabel
            );

            const titleId = 'v2-confirm-title-' + Date.now();
            const bodyId = 'v2-confirm-body-' + Date.now();
            title.id = titleId;
            copy.id = bodyId;
            dialog.setAttribute('role', 'alertdialog');
            dialog.setAttribute('aria-modal', 'true');
            dialog.setAttribute('aria-labelledby', titleId);
            dialog.setAttribute('aria-describedby', bodyId);
            cancel.type = 'button';
            accept.type = 'button';

            function finish(value) {
                document.removeEventListener('keydown', onKeydown);
                document.body.classList.remove('v2-modal-open');
                overlay.remove();
                if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
                    previouslyFocused.focus();
                }
                resolve(value);
            }

            function onKeydown(event) {
                if (event.key === 'Escape') finish(false);
            }

            cancel.addEventListener('click', () => finish(false));
            accept.addEventListener('click', () => finish(true));
            overlay.addEventListener('click', (event) => {
                if (event.target === overlay) finish(false);
            });
            document.addEventListener('keydown', onKeydown);

            header.append(title);
            body.append(copy);
            footer.append(cancel, accept);
            dialog.append(header, body, footer);
            overlay.append(dialog);
            document.body.append(overlay);
            document.body.classList.add('v2-modal-open');
            cancel.focus();
        });
    }

    window.V2Dialog = Object.freeze({ confirm });
})();
