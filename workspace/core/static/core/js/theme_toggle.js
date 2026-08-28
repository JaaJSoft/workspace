// Theme toggle: server-rendered <html data-theme> is the source of truth.
// The toggle swaps between the user's preferred light theme (data-light-theme)
// and dark theme (data-dark-theme), then persists the active theme via the
// user-settings API.
//
// All three attributes are read live on every event - the preferences page
// (settings_appearance.html) mutates them when the user picks a new theme,
// and we want the toggle to reflect those changes without a page reload.
(function () {
  const themeToggle = document.getElementById('themeToggle');
  if (!themeToggle) return;
  const html = document.documentElement;

  function getDarkTheme() { return html.getAttribute('data-dark-theme') || 'dark'; }
  function getLightTheme() { return html.getAttribute('data-light-theme') || 'light'; }
  function getCurrentTheme() { return html.getAttribute('data-theme') || getLightTheme(); }

  function syncChecked() {
    themeToggle.checked = getCurrentTheme() === getDarkTheme();
  }

  syncChecked();

  // Watch the <html> element so the toggle stays in sync when the
  // preferences page changes data-theme / data-dark-theme / data-light-theme.
  new MutationObserver(syncChecked).observe(html, {
    attributes: true,
    attributeFilter: ['data-theme', 'data-light-theme', 'data-dark-theme'],
  });

  themeToggle.addEventListener('change', function () {
    const newTheme = this.checked ? getDarkTheme() : getLightTheme();
    html.setAttribute('data-theme', newTheme);
    fetch('/api/v1/settings/core/theme', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
      body: JSON.stringify({ value: newTheme }),
    }).catch(() => {});
  });
})();
