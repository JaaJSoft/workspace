// The module sidebar's collapsed preference, one setting per module.
//
// The server sizes the <aside> from this preference before any script runs,
// and renders the same value into #sidebar-collapsed-data so a component
// seeds its `collapsed` state from what the page was painted with. Toggles
// are written back through the settings API and never awaited: the sidebar
// has already moved, and a refused write only costs the preference on the
// next load.
window.sidebarPreference = {
  // Below Tailwind's `lg`, where a module drawer leaves the page.
  narrowQuery: '(max-width: 1023px)',

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
    return window.saveUserSetting(module, 'sidebar_collapsed', collapsed === true).catch(() => {});
  },

  // The collapse state of a sidebar whose drawer is off-canvas below `lg`
  // (`lg:drawer-open`), spread into the module's component. The preference
  // is a desktop one: an opened drawer is the full sidebar whatever it says,
  // since a 64px icon rail is no use on a phone.
  drawerMixin(module) {
    return {
      collapsed: window.sidebarPreference.initial(),

      isMobile() {
        return window.matchMedia(window.sidebarPreference.narrowQuery).matches;
      },

      sidebarCollapsed() {
        return this.isMobile() ? false : this.collapsed;
      },

      toggleCollapse() {
        if (this.isMobile()) return;
        this.collapsed = !this.collapsed;
        window.sidebarPreference.save(module, this.collapsed);
      },
    };
  },
};
