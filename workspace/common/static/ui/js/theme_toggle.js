// Theme toggle: server-rendered <html data-theme> is the source of truth.
// The toggle persists changes via the user-settings API.
(function () {
  const themeToggle = document.getElementById('themeToggle');
  if (!themeToggle) return;
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  themeToggle.checked = current === 'dark';

  themeToggle.addEventListener('change', function () {
    const newTheme = this.checked ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', newTheme);
    fetch('/api/v1/settings/core/theme', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
      body: JSON.stringify({ value: newTheme }),
    }).catch(() => {});
  });
})();
