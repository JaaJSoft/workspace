// --- Projects module preferences (per-user, cross-project) ---

// Initial values are embedded server-side via |json_script (see
// project.html), so no fetch is needed on page load.
window.projectsPreferences = function projectsPreferences() {
  let initial = { reminder_hour: 8 };
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
      }).catch(() => {
        this.prefs[key] = previous;
        if (window.AppAlert) window.AppAlert.error('Failed to save preference');
      });
    },
  };
};
