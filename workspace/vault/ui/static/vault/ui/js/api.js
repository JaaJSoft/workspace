// Every request the vault makes, in one place. No response is ever cached and
// no error is swallowed: a failed call throws with the status attached, and
// the controller decides what the user is told.
function VaultApiError(message, status) {
  const error = new Error(message);
  error.name = 'VaultApiError';
  error.status = status;
  return error;
}

window.vaultApi = (function () {
  function request(url, options) {
    const settings = options || {};
    const method = settings.method || 'GET';
    // The token goes on every unsafe method, not on every request with a
    // body: DELETE has no body and Django refuses it just the same.
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') headers['X-CSRFToken'] = getCSRFToken();
    if (settings.body) headers['Content-Type'] = 'application/json';
    return fetch(url, {
      method: method,
      headers: headers,
      body: settings.body ? JSON.stringify(settings.body) : undefined,
    }).then(function (response) {
      if (!response.ok) {
        throw VaultApiError('the vault API refused the request', response.status);
      }
      return response.status === 204 ? null : response.json();
    });
  }

  return {
    Error: VaultApiError,
    fetchEnvelope: function () {
      return request('/api/v1/vault/account/envelope');
    },
    listVaults: function () {
      return request('/api/v1/vault/vaults');
    },
    createVault: function (body) {
      return request('/api/v1/vault/vaults', { method: 'POST', body: body });
    },
    updateVault: function (uuid, body) {
      return request('/api/v1/vault/vaults/' + uuid, { method: 'PATCH', body: body });
    },
    deleteVault: function (uuid) {
      return request('/api/v1/vault/vaults/' + uuid, { method: 'DELETE' });
    },
    listFolders: function (vaultUuid) {
      return request('/api/v1/vault/folders?vault=' + encodeURIComponent(vaultUuid));
    },
    createFolder: function (body) {
      return request('/api/v1/vault/folders', { method: 'POST', body: body });
    },
    updateFolder: function (uuid, body) {
      return request('/api/v1/vault/folders/' + uuid, { method: 'PATCH', body: body });
    },
    // Not a DELETE: the folder's entries have to be re-signed with no folder,
    // and they travel with the deletion so the two cannot half-happen.
    deleteFolder: function (uuid, entries) {
      return request('/api/v1/vault/folders/' + uuid + '/delete', {
        method: 'POST',
        body: { entries: entries },
      });
    },
    listTags: function (vaultUuid) {
      return request('/api/v1/vault/tags?vault=' + encodeURIComponent(vaultUuid));
    },
    createTag: function (body) {
      return request('/api/v1/vault/tags', { method: 'POST', body: body });
    },
    updateTag: function (uuid, body) {
      return request('/api/v1/vault/tags/' + uuid, { method: 'PATCH', body: body });
    },
    deleteTag: function (uuid) {
      return request('/api/v1/vault/tags/' + uuid, { method: 'DELETE' });
    },
    listEntries: function (vaultUuid, options) {
      let url = '/api/v1/vault/entries?vault=' + encodeURIComponent(vaultUuid);
      if (options && options.trashed) url += '&trashed=true';
      return request(url);
    },
    getEntry: function (uuid) {
      return request('/api/v1/vault/entries/' + uuid);
    },
    createEntry: function (body) {
      return request('/api/v1/vault/entries', { method: 'POST', body: body });
    },
    // PUT, not PATCH: the signature covers every field, so a partial write
    // would store a signature over values the row no longer holds.
    updateEntry: function (uuid, body) {
      return request('/api/v1/vault/entries/' + uuid, { method: 'PUT', body: body });
    },
    // The trash is a view, not a rewrite: the server sets deleted_at and
    // leaves metadata_sig alone, so the entry still verifies from the trash.
    trashEntry: function (uuid) {
      return request('/api/v1/vault/entries/' + uuid, { method: 'DELETE' });
    },
    restoreEntry: function (uuid) {
      return request('/api/v1/vault/entries/' + uuid + '/restore', { method: 'POST' });
    },
    // POST, not DELETE: DELETE on an entry is the trash, and this is the step
    // after it.
    purgeEntry: function (uuid) {
      return request('/api/v1/vault/entries/' + uuid + '/purge', { method: 'POST' });
    },
  };
})();
