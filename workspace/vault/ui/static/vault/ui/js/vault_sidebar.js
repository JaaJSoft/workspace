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

    init() {
      try {
        this.collapsed = window.localStorage.getItem(KEY) === 'true';
      } catch (err) {
        // Private browsing and a blocked-storage setting both throw on read.
        this.collapsed = false;
      }
    },

    toggleCollapse() {
      this.collapsed = !this.collapsed;
      try {
        window.localStorage.setItem(KEY, String(this.collapsed));
      } catch (err) {
        /* the preference simply does not survive the reload */
      }
    },
  };
};
