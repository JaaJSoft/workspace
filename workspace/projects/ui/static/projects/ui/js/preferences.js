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
    update(key, value) {
      const previous = this.prefs[key];
      this.prefs[key] = value;
      fetch('/api/v1/settings/projects/' + key, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ value: value }),
      }).then((resp) => {
        if (!resp.ok) throw new Error('rejected');
        if (key === 'notify_level') {
          // The header bell shows this value whenever no per-project
          // override is set; keep it in sync without a page swap.
          window.dispatchEvent(new CustomEvent('projects-notify-level-changed', { detail: { level: value } }));
        }
      }).catch(() => {
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
    _send(next, request) {
      const previous = this.override;
      this.override = next;
      request().then((resp) => {
        if (!resp.ok) throw new Error('rejected');
      }).catch(() => {
        this.override = previous;
        if (window.AppAlert) window.AppAlert.error('Failed to save notification level');
      });
    },
  };
};
