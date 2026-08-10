(function () {
    'use strict';

    const token = (window.crypto && typeof window.crypto.randomUUID === 'function')
        ? window.crypto.randomUUID().replace(/-/g, '')
        : Math.random().toString(36).slice(2) + Date.now().toString(36);

    function heartbeat() {
        fetch('/api/browser-presence', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, action: 'heartbeat' }),
            keepalive: true
        }).catch(function () {});
    }

    function closeLease() {
        const body = new Blob(
            [JSON.stringify({ token, action: 'close' })],
            { type: 'application/json' }
        );
        navigator.sendBeacon('/api/browser-presence', body);
    }

    heartbeat();
    window.setInterval(heartbeat, 5000);
    window.addEventListener('pagehide', closeLease);
}());
