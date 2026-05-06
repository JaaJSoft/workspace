// ── Mail Preferences ──────────────────────────────────────
window._mailPrefsDefaults = {
    density: 'normal',       // compact | normal | spacious
    previewLines: 1,         // 0 | 1 | 2
    confirmBeforeDelete: true,
    showLabels: true,
};
window._mailPrefsCache = { ...window._mailPrefsDefaults };

window._mailPrefsReady = fetch('/api/v1/settings/mail/preferences', { credentials: 'same-origin' })
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(data) {
        if (data && data.value && typeof data.value === 'object') {
            window._mailPrefsCache = { ...window._mailPrefsDefaults, ...data.value };
        }
    })
    .catch(function() {});

window.mailPreferences = function mailPreferences() {
    const API_URL = '/api/v1/settings/mail/preferences';
    let _saveTimer = null;

    return {
        prefs: { ...window._mailPrefsCache },

        async init() {
            await window._mailPrefsReady;
            this.prefs = { ...window._mailPrefsCache };
            window.addEventListener('mail:preferences-changed', function(e) {
                this.prefs = { ...e.detail };
            }.bind(this));
        },

        update(key, value) {
            this.prefs[key] = value;
            this._saveRemote();
            this._broadcast();
        },

        _broadcast() {
            window._mailPrefsCache = { ...this.prefs };
            window.dispatchEvent(new CustomEvent('mail:preferences-changed', { detail: this.prefs }));
        },

        _saveRemote() {
            clearTimeout(_saveTimer);
            _saveTimer = setTimeout(function() {
                fetch(API_URL, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                    body: JSON.stringify({ value: this.prefs }),
                }).catch(function() {});
            }.bind(this), 500);
        },
    };
};
