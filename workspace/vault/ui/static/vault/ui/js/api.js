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
  };
})();
