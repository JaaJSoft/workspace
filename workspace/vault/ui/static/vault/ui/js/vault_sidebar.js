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
  const NARROW = '(max-width: 1023px)';

  const preferred = window.sidebarPreference.initial();

  return {
    // What the user chose, kept apart from what is on screen: a narrow
    // window forces the rail without that becoming the preference, and
    // widening again has to know what to go back to.
    preferred: preferred,
    // The drawer stays open at every width, so below `lg` the sidebar is the
    // icon rail and nothing else; above it, the preference the server sized
    // the <aside> with.
    collapsed: window.matchMedia(NARROW).matches || preferred,

    isNarrow() {
      return window.matchMedia(NARROW).matches;
    },

    init() {
      window.matchMedia(NARROW).addEventListener('change', (event) => {
        this.collapsed = event.matches || this.preferred;
      });
    },

    toggleCollapse() {
      // Narrow, the rail is the only shape that fits, so the control does
      // nothing rather than producing a sidebar wider than the content.
      if (this.isNarrow()) return;
      this.collapsed = !this.collapsed;
      this.preferred = this.collapsed;
      window.sidebarPreference.save('vault', this.collapsed);
    },
  };
};
