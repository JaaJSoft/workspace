/**
 * Reusable user selector Alpine.js component.
 * Provides search-as-you-type with avatars, keyboard navigation, and event dispatch.
 *
 * Usage: x-data="userSelector('my-event-name')"
 *
 * Remote mode (default) searches /api/v1/users/search. Passing a function as
 * `localUsers` switches to local mode: the getter's list is filtered
 * client-side on every keystroke (no minimum query length) and the dropdown
 * opens on focus with the full list. A getter (not an array) so the parent
 * component can exclude already-picked users reactively.
 */
window.userSelector = function userSelector(eventName, localUsers) {
  return {
    query: '',
    results: [],
    loading: false,
    showDropdown: false,
    highlight: -1,
    eventName: eventName || 'user-selected',
    localUsers: typeof localUsers === 'function' ? localUsers : null,

    matchesQuery(user, needle) {
      if (!needle) return true;
      const name = ((user.first_name || '') + ' ' + (user.last_name || '')).toLowerCase();
      return (
        (user.username || '').toLowerCase().includes(needle) || name.includes(needle)
      );
    },

    searchLocal(q) {
      const needle = q.toLowerCase();
      this.results = this.localUsers().filter((u) => this.matchesQuery(u, needle));
      this.highlight = -1;
      this.showDropdown = true;
    },

    openLocal() {
      if (this.localUsers) this.searchLocal((this.query || '').trim());
    },

    async search() {
      const q = (this.query || '').trim();
      if (this.localUsers) {
        this.searchLocal(q);
        return;
      }
      if (q.length < 2) {
        this.results = [];
        this.showDropdown = false;
        this.highlight = -1;
        return;
      }
      this.loading = true;
      try {
        const resp = await fetch(`/api/v1/users/search?q=${encodeURIComponent(q)}&limit=10`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          const data = await resp.json();
          this.results = data.results || [];
          this.highlight = -1;
          this.showDropdown = true;
        }
      } catch (e) {
        this.results = [];
      } finally {
        this.loading = false;
      }
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
        this.selectUser(this.results[this.highlight]);
      }
    },

    selectUser(user) {
      window.dispatchEvent(new CustomEvent(this.eventName, { detail: { user } }));
      this.query = '';
      this.results = [];
      this.showDropdown = false;
      this.highlight = -1;
    },
  };
};
