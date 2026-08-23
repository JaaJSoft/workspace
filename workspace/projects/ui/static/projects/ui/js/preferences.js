// --- Projects module preferences (per-user, cross-project) ---

// Initial values are embedded server-side via |json_script (see
// project.html), so no fetch is needed on page load.
window.projectsPreferences = function projectsPreferences() {
  let initial = { reminder_hour: 8, notify_level: 'all' };
  const el = document.getElementById('projects-prefs-data');
  if (el) {
    try { initial = { ...initial, ...JSON.parse(el.textContent) }; } catch (_) { /* keep defaults */ }
  }
  return {
    prefs: initial,
    // Per-key request generation: only the latest request for a key may
    // roll back or broadcast, so a slow failure can't clobber the state
    // of a newer request that already settled.
    _gen: {},
    update(key, value) {
      const previous = this.prefs[key];
      this.prefs[key] = value;
      const gen = (this._gen[key] = (this._gen[key] || 0) + 1);
      fetch('/api/v1/settings/projects/' + key, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ value: value }),
      }).then((resp) => {
        if (!resp.ok) throw new Error('rejected');
        if (key === 'notify_level' && gen === this._gen[key]) {
          // The header bell shows this value whenever no per-project
          // override is set; keep it in sync without a page swap.
          window.dispatchEvent(new CustomEvent('projects-notify-level-changed', { detail: { level: value } }));
        }
      }).catch(() => {
        if (gen !== this._gen[key]) return;
        this.prefs[key] = previous;
        if (window.AppAlert) window.AppAlert.error('Failed to save preference');
      });
    },
  };
};

// --- Per-project notification level (the bell in the project header) ---

window.projectNotificationLevel = function projectNotificationLevel(opts) {
  const LABELS = { all: 'All notifications', in_app: 'In-app only', none: 'Nothing' };
  return {
    override: opts.override || '',
    moduleLevel: opts.moduleLevel || 'all',
    effective() { return this.override || this.moduleLevel; },
    label(level) { return LABELS[level] || LABELS.all; },
    set(level) {
      this._send(level, () => fetch(opts.url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ level: level }),
      }));
    },
    reset() {
      this._send('', () => fetch(opts.url, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
      }));
    },
    // Same latest-request-wins guard as projectsPreferences.update; one
    // counter is enough because every request targets the same control.
    _gen: 0,
    _send(next, request) {
      const previous = this.override;
      this.override = next;
      const gen = ++this._gen;
      request().then((resp) => {
        if (!resp.ok) throw new Error('rejected');
      }).catch(() => {
        if (gen !== this._gen) return;
        this.override = previous;
        if (window.AppAlert) window.AppAlert.error('Failed to save notification level');
      });
    },
  };
};
