/**
 * Reusable user selector Alpine.js component.
 * Provides search-as-you-type with avatars, keyboard navigation, and event dispatch.
 *
 * Usage: x-data="userSelector('my-event-name')"
 */
window.userSelector = function userSelector(eventName) {
  return {
    query: '',
    results: [],
    loading: false,
    showDropdown: false,
    highlight: -1,
    eventName: eventName || 'user-selected',

    async search() {
      const q = (this.query || '').trim();
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
