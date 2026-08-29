// The preferences both vault screens carry, and the device they are set on.
//
// A mixin rather than a partial's own component: the panel it backs is
// included in a sidebar whose scope is the page's component, so the state has
// to be on that component - on both of them, identically, because the listing
// and the browser show the same panel.
//
// Methods, never getters: object spread copies values, so a `get` here would
// be evaluated once at composition and frozen.
window.vaultPrefsMixin = function vaultPrefsMixin() {
  const DEFAULTS = { lockAfterMinutes: 5, defaultSort: 'default' };

  function readJson(elementId) {
    const element = document.getElementById(elementId);
    if (!element) return null;
    try {
      return JSON.parse(element.textContent);
    } catch (err) {
      return null;
    }
  }

  function getCSRFToken() {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  return {
    prefs: Object.assign({}, DEFAULTS),

    putSetting: function (key, value) {
      return fetch('/api/v1/settings/vault/' + key, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify({ value: value }),
      }).then(function (response) {
        if (!response.ok) throw new Error('the setting was refused');
      });
    },
    loadPrefs: function () {
      const stored = readJson('vault-prefs') || {};
      this.prefs = {
        lockAfterMinutes: Number(stored.lock_after_minutes) || DEFAULTS.lockAfterMinutes,
        defaultSort: stored.default_sort || DEFAULTS.defaultSort,
      };
      this.sortField = this.prefs.defaultSort;
      window.vaultSession.setIdleTimeout(this.prefs.lockAfterMinutes);
    },
    updatePref: async function (key, value) {
      const previous = Object.assign({}, this.prefs);
      if (key === 'default_sort') {
        this.prefs.defaultSort = value;
        this.sortField = value;
      }
      if (key === 'lock_after_minutes') {
        this.prefs.lockAfterMinutes = value;
        window.vaultSession.setIdleTimeout(value);
      }
      try {
        await this.putSetting(key, value);
      } catch (err) {
        this.prefs = previous;
        this.sortField = previous.defaultSort;
        window.vaultSession.setIdleTimeout(previous.lockAfterMinutes);
        this.error = 'That preference could not be saved. Try again.';
      }
    },
    forgetDevice: async function () {
      const confirmed = await this.confirm(
        'Forget the recovery key stored in this browser? You will need your '
          + 'emergency kit the next time you unlock here.',
        { title: 'Forget the key on this device', okLabel: 'Forget it', okClass: 'btn-error' }
      );
      if (!confirmed) return;
      window.vaultSession.forgetDevice();
      this.secretRequired = true;
      this.secretRemembered = false;
      this.secretText = '';
    },
  };
};
