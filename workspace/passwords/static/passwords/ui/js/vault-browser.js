// vault-browser.js — Alpine component for the vault detail page.
// Depends on vault-crypto.js (VaultCrypto) being loaded first.

function vaultBrowser(vaultData) {
  return {
    // ── Vault ────────────────────────────────────────────────────────────────
    vault: vaultData,
    vaultKey: null,          // CryptoKey | null — never serialized

    // ── Raw data (server) ────────────────────────────────────────────────────
    rawEntries: [],
    folders: [],             // { uuid, vault, parent, name, icon, color, order }

    // ── Decrypted data ───────────────────────────────────────────────────────
    // Each entry: { ...serverFields, name, username, password, totp_secret }
    entries: [],

    // ── Sidebar ──────────────────────────────────────────────────────────────
    collapsed: localStorage.getItem('passwords_vault_sidebar_collapsed') === 'true',
    sidebarView: 'all',      // 'all' | 'trash'

    // ── Folder navigation ────────────────────────────────────────────────────
    currentFolderUuid: null,
    folderHistory: [null],
    folderHistoryIndex: 0,

    // ── Filters & sort ───────────────────────────────────────────────────────
    search: '',
    typeFilter: 'all',       // 'all' | 'folders' | 'entries' | 'favorites'
    sortField: 'default',    // 'default' | 'name' | 'created' | 'modified' | 'favorite'
    sortDir: 'asc',

    // ── View mode ────────────────────────────────────────────────────────────
    viewMode: localStorage.getItem('passwords_vault_view_mode') || 'list',
    mosaicTileSize: parseInt(localStorage.getItem('passwords_vault_tile_size') || '3'),

    // ── Selection ────────────────────────────────────────────────────────────
    selectedUuids: new Set(),
    hoveredUuid: null,

    // ── Properties panel ─────────────────────────────────────────────────────
    activeEntry: null,
    showPropertiesPanel: false,
    showPasswordInPanel: false,

    // ── Context menu ─────────────────────────────────────────────────────────
    contextMenu: { open: false, x: 0, y: 0, target: null },

    // ── Move-to-folder dialog ─────────────────────────────────────────────────
    movePicker: { open: false, entryUuid: null },

    // ── Rename folder dialog ──────────────────────────────────────────────────
    renameDialog: { open: false, uuid: null, name: '' },

    // ── Action loading indicators ─────────────────────────────────────────────
    actionLoading: {},       // uuid -> bool

    // ═════════════════════════════════════════════════════════════════════════
    // Computed
    // ═════════════════════════════════════════════════════════════════════════

    get locked() { return this.vaultKey === null; },

    get canGoBack() { return this.folderHistoryIndex > 0; },

    get canGoForward() { return this.folderHistoryIndex < this.folderHistory.length - 1; },

    get parentFolderUuid() {
      if (!this.currentFolderUuid) return null;
      const f = this.folders.find(x => x.uuid === this.currentFolderUuid);
      return f ? f.parent : null;
    },

    get breadcrumbs() {
      const crumbs = [{ uuid: null, name: this.vault.name }];
      if (!this.currentFolderUuid) return crumbs;
      const path = [];
      let uuid = this.currentFolderUuid;
      while (uuid) {
        const f = this.folders.find(x => x.uuid === uuid);
        if (!f) break;
        path.unshift({ uuid: f.uuid, name: f.name });
        uuid = f.parent;
      }
      return [...crumbs, ...path];
    },

    get currentFolderName() {
      if (!this.currentFolderUuid) return 'All entries';
      const f = this.folders.find(x => x.uuid === this.currentFolderUuid);
      return f ? f.name : 'Unknown folder';
    },

    get currentFolderItems() {
      let items = [];

      if (this.sidebarView === 'trash') {
        items = this.entries
          .filter(e => e.deleted_at)
          .map(e => ({ ...e, nodeType: 'entry' }));
      } else if (this.sidebarView === 'favorites') {
        items = this.entries
          .filter(e => !e.deleted_at && e.is_favorite)
          .map(e => ({ ...e, nodeType: 'entry' }));
      } else {
        const folderItems = this.folders
          .filter(f => f.parent === this.currentFolderUuid)
          .map(f => ({ ...f, nodeType: 'folder' }));
        const entryItems = this.entries
          .filter(e => !e.deleted_at && e.folder === this.currentFolderUuid)
          .map(e => ({ ...e, nodeType: 'entry' }));
        items = [...folderItems, ...entryItems];
      }

      // Type filter
      if (this.typeFilter === 'folders') {
        items = items.filter(i => i.nodeType === 'folder');
      } else if (this.typeFilter === 'entries') {
        items = items.filter(i => i.nodeType === 'entry');
      } else if (this.typeFilter === 'favorites') {
        items = items.filter(i => i.is_favorite);
      }

      // Search
      if (this.search) {
        const q = this.search.toLowerCase();
        items = items.filter(i => (i.name || '').toLowerCase().includes(q));
      }

      // Sort
      if (this.sortField !== 'default') {
        items = [...items].sort((a, b) => {
          if (a.nodeType !== b.nodeType) return a.nodeType === 'folder' ? -1 : 1;
          let av, bv;
          switch (this.sortField) {
            case 'name':     av = a.name || '';       bv = b.name || '';       break;
            case 'created':  av = a.created_at || ''; bv = b.created_at || ''; break;
            case 'modified': av = a.updated_at || ''; bv = b.updated_at || ''; break;
            case 'favorite': av = a.is_favorite ? 1 : 0; bv = b.is_favorite ? 1 : 0; break;
            default: return 0;
          }
          const cmp = typeof av === 'string' ? av.localeCompare(bv) : (av < bv ? -1 : av > bv ? 1 : 0);
          return this.sortDir === 'asc' ? cmp : -cmp;
        });
      } else {
        // Default: folders first, then by name
        items = [...items].sort((a, b) => {
          if (a.nodeType !== b.nodeType) return a.nodeType === 'folder' ? -1 : 1;
          return (a.name || '').localeCompare(b.name || '');
        });
      }

      return items;
    },

    get statusCounts() {
      // Counts BEFORE filters — total in current folder/view
      if (this.sidebarView === 'trash') {
        const trashCount = this.entries.filter(e => e.deleted_at).length;
        return { total: trashCount, folders: 0, entries: trashCount };
      }
      if (this.sidebarView === 'favorites') {
        const favCount = this.entries.filter(e => !e.deleted_at && e.is_favorite).length;
        return { total: favCount, folders: 0, entries: favCount };
      }
      const fCount = this.folders.filter(f => f.parent === this.currentFolderUuid).length;
      const eCount = this.entries.filter(e => !e.deleted_at && e.folder === this.currentFolderUuid).length;
      return { total: fCount + eCount, folders: fCount, entries: eCount };
    },

    // ═════════════════════════════════════════════════════════════════════════
    // Lifecycle
    // ═════════════════════════════════════════════════════════════════════════

    async init() {
      await this.loadFolders();
      this._refreshIcons();
    },

    _refreshIcons() {
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    // ═════════════════════════════════════════════════════════════════════════
    // Data loading
    // ═════════════════════════════════════════════════════════════════════════

    async loadFolders() {
      try {
        const r = await fetch(`/api/v1/passwords/vaults/${this.vault.uuid}/folders`);
        if (r.ok) this.folders = await r.json();
      } catch {}
    },

    async loadEntries() {
      if (!this.vaultKey) return;
      try {
        const r = await fetch(`/api/v1/passwords/entries?vault=${this.vault.uuid}`);
        if (!r.ok) return;
        this.rawEntries = await r.json();
        await this._decryptEntries();
        this._refreshIcons();
      } catch {}
    },

    async _decryptEntries() {
      const out = [];
      for (const e of this.rawEntries) {
        const dec = async (field) => field ? (await VaultCrypto.aesDecrypt(this.vaultKey, field) || '') : '';
        const name     = await dec(e.encrypted_name)     || '[decryption failed]';
        const username = await dec(e.encrypted_username);
        const password = await dec(e.encrypted_password);
        const totp_secret = await dec(e.encrypted_totp_secret);
        out.push({ ...e, name, username, password, totp_secret });
      }
      this.entries = out;
    },

    // ═════════════════════════════════════════════════════════════════════════
    // Lock / Unlock
    // ═════════════════════════════════════════════════════════════════════════

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
      this.activeEntry = null;
      this.showPropertiesPanel = false;
      sessionStorage.removeItem('vault_unlocked_' + this.vault.uuid);
    },

    // ═════════════════════════════════════════════════════════════════════════
    // Navigation
    // ═════════════════════════════════════════════════════════════════════════

    navigateToFolder(uuid) {
      // Truncate forward history on new navigation
      this.folderHistory = this.folderHistory.slice(0, this.folderHistoryIndex + 1);
      this.folderHistory.push(uuid);
      this.folderHistoryIndex++;
      this.currentFolderUuid = uuid;
      this.selectedUuids = new Set();
      this.search = '';
      this._refreshIcons();
    },

    goBack() {
      if (!this.canGoBack) return;
      this.folderHistoryIndex--;
      this.currentFolderUuid = this.folderHistory[this.folderHistoryIndex];
      this.selectedUuids = new Set();
      this._refreshIcons();
    },

    goForward() {
      if (!this.canGoForward) return;
      this.folderHistoryIndex++;
      this.currentFolderUuid = this.folderHistory[this.folderHistoryIndex];
      this.selectedUuids = new Set();
      this._refreshIcons();
    },

    goUp() {
      this.navigateToFolder(this.parentFolderUuid);
    },

    setSidebarView(view) {
      this.sidebarView = view;
      this.currentFolderUuid = null;
      this.folderHistory = [null];
      this.folderHistoryIndex = 0;
      this.search = '';
      this.selectedUuids = new Set();
      this.closePropertiesPanel();
      this._refreshIcons();
    },

    toggleCollapse() {
      this.collapsed = !this.collapsed;
      localStorage.setItem('passwords_vault_sidebar_collapsed', this.collapsed);
    },

    // ═════════════════════════════════════════════════════════════════════════
    // View mode
    // ═════════════════════════════════════════════════════════════════════════

    setViewMode(mode) {
      this.viewMode = mode;
      localStorage.setItem('passwords_vault_view_mode', mode);
      this._refreshIcons();
    },

    setMosaicTileSize(size) {
      this.mosaicTileSize = size;
      localStorage.setItem('passwords_vault_tile_size', size);
    },

    tileMinWidth() { return [100, 140, 180, 220, 260][this.mosaicTileSize - 1] || 180; },
    tileGap()      { return this.mosaicTileSize <= 2 ? 6 : 12; },
    tileIconSize() { return [24, 32, 40, 52, 64][this.mosaicTileSize - 1] || 40; },

    // ═════════════════════════════════════════════════════════════════════════
    // Filters
    // ═════════════════════════════════════════════════════════════════════════

    resetAll() {
      this.search = '';
      this.typeFilter = 'all';
      this.sortField = 'default';
      this.sortDir = 'asc';
    },

    toggleSortDir() {
      this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
    },

    // ═════════════════════════════════════════════════════════════════════════
    // Selection
    // ═════════════════════════════════════════════════════════════════════════

    isSelected(uuid) { return this.selectedUuids.has(uuid); },

    toggleSelection(uuid) {
      const next = new Set(this.selectedUuids);
      next.has(uuid) ? next.delete(uuid) : next.add(uuid);
      this.selectedUuids = next;
    },

    clearSelection() { this.selectedUuids = new Set(); },

    // ═════════════════════════════════════════════════════════════════════════
    // Properties panel
    // ═════════════════════════════════════════════════════════════════════════

    openPropertiesPanel(entry) {
      this.activeEntry = entry;
      this.showPropertiesPanel = true;
      this.showPasswordInPanel = false;
    },

    closePropertiesPanel() {
      this.showPropertiesPanel = false;
      this.activeEntry = null;
      this.showPasswordInPanel = false;
    },

    async copyToClipboard(text) {
      await navigator.clipboard.writeText(text);
      // Clear clipboard after 30 s
      setTimeout(() => navigator.clipboard.writeText(''), 30000);
    },

    // ═════════════════════════════════════════════════════════════════════════
    // Context menu
    // ═════════════════════════════════════════════════════════════════════════

    openContextMenu(event, target) {
      event.preventDefault();
      this.$nextTick(() => {
        const menuW = 256, menuH = 220;
        const x = Math.min(event.clientX, window.innerWidth - menuW - 8);
        const y = Math.min(event.clientY, window.innerHeight - menuH - 8);
        this.contextMenu = { open: true, x, y, target };
      });
    },

    closeContextMenu() {
      this.contextMenu = { ...this.contextMenu, open: false, target: null };
    },

    // ═════════════════════════════════════════════════════════════════════════
    // API helpers
    // ═════════════════════════════════════════════════════════════════════════

    isActionLoading(uuid) { return !!this.actionLoading[uuid]; },

    _setLoading(uuid, val) {
      this.actionLoading = val
        ? { ...this.actionLoading, [uuid]: true }
        : Object.fromEntries(Object.entries(this.actionLoading).filter(([k]) => k !== uuid));
    },

    async _patchEntry(uuid, fields) {
      return fetch(`/api/v1/passwords/entries/${uuid}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify(fields),
      });
    },

    // ═════════════════════════════════════════════════════════════════════════
    // Entry actions
    // ═════════════════════════════════════════════════════════════════════════

    async toggleFavorite(uuid, currentValue) {
      this._setLoading(uuid, true);
      try {
        const r = await this._patchEntry(uuid, { is_favorite: !currentValue });
        if (r.ok) {
          this.entries = this.entries.map(e =>
            e.uuid === uuid ? { ...e, is_favorite: !currentValue } : e
          );
          if (this.activeEntry?.uuid === uuid) {
            this.activeEntry = { ...this.activeEntry, is_favorite: !currentValue };
          }
        }
      } finally { this._setLoading(uuid, false); }
    },

    async trashEntry(uuid) {
      this._setLoading(uuid, true);
      try {
        const r = await fetch(`/api/v1/passwords/entries/${uuid}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (r.ok) {
          const now = new Date().toISOString();
          this.entries = this.entries.map(e =>
            e.uuid === uuid ? { ...e, deleted_at: now } : e
          );
          if (this.activeEntry?.uuid === uuid) this.closePropertiesPanel();
        }
      } finally { this._setLoading(uuid, false); }
    },

    async moveEntryToFolder(uuid, folderUuid) {
      this._setLoading(uuid, true);
      try {
        const r = await this._patchEntry(uuid, { folder: folderUuid });
        if (r.ok) {
          this.entries = this.entries.map(e =>
            e.uuid === uuid ? { ...e, folder: folderUuid } : e
          );
          if (this.activeEntry?.uuid === uuid) {
            this.activeEntry = { ...this.activeEntry, folder: folderUuid };
          }
        }
      } finally {
        this._setLoading(uuid, false);
        this.movePicker = { open: false, entryUuid: null };
      }
    },

    // ═════════════════════════════════════════════════════════════════════════
    // Folder actions
    // ═════════════════════════════════════════════════════════════════════════

    openRenameDialog(uuid, currentName) {
      this.renameDialog = { open: true, uuid, name: currentName };
      this.closeContextMenu();
    },

    async submitRenameFolder() {
      const { uuid, name } = this.renameDialog;
      if (!uuid || !name.trim()) return;
      const r = await fetch(`/api/v1/passwords/folders/${uuid}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (r.ok) {
        this.folders = this.folders.map(f =>
          f.uuid === uuid ? { ...f, name: name.trim() } : f
        );
      }
      this.renameDialog = { open: false, uuid: null, name: '' };
    },

    async deleteFolder(uuid) {
      const r = await fetch(`/api/v1/passwords/folders/${uuid}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (r.ok) {
        this.folders = this.folders.filter(f => f.uuid !== uuid);
        if (this.currentFolderUuid === uuid) this.navigateToFolder(null);
      }
      this.closeContextMenu();
    },

    // ═════════════════════════════════════════════════════════════════════════
    // Create (called from dialogs)
    // ═════════════════════════════════════════════════════════════════════════

    async createEntry(payload) {
      if (!this.vaultKey) return null;
      const enc = async (val) => VaultCrypto.aesEncrypt(this.vaultKey, val || '');
      const body = {
        vault: this.vault.uuid,
        encrypted_name:         await enc(payload.name),
        encrypted_username:     await enc(payload.username),
        encrypted_password:     await enc(payload.password),
        encrypted_totp_secret:  await enc(''),
        uris: payload.url ? [{ uri: payload.url }] : [],
        icon: payload.icon || 'key-round',
        folder: payload.folder || this.currentFolderUuid || null,
      };
      const r = await fetch('/api/v1/passwords/entries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify(body),
      });
      if (!r.ok) return null;
      const data = await r.json();
      await this.loadEntries();
      return data;
    },

    async createFolder(name) {
      const r = await fetch(`/api/v1/passwords/vaults/${this.vault.uuid}/folders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ name, parent: this.currentFolderUuid }),
      });
      if (r.ok) await this.loadFolders();
      this._refreshIcons();
    },
  };
}
