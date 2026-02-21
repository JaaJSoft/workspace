/**
 * File locking mixin for Alpine.js viewers.
 *
 * Usage in a viewer's Alpine component:
 *
 *   var lock = window.fileLock(fileUuid, getCSRFToken);
 *
 *   // In Alpine data:
 *   lockOwner: <initial value from template>,
 *   ...lock.data(),
 *
 *   // Call lock.acquire(self, callbacks) after editor is ready
 *   // Call lock.dispose(self) in dispose()
 */
window.fileLock = function(fileUuid, getCSRFToken) {
  var _heartbeatTimer = null;
  var _beforeUnloadHandler = null;
  var _lockUrl = '/api/v1/files/' + fileUuid + '/lock';

  function _fetch(method) {
    return fetch(_lockUrl, {
      method: method,
      headers: { 'X-CSRFToken': getCSRFToken() },
      credentials: 'same-origin',
      keepalive: method === 'DELETE',
    });
  }

  function startHeartbeat(self, onConflict) {
    stopHeartbeat();
    _heartbeatTimer = setInterval(function() {
      _fetch('POST').then(function(resp) {
        if (resp.status === 409) {
          resp.json().then(function(data) {
            self.lockOwner = data.locked_by ? data.locked_by.username : 'Another user';
            if (onConflict) onConflict();
          });
        }
      }).catch(function() {});
    }, 120000);
    _beforeUnloadHandler = function() { _fetch('DELETE').catch(function() {}); };
    window.addEventListener('beforeunload', _beforeUnloadHandler);
  }

  function stopHeartbeat() {
    if (_heartbeatTimer) {
      clearInterval(_heartbeatTimer);
      _heartbeatTimer = null;
    }
    if (_beforeUnloadHandler) {
      window.removeEventListener('beforeunload', _beforeUnloadHandler);
      _beforeUnloadHandler = null;
    }
  }

  return {
    /**
     * Acquire the lock. Call after editor is ready.
     * @param {object} self - Alpine component instance (must have `lockOwner` property)
     * @param {object} callbacks
     * @param {function} callbacks.onLocked - called when file is locked by another user
     * @param {function} callbacks.onAcquired - called when lock is acquired successfully
     */
    acquire: function(self, callbacks) {
      _fetch('POST').then(function(resp) {
        if (resp.ok) {
          self.lockOwner = null;
          if (callbacks && callbacks.onAcquired) callbacks.onAcquired();
          startHeartbeat(self, callbacks && callbacks.onLocked);
        } else if (resp.status === 409) {
          resp.json().then(function(data) {
            self.lockOwner = data.locked_by ? data.locked_by.username : 'Another user';
            if (callbacks && callbacks.onLocked) callbacks.onLocked();
          });
        }
      }).catch(function(e) {
        console.error('Failed to acquire lock:', e);
      });
    },

    /**
     * Force unlock then re-acquire.
     * @param {object} self - Alpine component instance
     * @param {object} callbacks
     * @param {function} callbacks.onAcquired - called on successful re-acquire
     * @param {function} callbacks.onFailed - called if re-acquire fails
     */
    forceUnlock: function(self, callbacks) {
      _fetch('DELETE').then(function() {
        return _fetch('POST');
      }).then(function(resp) {
        if (resp.ok) {
          self.lockOwner = null;
          if (callbacks && callbacks.onAcquired) callbacks.onAcquired();
          startHeartbeat(self, callbacks && callbacks.onLocked);
        } else {
          if (callbacks && callbacks.onFailed) callbacks.onFailed();
          if (window.AppAlert) window.AppAlert.error('Failed to acquire lock');
        }
      }).catch(function(e) {
        console.error('Force unlock failed:', e);
        if (callbacks && callbacks.onFailed) callbacks.onFailed();
        if (window.AppAlert) window.AppAlert.error('Force unlock failed');
      });
    },

    /**
     * Release the lock and stop heartbeat. Call in dispose().
     */
    dispose: function() {
      stopHeartbeat();
      _fetch('DELETE').catch(function() {});
    },
  };
};
