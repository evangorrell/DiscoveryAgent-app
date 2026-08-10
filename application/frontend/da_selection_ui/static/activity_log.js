// Return to the local Projects entry if a request cannot use the current session.
const originalFetch = window.fetch;
window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    if (response.status === 401) {
        window.location.href = '/projects';
    }
    return response;
};

function showStatus(elementId, message, type) {
    const element = document.getElementById(elementId);
    if (!element) return;
    element.textContent = message;
    element.className = `status-message ${type}`;
    element.style.display = 'block';
}

async function finalizeSession() {
    const actionsInput = document.getElementById('activity-log-input');
    const finalizeBtn = document.getElementById('activity-log-finalize-btn');
    const statusId = 'activity-log-status';
    const postSelectionActions = actionsInput ? actionsInput.value.trim() : '';

    if (finalizeBtn) {
        finalizeBtn.disabled = true;
        finalizeBtn.textContent = 'Finalizing...';
    }
    showStatus(statusId, 'Saving activity log...', 'success');

    try {
        const response = await fetch('/api/post_selection_actions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                post_selection_actions: postSelectionActions || null
            })
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.error || 'Failed to save activity log.');
        }

        window.location.href = '/thank_you';
    } catch (error) {
        showStatus(statusId, `Error: ${error.message}`, 'error');
        if (finalizeBtn) {
            finalizeBtn.disabled = false;
            finalizeBtn.textContent = 'Finalize';
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const finalizeBtn = document.getElementById('activity-log-finalize-btn');
    if (finalizeBtn) {
        finalizeBtn.addEventListener('click', finalizeSession);
    }
});
