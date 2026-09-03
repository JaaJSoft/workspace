// The module sidebar's collapsed preference, one setting per module.
//
// The server sizes the <aside> from this preference before any script runs,
// and renders the same value into #sidebar-collapsed-data so a component
// seeds its `collapsed` state from what the page was painted with. Toggles
// are written back through the settings API and never awaited: the sidebar
// has already moved, and a refused write only costs the preference on the
// next load.
window.sidebarPreference = {
  initial() {
    const el = document.getElementById('sidebar-collapsed-data');
    if (!el) return false;
    try {
      return JSON.parse(el.textContent) === true;
    } catch (_) {
      return false;
    }
  },

  save(module, collapsed) {
    return fetch(`/api/v1/settings/${module}/sidebar_collapsed`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
      body: JSON.stringify({ value: collapsed === true }),
    }).catch(() => {});
  },
};
