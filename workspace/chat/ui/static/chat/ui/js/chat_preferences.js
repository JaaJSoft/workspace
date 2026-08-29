// ── Chat Preferences ──────────────────────────────────────
window._chatPrefsDefaults = {
    compactConversationList: false,
    compactMessageView: false,
    showThreadRepliesInline: false,
    // slide | pop | fade | bounce | none - the CSS in chat.css keys the
    // entrance keyframes on this value.
    messageAnimation: 'slide',
};
// Initial prefs are embedded server-side via |json_script (index.html and
// room.html). Reading them synchronously means the first Alpine paint
// already has the right density flags - no reshuffle once a fetch lands.
(function bootChatPrefs() {
    let initial = {};
    const el = document.getElementById('chat-prefs-data');
    if (el) {
        try { initial = JSON.parse(el.textContent) || {}; } catch (e) { initial = {}; }
    }
    window._chatPrefsCache = { ...window._chatPrefsDefaults, ...initial };
})();

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

        init() {
            window.addEventListener('chat:preferences-changed', function(e) {
                this.prefs = { ...e.detail };
            }.bind(this));
        },

        update(key, value) {
            this.prefs[key] = value;
            this._saveRemote();
            this._broadcast();
        },

        // For a preference the server applies when it renders the message
        // list, so the list has to be refetched for it to take effect.
        //
        // The write cannot be debounced like update()'s: the filter runs
        // server-side, so a refetch issued before the PUT lands would come
        // back filtered by the old value. Await the write, then ask chatApp
        // for its incremental refresh - which preserves scroll position,
        // unlike a full reload.
        async updateAndSync(key, value) {
            this.prefs[key] = value;
            this._broadcast();
            clearTimeout(_saveTimer);
            let resp;
            try {
                resp = await fetch(API_URL, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                    body: JSON.stringify({ value: this.prefs }),
                    credentials: 'same-origin',
                });
            } catch (e) {
                // The toggle did not stick; skip the refetch rather than
                // repaint the list with the value the server still holds.
                console.error('Failed to save preference', e);
                return;
            }
            // fetch resolves on 4xx/5xx too - an HTTP error also means the
            // server kept the old value, so the same rule applies.
            if (!resp.ok) {
                console.error('Failed to save preference', resp.status);
                return;
            }
            window.dispatchEvent(new CustomEvent('chat:refresh-messages'));
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
