function passwordsIndex(vaultsData) {
  return {
    vaults: vaultsData,
    viewMode: localStorage.getItem('passwords_view') || 'grid',
    search: '',
    collapsed: localStorage.getItem('passwords_sidebar_collapsed') === 'true',

    init() {
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    get filteredVaults() {
      if (!this.search) return this.vaults;
      const q = this.search.toLowerCase();
      return this.vaults.filter(v => v.name.toLowerCase().includes(q));
    },

    isUnlocked(vaultUuid) {
      return sessionStorage.getItem('vault_unlocked_' + vaultUuid) === '1';
    },

    setView(mode) {
      this.viewMode = mode;
      localStorage.setItem('passwords_view', mode);
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
