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
    // Entries a newer build wrote and this one cannot read. Counted apart
    // from tamperedCount: neither is tampering, and lumping the two together
    // would send the user to distrust an account that a reload would fix.
    unsupportedCount: 0,

    view: 'all',
    folderUuid: null,
    tagFilter: null,

    search: '',
    typeFilter: 'all',
    sortField: 'default',
    sortDir: 'asc',
    viewMode: 'list',
    tileSize: 3,

    selected: [],

    setData(data) {
      this.folders = data.folders || [];
      this.tags = data.tags || [];
      this.entries = data.entries || [];
      this.tamperedCount = data.tamperedCount || 0;
      this.unsupportedCount = data.unsupportedCount || 0;
      this.selected = [];
    },

    // ---- structure -------------------------------------------------------

    tagById(uuid) {
      return this.tags.find((tag) => tag.uuid === uuid) || null;
    },

    // The tags of an entry the vault still knows, in the entry's order: a
    // uuid with no tag row behind it draws nothing and counts for nothing.
    entryTags(entry) {
      return entry.tags.map((uuid) => this.tagById(uuid)).filter(Boolean);
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

    // Every value here names something inside one vault - a folder UUID, the
    // trail that reached it - so it means nothing in the next one. Switching
    // used to be a page load, which cleared it for free; it is a fetch now,
    // and a folder UUID left behind filters the opened vault down to nothing.
    resetNavigation() {
      this.view = 'all';
      this.folderUuid = null;
      this.tagFilter = null;
      this.selected = [];
    },

    // Where the listing stands, as identifiers only: it is what a history
    // entry keeps, and an entry outlives the lock.
    place() {
      return { view: this.view, folder: this.folderUuid, tag: this.tagFilter };
    },

    // The browser overrides this to record the move in the browser's history;
    // on its own the store just moves.
    navigate(place) {
      this.apply(place);
    },

    apply(place) {
      this.view = place.view;
      this.folderUuid = place.folder;
      this.tagFilter = place.tag;
      this.selected = [];
    },

    openFolder(uuid) {
      this.navigate({ view: 'all', folder: uuid, tag: null });
    },

    setView(view) {
      this.navigate({ view: view, folder: null, tag: null });
    },

    setTagFilter(uuid) {
      const next = this.tagFilter === uuid ? null : uuid;
      this.navigate({ view: 'all', folder: null, tag: next });
    },

    canGoUp() {
      return this.view === 'all' && !this.tagFilter && this.folderUuid !== null;
    },

    goUp() {
      const folder = this.folderById(this.folderUuid);
      this.navigate({
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
      // The field is the entry property to compare: the server names the
      // sorts on offer (SORT_FIELDS), so no list of them is kept here.
      const field = this.sortField;
      const compare = (a, b) =>
        typeof a === 'boolean' || typeof b === 'boolean'
          ? Number(b) - Number(a)
          : String(a).localeCompare(String(b));
      // A copy: sorting the array the caller handed us would reorder the
      // store's own data as a side effect of asking what to display.
      return [...rows].sort((a, b) => direction * compare(a[field], b[field]));
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
      const count = (singular, plural, n) => interpolate(ngettext(singular, plural, n), { count: n }, true);
      const parts = [count('%(count)s item', '%(count)s items', folders + entries)];
      if (folders) parts.push(count('%(count)s folder', '%(count)s folders', folders));
      parts.push(count('%(count)s entry', '%(count)s entries', entries));
      return parts.join(' · ');
    },

    selectionLabel() {
      const count = this.selected.length;
      return interpolate(ngettext('%(count)s selected', '%(count)s selected', count), { count }, true);
    },

    tamperedHeadline() {
      const count = this.tamperedCount;
      return interpolate(
        ngettext(
          '%(count)s entry was removed from the list.',
          '%(count)s entries were removed from the list.',
          count,
        ),
        { count },
        true,
      );
    },

    unsupportedHeadline() {
      const count = this.unsupportedCount;
      return interpolate(
        ngettext(
          '%(count)s entry needs a newer version of the app.',
          '%(count)s entries need a newer version of the app.',
          count,
        ),
        { count },
        true,
      );
    },

    unsupportedDetail() {
      return ngettext(
        'It was saved by a more recent version than the one this page is running. Reload the page to read it.',
        'They were saved by a more recent version than the one this page is running. Reload the page to read them.',
        this.unsupportedCount,
      );
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

    // Over what is on screen, not over everything the vault holds: a filter
    // narrowing the listing has to narrow what "all" means, or the box would
    // tick rows the user cannot see.
    selectAllState() {
      const rows = this.visibleEntries();
      if (!rows.length) return 'none';
      const picked = rows.filter((entry) => this.isSelected(entry.uuid)).length;
      if (picked === 0) return 'none';
      return picked === rows.length ? 'all' : 'partial';
    },

    selectAll() {
      this.selected = [
        ...new Set([
          ...this.selected,
          ...this.visibleEntries().map((entry) => entry.uuid),
        ]),
      ];
    },

    toggleSelectAll() {
      const uuids = this.visibleEntries().map((entry) => entry.uuid);
      this.selected =
        this.selectAllState() === 'all'
          ? this.selected.filter((uuid) => !uuids.includes(uuid))
          : [...new Set([...this.selected, ...uuids])];
    },

    selectedEntries() {
      return this.entries.filter((entry) => this.isSelected(entry.uuid));
    },
  };
};
