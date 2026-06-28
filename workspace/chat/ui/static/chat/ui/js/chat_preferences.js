// ── Chat Preferences ──────────────────────────────────────
window._chatPrefsDefaults = {
    compactConversationList: false,
    compactMessageView: false,
};
window._chatPrefsCache = { ...window._chatPrefsDefaults };

window._chatPrefsReady = fetch('/api/v1/settings/chat/preferences', { credentials: 'same-origin' })
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(data) {
        if (data && data.value && typeof data.value === 'object') {
            window._chatPrefsCache = { ...window._chatPrefsDefaults, ...data.value };
        }
    })
    .catch(function() {});

// Helper to update a single chat preference from anywhere. Mutates the cache,
// persists via the same endpoint as `chatPreferences()._saveRemote`, and
// broadcasts the change so any Alpine component listening to
// `chat:preferences-changed` re-renders.
window.updateChatPref = function updateChatPref(key, value) {
    window._chatPrefsCache = { ...window._chatPrefsCache, [key]: value };
    fetch('/api/v1/settings/chat/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ value: window._chatPrefsCache }),
        credentials: 'same-origin',
    }).catch(function() {});
    window.dispatchEvent(new CustomEvent('chat:preferences-changed', { detail: window._chatPrefsCache }));
};

window.chatPreferences = function chatPreferences() {
    const API_URL = '/api/v1/settings/chat/preferences';
    let _saveTimer = null;

    return {
        prefs: { ...window._chatPrefsCache },
        callSounds: (function () {
            const el = document.getElementById('call-sounds-enabled-data');
            return el ? JSON.parse(el.textContent) : true;
        })(),

        async init() {
            await window._chatPrefsReady;
            this.prefs = { ...window._chatPrefsCache };
            window.addEventListener('chat:preferences-changed', function(e) {
                this.prefs = { ...e.detail };
            }.bind(this));
        },

        update(key, value) {
            this.prefs[key] = value;
            this._saveRemote();
            this._broadcast();
        },

        saveCallSounds(value) {
            this.callSounds = value;
            // Apply live: the call-sounds engine reads its enabled flag from the
            // json_script seed only at init, so without this a toggle would not
            // take effect until the page reloads (the toggle and calls now live
            // on the same page).
            if (window.chatCallSounds) window.chatCallSounds.setEnabled(value);
            fetch('/api/v1/settings/chat/call_sounds', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                body: JSON.stringify({ value: value }),
            }).catch(function() {});
        },

        _broadcast() {
            window._chatPrefsCache = { ...this.prefs };
            window.dispatchEvent(new CustomEvent('chat:preferences-changed', { detail: this.prefs }));
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
