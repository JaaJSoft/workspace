// What the browser shows, decided over data that is already in the clear.
//
// The store never decrypts and never talks to the network: the controller
// hands it rows whose names and logins have been opened, and it answers what
// belongs on screen. Keeping the two apart is what makes filtering, sorting
// and navigation testable without a browser, a key or a server - and it is
// also the boundary that keeps a secret out of here, because nothing in this
// file has any business holding one.
//
// A row reaching the store looks like:
//   folder { uuid, parent, name }
//   tag    { uuid, name, color }
//   entry  { uuid, folder, name, username, tags: [uuid], favorite, trashed,
//            type, modified }
// A password is deliberately absent: it is opened at the moment it is copied
// and never stored.

window.vaultStore = function vaultStore() {
  return {
    folders: [],
    tags: [],
    entries: [],
    // Entries whose signature did not verify never reach `entries`. They are
    // counted instead: the banner says how many, and nothing about them is
    // rendered, because rendering unverified data is the defect the count
    // exists to report.
    tamperedCount: 0,

    view: 'all',
    folderUuid: null,
    tagFilter: null,
    history: [{ view: 'all', folder: null, tag: null }],
    historyIndex: 0,

    search: '',
    typeFilter: 'all',
    sortField: 'default',
    sortDir: 'asc',
    viewMode: 'list',

    selected: [],

    setData(data) {
      this.folders = data.folders || [];
      this.tags = data.tags || [];
      this.entries = data.entries || [];
      this.tamperedCount = data.tamperedCount || 0;
      this.selected = [];
    },

    // ---- structure -------------------------------------------------------

    tagById(uuid) {
      return this.tags.find((tag) => tag.uuid === uuid) || null;
    },

    folderById(uuid) {
      return this.folders.find((folder) => folder.uuid === uuid) || null;
    },

    tagCount(uuid) {
      return this.entries.filter(
        (entry) => !entry.trashed && entry.tags.includes(uuid),
      ).length;
    },

    trashCount() {
      return this.entries.filter((entry) => entry.trashed).length;
    },

    // The trail is built here rather than by the server, which has never
    // seen a folder's name: they arrive encrypted and are opened by the
    // client that holds the key.
    breadcrumbs(rootName) {
      const trail = [{ uuid: null, name: rootName, root: true }];
      if (this.view !== 'all' || this.tagFilter) return trail;
      const walk = (uuid) => {
        const folder = this.folderById(uuid);
        if (!folder) return;
        if (folder.parent) walk(folder.parent);
        trail.push({ uuid: folder.uuid, name: folder.name, root: false });
      };
      walk(this.folderUuid);
      return trail;
    },

    // ---- navigation ------------------------------------------------------

    push(state) {
      // Anything ahead of the cursor is dropped: navigating after going back
      // starts a new future, exactly as a browser's own history does.
      this.history = this.history.slice(0, this.historyIndex + 1);
      this.history.push(state);
      this.historyIndex = this.history.length - 1;
      this.apply(state);
    },

    apply(state) {
      this.view = state.view;
      this.folderUuid = state.folder;
      this.tagFilter = state.tag;
      this.selected = [];
    },

    openFolder(uuid) {
      this.push({ view: 'all', folder: uuid, tag: null });
    },

    setView(view) {
      this.push({ view: view, folder: null, tag: null });
    },

    setTagFilter(uuid) {
      const next = this.tagFilter === uuid ? null : uuid;
      this.push({ view: 'all', folder: null, tag: next });
    },

    canGoBack() {
      return this.historyIndex > 0;
    },

    canGoForward() {
      return this.historyIndex < this.history.length - 1;
    },

    goBack() {
      if (!this.canGoBack()) return;
      this.historyIndex -= 1;
      this.apply(this.history[this.historyIndex]);
    },

    goForward() {
      if (!this.canGoForward()) return;
      this.historyIndex += 1;
      this.apply(this.history[this.historyIndex]);
    },

    canGoUp() {
      return this.view === 'all' && !this.tagFilter && this.folderUuid !== null;
    },

    goUp() {
      const folder = this.folderById(this.folderUuid);
      this.push({
        view: 'all',
        folder: folder ? folder.parent : null,
        tag: null,
      });
    },

    // ---- listing ---------------------------------------------------------

    matchesSearch(text) {
      if (!this.search) return true;
      return String(text || '')
        .toLowerCase()
        .includes(this.search.toLowerCase());
    },

    visibleFolders() {
      // Folders exist inside the vault's own tree only: the trash holds no
      // folder (deleting one is immediate and composite), a favourites view
      // is about entries, and a tag filter cuts across the tree.
      if (this.view !== 'all' || this.tagFilter) return [];
      if (this.typeFilter === 'entries' || this.typeFilter === 'favorites') return [];
      return this.folders
        .filter((folder) => folder.parent === this.folderUuid)
        .filter((folder) => this.matchesSearch(folder.name));
    },

    visibleEntries() {
      if (this.typeFilter === 'folders') return [];
      let rows = this.entries.filter((entry) =>
        this.view === 'trash' ? entry.trashed : !entry.trashed,
      );
      if (this.view === 'favorites' || this.typeFilter === 'favorites') {
        rows = rows.filter((entry) => entry.favorite);
      }
      if (this.tagFilter) {
        rows = rows.filter((entry) => entry.tags.includes(this.tagFilter));
      } else if (this.view === 'all') {
        rows = rows.filter((entry) => entry.folder === this.folderUuid);
      }
      if (this.search) {
        rows = rows.filter(
          (entry) =>
            this.matchesSearch(entry.name) || this.matchesSearch(entry.username),
        );
      }
      return this.sorted(rows);
    },

    sorted(rows) {
      if (this.sortField === 'default') return rows;
      const direction = this.sortDir === 'asc' ? 1 : -1;
      const compare = {
        name: (a, b) => a.name.localeCompare(b.name),
        favorite: (a, b) => Number(b.favorite) - Number(a.favorite),
        modified: (a, b) => String(a.modified).localeCompare(String(b.modified)),
      }[this.sortField];
      if (!compare) return rows;
      // A copy: sorting the array the caller handed us would reorder the
      // store's own data as a side effect of asking what to display.
      return [...rows].sort((a, b) => direction * compare(a, b));
    },

    isEmpty() {
      return this.visibleFolders().length === 0 && this.visibleEntries().length === 0;
    },

    filtering() {
      return Boolean(this.search) || this.typeFilter !== 'all';
    },

    resetAll() {
      this.search = '';
      this.typeFilter = 'all';
      this.sortField = 'default';
      this.sortDir = 'asc';
    },

    toggleSortDir() {
      this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
    },

    statusLine() {
      const folders = this.visibleFolders().length;
      const entries = this.visibleEntries().length;
      const total = folders + entries;
      const parts = [`${total} item${total === 1 ? '' : 's'}`];
      if (folders) parts.push(`${folders} folder${folders === 1 ? '' : 's'}`);
      parts.push(`${entries} entr${entries === 1 ? 'y' : 'ies'}`);
      return parts.join(' · ');
    },

    // ---- selection -------------------------------------------------------

    isSelected(uuid) {
      return this.selected.includes(uuid);
    },

    toggleSelection(uuid) {
      this.selected = this.isSelected(uuid)
        ? this.selected.filter((value) => value !== uuid)
        : [...this.selected, uuid];
    },

    clearSelection() {
      this.selected = [];
    },

    selectedEntries() {
      return this.entries.filter((entry) => this.isSelected(entry.uuid));
    },
  };
};
