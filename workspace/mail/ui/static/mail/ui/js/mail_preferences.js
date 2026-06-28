// ── Mail Preferences ──────────────────────────────────────
window._mailPrefsDefaults = {
    density: 'normal',       // compact | normal | spacious
    previewLines: 1,         // 0 | 1 | 2
    confirmBeforeDelete: true,
    showLabels: true,
    rulesCompact: false,     // compact rule rows in the rules dialog
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

// Helper to update a single mail preference from anywhere (not just the
// preferences dialog). Mutates the cache, persists via the same endpoint as
// `mailPreferences()._saveRemote`, and broadcasts the change so any Alpine
// component listening to `mail:preferences-changed` re-renders.
window.updateMailPref = function updateMailPref(key, value) {
    window._mailPrefsCache = { ...window._mailPrefsCache, [key]: value };
    fetch('/api/v1/settings/mail/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ value: window._mailPrefsCache }),
        credentials: 'same-origin',
    }).catch(function() {});
    window.dispatchEvent(new CustomEvent('mail:preferences-changed', { detail: window._mailPrefsCache }));
};

window.mailPreferences = function mailPreferences() {
    const API_URL = '/api/v1/settings/mail/preferences';
    let _saveTimer = null;

    return {
        prefs: { ...window._mailPrefsCache },
        ai: (function () {
            const el = document.getElementById('mail-ai-features-data');
            return el ? JSON.parse(el.textContent) : { classify: true, extract: true, manual: true };
        })(),

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

        saveAiFeature(feature, value) {
            this.ai[feature] = value;
            fetch('/api/v1/settings/mail/ai_' + feature, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                body: JSON.stringify({ value: value }),
            }).catch(function() {});
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
