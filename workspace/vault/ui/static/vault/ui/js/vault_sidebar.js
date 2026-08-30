// The collapse state of the vault's sidebar, on both of its screens.
//
// It has its own component rather than living on the page's, for the reason
// the file browser's does: the sidebar carries x-show bindings on
// <i data-lucide> elements, Lucide replaces those nodes at boot, and a
// binding whose nearest x-data is several levels up fails its first
// evaluation against the replaced node. A scope on the sidebar itself is
// always there before the swap.
//
// Everything else the sidebar reads - the views, the countdown, the
// preferences - is inherited from the page's component, which is this one's
// ancestor.
window.vaultSidebar = function vaultSidebar() {
  const KEY = 'vault.sidebar.collapsed';

  return {
    collapsed: false,

    // Below this the sidebar is the icon rail and nothing else: the drawer
    // stays open at every width, so a narrow screen narrows it instead of
    // taking it away.
    isNarrow() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },

    init() {
      try {
        this.collapsed = window.localStorage.getItem(KEY) === 'true';
      } catch (err) {
        // Private browsing and a blocked-storage setting both throw on read.
        this.collapsed = false;
      }
      if (this.isNarrow()) this.collapsed = true;
      window
        .matchMedia('(max-width: 1023px)')
        .addEventListener('change', (event) => {
          if (event.matches) this.collapsed = true;
        });
    },

    toggleCollapse() {
      // Narrow, the rail is the only shape that fits, so the control does
      // nothing rather than producing a sidebar wider than the content.
      if (this.isNarrow()) return;
      this.collapsed = !this.collapsed;
      try {
        window.localStorage.setItem(KEY, String(this.collapsed));
      } catch (err) {
        /* the preference simply does not survive the reload */
      }
    },
  };
};
