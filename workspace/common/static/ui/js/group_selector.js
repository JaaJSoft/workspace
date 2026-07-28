/**
 * Reusable group selector Alpine.js component, the group counterpart of
 * userSelector. Filter-as-you-type over a local list with keyboard
 * navigation and event dispatch.
 *
 * Usage: x-data="groupSelector('my-event-name', () => myGroups())"
 *
 * Groups always come from a small local list (e.g. /api/v1/users/groups),
 * so there is no remote mode: the getter's list is filtered client-side on
 * every keystroke and the dropdown opens on focus with the full list. A
 * getter (not an array) so the parent component can exclude already-picked
 * groups reactively.
 */
window.groupSelector = function groupSelector(eventName, localGroups) {
  return {
    query: '',
    results: [],
    showDropdown: false,
    highlight: -1,
    eventName: eventName || 'group-selected',
    localGroups: typeof localGroups === 'function' ? localGroups : () => [],

    search() {
      const needle = (this.query || '').trim().toLowerCase();
      this.results = this.localGroups().filter(
        (g) => !needle || (g.name || '').toLowerCase().includes(needle)
      );
      this.highlight = -1;
      this.showDropdown = true;
    },

    handleKeydown(e) {
      const open = this.showDropdown && this.results.length > 0;
      if (e.key === 'ArrowDown' && open) {
        e.preventDefault();
        this.highlight = (this.highlight + 1) % this.results.length;
      } else if (e.key === 'ArrowUp' && open) {
        e.preventDefault();
        this.highlight = this.highlight <= 0 ? this.results.length - 1 : this.highlight - 1;
      } else if (e.key === 'Enter' && open && this.highlight >= 0) {
        e.preventDefault();
        this.selectGroup(this.results[this.highlight]);
      }
    },

    selectGroup(group) {
      window.dispatchEvent(new CustomEvent(this.eventName, { detail: { group } }));
      this.query = '';
      this.results = [];
      this.showDropdown = false;
      this.highlight = -1;
    },
  };
};
