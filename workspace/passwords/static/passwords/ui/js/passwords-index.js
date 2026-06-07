function passwordsIndex(vaultsData) {
  return {
    vaults: vaultsData,
    viewMode: localStorage.getItem('passwords_view') || 'grid',
    search: '',
    activeFilter: 'all',   // 'all' | 'favorites' | 'recent' | 'shared'
    collapsed: localStorage.getItem('passwords_sidebar_collapsed') === 'true',

    init() {
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    get filteredVaults() {
      let vaults = this.vaults;

      // Apply sidebar filter
      if (this.activeFilter === 'favorites') {
        vaults = vaults.filter(v => v.is_favorite);
      } else if (this.activeFilter === 'recent') {
        // Sort by updated_at descending, show all
        vaults = [...vaults].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
      } else if (this.activeFilter === 'shared') {
        vaults = vaults.filter(v => !v.is_owner);
      }

      // Apply search on top
      if (this.search) {
        const q = this.search.toLowerCase();
        vaults = vaults.filter(v => v.name.toLowerCase().includes(q));
      }

      return vaults;
    },

    isUnlocked(vaultUuid) {
      return sessionStorage.getItem('vault_unlocked_' + vaultUuid) === '1';
    },

    setViewMode(mode) {
      this.viewMode = mode;
      localStorage.setItem('passwords_view', mode);
    },

    setFilter(filter) {
      this.activeFilter = filter;
    },

    toggleCollapse() {
      this.collapsed = !this.collapsed;
      localStorage.setItem('passwords_sidebar_collapsed', this.collapsed);
    },

    openCreateVault() {
      document.getElementById('create-vault-dialog').showModal();
    },
  };
}
