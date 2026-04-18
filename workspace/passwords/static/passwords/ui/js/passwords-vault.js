function passwordsVault(vaultData) {
  return {
    vault: vaultData,
    vaultKey: null,        // CryptoKey | null — never serialized
    entries: [],           // decrypted entry objects
    rawEntries: [],        // encrypted entries from server
    folders: [],           // folder list from server
    activeView: 'all',     // 'all' | 'folder' | 'favorites' | 'trash'
    activeFolder: null,
    search: '',
    sortBy: 'name',
    expandedFolders: [],
    collapsed: localStorage.getItem('passwords_vault_sidebar_collapsed') === 'true',

    get locked() { return this.vaultKey === null; },

    get filteredEntries() {
      let list = this.entries;
      if (this.search) {
        const q = this.search.toLowerCase();
        list = list.filter(e =>
          (e.name || '').toLowerCase().includes(q) ||
          (e.username || '').toLowerCase().includes(q)
        );
      }
      if (this.sortBy === 'name') {
        list = [...list].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
      }
      return list;
    },

    get sidebarFolders() {
      const buildTree = (items, parentId = null) =>
        items
          .filter(f => f.parent === parentId)
          .map(f => ({ ...f, children: buildTree(items, f.uuid) }));
      return buildTree(this.folders);
    },

    async init() {
      await this.loadFolders();
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    async loadFolders() {
      try {
        const resp = await fetch(`/api/v1/passwords/vaults/${this.vault.uuid}/folders`);
        if (resp.ok) this.folders = await resp.json();
      } catch {}
    },

    async unlock(masterPassword) {
      const raw = await VaultCrypto.unlockVault(
        masterPassword,
        this.vault.kdf_salt,
        this.vault.kdf_iterations,
        this.vault.protected_vault_key
      );
      if (!raw) return false;
      this.vaultKey = await VaultCrypto.importVaultKey(raw);
      sessionStorage.setItem('vault_unlocked_' + this.vault.uuid, '1');
      await this.loadEntries();
      return true;
    },

    lockVault() {
      this.vaultKey = null;
      this.entries = [];
      this.rawEntries = [];
      sessionStorage.removeItem('vault_unlocked_' + this.vault.uuid);
    },

    async loadEntries() {
      if (!this.vaultKey) return;
      const params = new URLSearchParams({ vault: this.vault.uuid });
      if (this.activeView === 'trash') params.set('trashed', 'true');
      if (this.activeView === 'favorites') params.set('favorites', 'true');
      const resp = await fetch(`/api/v1/passwords/entries?${params}`);
      if (!resp.ok) return;
      this.rawEntries = await resp.json();
      await this.decryptEntries();
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    async decryptEntries() {
      const decrypted = [];
      for (const e of this.rawEntries) {
        const name = await VaultCrypto.aesDecrypt(this.vaultKey, e.encrypted_name);
        const username = e.encrypted_username
          ? await VaultCrypto.aesDecrypt(this.vaultKey, e.encrypted_username)
          : '';
        decrypted.push({ ...e, name: name || '[decryption failed]', username: username || '' });
      }
      this.entries = decrypted;
    },

    setView(view, folderUuid = null) {
      this.activeView = view;
      this.activeFolder = folderUuid;
      this.search = '';
      if (!this.locked) this.loadEntries();
    },

    toggleFolderExpand(uuid) {
      const idx = this.expandedFolders.indexOf(uuid);
      if (idx === -1) this.expandedFolders.push(uuid);
      else this.expandedFolders.splice(idx, 1);
    },

    async createEntry(payload) {
      if (!this.vaultKey) return null;
      const body = {
        vault: this.vault.uuid,
        encrypted_name: await VaultCrypto.aesEncrypt(this.vaultKey, payload.name || ''),
        encrypted_username: await VaultCrypto.aesEncrypt(this.vaultKey, payload.username || ''),
        encrypted_password: await VaultCrypto.aesEncrypt(this.vaultKey, payload.password || ''),
        uris: payload.url ? [{ uri: payload.url }] : [],
        icon: payload.icon || 'key-round',
      };
      if (payload.folder) body.folder = payload.folder;
      const resp = await fetch('/api/v1/passwords/entries', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '',
        },
        body: JSON.stringify(body),
      });
      if (!resp.ok) return null;
      await this.loadEntries();
      return resp.json();
    },

    async copyPassword(entry) {
      if (!this.vaultKey || !entry.encrypted_password) return;
      const plain = await VaultCrypto.aesDecrypt(this.vaultKey, entry.encrypted_password);
      if (!plain) return;
      await navigator.clipboard.writeText(plain);
      setTimeout(() => navigator.clipboard.writeText(''), 30000);
    },

    async deleteEntry(entryUuid) {
      await fetch(`/api/v1/passwords/entries/${entryUuid}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '' },
      });
      this.entries = this.entries.filter(e => e.uuid !== entryUuid);
    },

    async createFolder(name) {
      const resp = await fetch(`/api/v1/passwords/vaults/${this.vault.uuid}/folders`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '',
        },
        body: JSON.stringify({ name }),
      });
      if (resp.ok) await this.loadFolders();
    },

    toggleCollapse() {
      this.collapsed = !this.collapsed;
      localStorage.setItem('passwords_vault_sidebar_collapsed', this.collapsed);
    },
  };
}
