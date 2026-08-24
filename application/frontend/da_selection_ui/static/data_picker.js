(function () {
    'use strict';

    let pickerPending = false;

    async function readJson(response) {
        try { return await response.json(); }
        catch (_error) { return null; }
    }

    function setControlsBusy(controls, busy) {
        (controls || []).filter(Boolean).forEach((control) => {
            control.disabled = Boolean(busy);
            if (busy) control.setAttribute('aria-busy', 'true');
            else control.removeAttribute('aria-busy');
        });
    }

    async function chooseDataFolders(options) {
        const config = Object.assign({ controls: [] }, options || {});
        if (pickerPending) return { ignored: true };

        pickerPending = true;
        setControlsBusy(config.controls, true);
        try {
            const response = await fetch('/api/data-library/choose', {
                method: 'POST',
                credentials: 'include',
                headers: { Accept: 'application/json' },
            });
            const data = await readJson(response);
            if (!(response.ok && data && data.success)) {
                const error = new Error((data && data.error) || 'The folder chooser could not be opened.');
                error.code = data && data.code;
                throw error;
            }
            return data;
        } finally {
            pickerPending = false;
            setControlsBusy(config.controls, false);
        }
    }

    window.DiscoveryAgentDataPicker = Object.freeze({
        choose: chooseDataFolders,
        isPending: () => pickerPending,
    });
})();
