function getCSRFToken() {
    return document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
}

/**
 * Shared file actions — dialog helpers and API calls.
 *
 * Returns promises so consumers can .then() for their own refresh logic.
 * Dialogs use $dispatch events; consumers listen for those events
 * and call these functions.
 *
 * Usage (in an Alpine component):
 *
 *   init() {
 *     window.addEventListener('create-folder', function(e) {
 *       window.fileActions.createFolder(e.detail.name, parentUuid)
 *         .then(function() { refreshSidebar(); });
 *     });
 *     window.addEventListener('rename-item', function(e) {
 *       window.fileActions.renameItem(e.detail.uuid, e.detail.name)
 *         .then(function() { refreshSidebar(); });
 *     });
 *   }
 */
window.fileActions = {
    // ── Dialog helpers ───────────────────────────────────

    showCreateFolderDialog: function() {
        var dialog = document.getElementById('create-folder-dialog');
        if (!dialog) return;
        var input = dialog.querySelector('input');
        if (input) input.value = '';
        dialog.showModal();
        setTimeout(function() { if (input) input.focus(); }, 100);
    },

    showRenameDialog: function(uuid, name) {
        var dialog = document.getElementById('rename-dialog');
        if (!dialog) return;
        window.dispatchEvent(new CustomEvent('open-rename', { detail: { uuid: uuid, name: name } }));
        dialog.showModal();
        setTimeout(function() {
            var input = dialog.querySelector('input');
            if (input) input.focus();
        }, 100);
    },

    // ── API calls (return promises) ──────────────────────

    /**
     * Create a folder. Resolves with the created file object, rejects on error.
     * Closes the dialog on success.
     */
    createFolder: function(name, parent) {
        return fetch('/api/v1/files', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({
                name: name,
                node_type: 'folder',
                parent: parent || null,
            }),
        }).then(function(resp) {
            if (!resp.ok) {
                return resp.json().catch(function() { return {}; }).then(function(data) {
                    throw new Error(data.name ? data.name[0] : (data.detail || 'Failed to create folder'));
                });
            }
            var dlg = document.getElementById('create-folder-dialog');
            if (dlg) dlg.close();
            return resp.json();
        });
    },

    /**
     * Rename a file/folder. Resolves with the updated object, rejects on error.
     * Closes the dialog on success.
     */
    renameItem: function(uuid, newName) {
        return fetch('/api/v1/files/' + uuid, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ name: newName }),
        }).then(function(resp) {
            if (!resp.ok) {
                return resp.json().catch(function() { return {}; }).then(function(data) {
                    throw new Error(data.name ? data.name[0] : (data.detail || 'Failed to rename'));
                });
            }
            var dlg = document.getElementById('rename-dialog');
            if (dlg) dlg.close();
            return resp.json();
        });
    },
};
