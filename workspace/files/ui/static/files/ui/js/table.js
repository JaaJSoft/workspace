// Upper bound of POST /api/v1/files/actions, mirrored from the endpoint.
const ACTIONS_BATCH_SIZE = 200;

const SORT_OPTIONS = [
  { id: 'default', label: gettext('Default') },
  { id: 'name', label: gettext('Name') },
  { id: 'size', label: gettext('Size') },
  { id: 'created', label: gettext('Created') },
  { id: 'modified', label: gettext('Modified') },
  { id: 'favorite', label: gettext('Favorite') },
  { id: 'type', label: gettext('Type') },
];

const TYPE_FILTERS = {
  files: { label: gettext('Files'), icon: 'file' },
  folders: { label: gettext('Folders'), icon: 'folder' },
  favorites: { label: gettext('Favorites'), icon: 'star' },
};

window.fileTableControls = function fileTableControls() {
  return {
    storageKey: 'fileTableControls:v4',
    searchQuery: '',
    typeFilter: 'all',
    tagFilter: [],
    sortField: window._filePrefsCache.defaultSort || 'default',
    sortDir: window._filePrefsCache.defaultSortDir || 'asc',
    columns: [
      { id: 'select', label: pgettext('table column', 'Select'), required: false },
      { id: 'icon', label: gettext('Type'), required: true },
      { id: 'name', label: gettext('Name'), required: true },
      { id: 'favorite', label: gettext('Fav'), required: false },
      { id: 'tags', label: gettext('Tags'), required: false },
      { id: 'size', label: gettext('Size'), required: false },
      { id: 'created', label: gettext('Created'), required: false },
      { id: 'modified', label: gettext('Modified'), required: false },
      { id: 'actions', label: gettext('Actions'), required: false }
    ],
    defaultColumnOrder: ['select', 'icon', 'name', 'favorite', 'tags', 'size', 'created', 'modified', 'actions'],
    defaultColumnVisibility: {
      select: true,
      icon: true,
      name: true,
      favorite: true,
      tags: true,
      size: true,
      created: false,
      modified: true,
      actions: true
    },
    columnOrder: ['select', 'icon', 'name', 'favorite', 'tags', 'size', 'created', 'modified', 'actions'],
    columnVisibility: {
      select: true,
      icon: true,
      name: true,
      favorite: true,
      tags: true,
      size: true,
      created: false,
      modified: true,
      actions: true
    },
    table: null,
    tbody: null,
    originalRows: [],
    showHiddenFiles: window._filePrefsCache.showHiddenFiles || false,
    ready: false,
    _initializing: true,
    _saveTimer: null,

    // Selection state
    selectedUuids: new Set(),
    lastSelectedUuid: null, // Track last selected item for shift-click range selection

    // Hovered row (for shortcuts when nothing is selected)
    hoveredUuid: null,

    // Clipboard state
    hasClipboardItems: false,

    // Actions state (fetched from API, keyed by UUID)
    actionsMap: {},
    actionsLoading: false,

    // Bulk actions (computed from actionsMap intersection)
    bulkActions: [],
    bulkActionsLoading: false,

    // Plain method, not an ES getter: fileTableWithView() merges its mixins
    // with object spread, which invokes getters once at spread time and
    // freezes their value. Methods survive the spread.
    orderedColumns() {
      return this.columnOrder
        .map((id) => this.columns.find((col) => col.id === id))
        .filter(Boolean);
    },

    init() {
      // A listing is rendered in one view mode: rows in a <table> for the
      // list, cards in a grid for the mosaic. Both carry the same data-*
      // attributes, so filters, counts and selection read either; only
      // sorting and column layout are table work.
      // Captured here: a method run from an item's own expression sees that
      // item as its `$el`, not this root.
      this._root = this.$el;
      this.table = this._root.querySelector('table');
      this.tbody = this.table ? this.table.querySelector('tbody') : null;
      this.originalRows = this.tbody
        ? Array.from(this.tbody.querySelectorAll('tr'))
        : this.cards();
      this.initStorageKey();
      this.loadState();
      this.pruneMissingColumns();
      this.ready = true;
      this.applyAll();
      this._initializing = false;

      this.$watch('searchQuery', () => this.applyRows());
      this.$watch('typeFilter', () => this.applyRows());
      this.$watch('tagFilter', () => this.applyRows());
      this.$watch('sortField', () => this.applyRows());
      this.$watch('sortDir', () => this.applyRows());
      this.$watch('showHiddenFiles', () => this.applyRows());

      // Sync showHiddenFiles when preferences change
      window.addEventListener('preferences-changed', (e) => {
        const show = e.detail?.showHiddenFiles;
        if (typeof show === 'boolean' && show !== this.showHiddenFiles) {
          this.showHiddenFiles = show;
        }
      });

      // Clear selection after bulk actions
      window.addEventListener('clear-file-selection', () => {
        this.clearSelection();
      });

      // Track clipboard state
      window.addEventListener('clipboard-changed', () => {
        this.hasClipboardItems = window.fileClipboard.hasItems();
      });
      this.hasClipboardItems = window.fileClipboard.hasItems();

      this._itemsByUuid = new Map(this.originalRows.map((el) => [el.dataset.uuid, el]));
      this._paintedSelection = new Set();
      this.$watch('selectedUuids', () => {
        this._paintSelection();
        this._computeBulkActions();
      });

      // Fetch actions for all visible rows
      this.fetchActions();

      // Keyboard shortcuts (single listener — remove previous on re-init)
      if (window._fileShortcutHandler) {
        window.removeEventListener('keydown', window._fileShortcutHandler);
      }
      window._fileShortcutHandler = (e) => this._handleKeyboardShortcut(e);
      window.addEventListener('keydown', window._fileShortcutHandler);
    },

    // --- Item events, delegated -------------------------------------------
    // Rows and cards carry no listener of their own: the listing container
    // (folder_content.html) forwards its events here, and the item is the
    // closest [data-uuid]. Binding a dozen listeners per item cost a large
    // folder seconds before it could be used.

    _eventElement(event) {
      const target = event.target;
      return target instanceof Element ? target : target?.parentElement || null;
    },

    _eventItem(event) {
      return this._eventElement(event)?.closest('[data-uuid]') || null;
    },

    _itemData(item) {
      const uuid = item.dataset.uuid;
      return {
        uuid,
        name: item.dataset.displayName || item.dataset.name || '',
        nodeType: item.dataset.nodeType || 'file',
        fileType: item.dataset.fileType || '',
        actions: this.actionsMap[uuid] || [],
      };
    },

    onListingClick(event, inTrash) {
      const target = this._eventElement(event);
      const item = target?.closest('[data-uuid]');
      if (!item) return;
      const uuid = item.dataset.uuid;

      if (target.closest('input[data-select-item]')) {
        this.toggleRowSelection(uuid, event.shiftKey);
        return;
      }
      const control = target.closest('[data-item-control]');
      if (control) {
        if (control.dataset.itemControl === 'favorite') {
          this.toggleFavorite(uuid, item.dataset.favorite === '1');
        } else if (control.dataset.itemControl === 'menu') {
          this.openContextMenu(event, this._itemData(item));
        }
        return;
      }

      if (target.closest('a, button, input, select, textarea, label, [data-stop-row-click]')) {
        return;
      }
      // With a selection under way, a click extends it instead of opening,
      // whether or not the item could be opened.
      if (this.selectedUuids.size > 0) {
        this.toggleRowSelection(uuid, event.shiftKey);
        return;
      }
      const isFolder = item.dataset.nodeType === 'folder';
      // A trashed folder has no listing to open; a trashed file opens in the
      // viewer, read-only.
      if (isFolder ? inTrash : item.dataset.viewable !== '1') return;
      if (isFolder) {
        window.folderNav.navigateTo(`/files/${uuid}`);
        return;
      }
      window.dispatchEvent(new CustomEvent('open-file-viewer', {
        detail: { uuid, name: item.dataset.displayName || '', type: item.dataset.fileType || '' }
      }));
    },

    onListingContextMenu(event, inTrash) {
      const item = this._eventItem(event);
      if (item) {
        this.openContextMenu(event, this._itemData(item));
      } else if (!inTrash) {
        this.openBackgroundContextMenu(event);
      }
    },

    // The hovered item is the keyboard shortcuts' target when nothing is
    // selected.
    onListingPointerOver(event) {
      const item = this._eventItem(event);
      if (item && this.hoveredUuid !== item.dataset.uuid) {
        this.hoveredUuid = item.dataset.uuid;
      }
    },

    onListingPointerOut(event) {
      const item = this._eventItem(event);
      if (!item || item.contains(event.relatedTarget)) return;
      // A dialog the item opened (rename, delete) still acts on it.
      if (document.querySelector('dialog[open]')) return;
      if (this.hoveredUuid === item.dataset.uuid) this.hoveredUuid = null;
    },

    onListingDragStart(event) {
      const item = this._eventItem(event);
      if (!item) return;
      this.onNodeDragStart(event, item.dataset.uuid, item.dataset.displayName || '', item.dataset.nodeType || 'file');
    },

    // A file is offered for a drag once the registry allows it a cut. A
    // folder is draggable from the server: pinning it needs no cut.
    _paintDraggable(actionsByUuid) {
      for (const [uuid, actions] of Object.entries(actionsByUuid)) {
        const item = this._itemsByUuid?.get(uuid);
        if (!item || item.dataset.nodeType !== 'file') continue;
        if (actions.some((a) => a.id === 'cut')) {
          item.setAttribute('draggable', 'true');
        } else {
          item.removeAttribute('draggable');
        }
      }
    },

    openContextMenu(event, nodeData) {
      event.preventDefault();

      // Selection-aware dispatch:
      //   - right-clicked file IS in a multi-selection -> menu acts on the
      //     whole selection (replace actions with the bulk-action intersection
      //     and attach selectionUuids so the menu dispatches bulk events).
      //   - right-clicked file is NOT in the current selection -> standard OS
      //     behavior: replace the selection with just that one file.
      if (this.selectedUuids.has(nodeData.uuid)) {
        if (this.selectedUuids.size > 1) {
          const uuids = this.getSelectedUuids();
          nodeData = {
            ...nodeData,
            selectionUuids: uuids,
            actions: this._buildSelectionActions(uuids),
          };
        }
      } else if (this.selectedUuids.size > 0) {
        this.selectedUuids = new Set([nodeData.uuid]);
        this.lastSelectedUuid = nodeData.uuid;
      }

      window.dispatchEvent(new CustomEvent('open-context-menu', {
        detail: { event, nodeData }
      }));
    },

    // Intersection of bulk-capable actions across all selected files,
    // sorted in the same category order the bulk toolbar uses. Toggle
    // actions get an extra `_bulkAdd` flag baked in so the context menu
    // can dispatch the right direction without re-reading actionsMap.
    _buildSelectionActions(uuids) {
      const lists = uuids.map(uuid => this.actionsMap[uuid] || []);
      if (lists.some(l => l.length === 0)) return [];

      let common = lists[0].filter(a => a.bulk);
      for (let i = 1; i < lists.length; i++) {
        const ids = new Set(lists[i].filter(a => a.bulk).map(a => a.id));
        common = common.filter(a => ids.has(a.id));
        if (common.length === 0) break;
      }

      const catOrder = ['transfer', 'organize', 'edit', 'danger', 'trash'];
      const sorted = common.slice().sort(
        (a, b) => catOrder.indexOf(a.category) - catOrder.indexOf(b.category)
      );

      const allHaveState = (actionId, key) => uuids.every(uuid => {
        const a = (this.actionsMap[uuid] || []).find(x => x.id === actionId);
        return a && a.state && a.state[key];
      });

      return sorted.map(action => {
        if (action.id === 'toggle_favorite') {
          return { ...action, _bulkAdd: !allHaveState('toggle_favorite', 'is_favorite') };
        }
        if (action.id === 'toggle_pin') {
          return { ...action, _bulkAdd: !allHaveState('toggle_pin', 'is_pinned') };
        }
        if (action.id === 'toggle_sensitive') {
          return { ...action, _bulkAdd: !allHaveState('toggle_sensitive', 'is_sensitive') };
        }
        return action;
      });
    },

    // Only reached off any item (onListingContextMenu); a control keeps the
    // browser's own menu.
    openBackgroundContextMenu(event) {
      if (event.target.closest('button, a, input, select, textarea')) return;

      event.preventDefault();
      // Dispatch event for background context menu
      window.dispatchEvent(new CustomEvent('open-background-context-menu', {
        detail: { event }
      }));
    },

    // Selection methods
    // Items show their selection through a data-selected attribute set here,
    // not through a binding per item: every binding reading selectedUuids
    // re-ran on each click, for every item of the listing.
    _paintSelection() {
      const selected = this.selectedUuids;
      const painted = this._paintedSelection;
      for (const uuid of painted) {
        if (!selected.has(uuid)) this._paintItemSelection(uuid, false);
      }
      for (const uuid of selected) {
        if (!painted.has(uuid)) this._paintItemSelection(uuid, true);
      }
      this._paintedSelection = new Set(selected);
    },

    _paintItemSelection(uuid, selected) {
      const item = this._itemsByUuid.get(uuid);
      if (!item) return;
      item.toggleAttribute('data-selected', selected);
      const checkbox = item.querySelector('input[data-select-item]');
      if (checkbox) checkbox.checked = selected;
    },

    toggleRowSelection(uuid, shiftKey = false) {
      // Shift-click: select range from last selected to current
      if (shiftKey && this.lastSelectedUuid && this.lastSelectedUuid !== uuid) {
        // Get all visible items in current view (filtered rows in list view, or all items in mosaic view)
        // The range runs over what is on screen, in its current order: a
        // filtered-out item is never swept into a selection.
        const allUuids = this.visibleItemUuids();

        const startIdx = allUuids.indexOf(this.lastSelectedUuid);
        const endIdx = allUuids.indexOf(uuid);

        if (startIdx !== -1 && endIdx !== -1) {
          const [from, to] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
          for (let i = from; i <= to; i++) {
            this.selectedUuids.add(allUuids[i]);
          }
          this.lastSelectedUuid = uuid;
          this.selectedUuids = new Set(this.selectedUuids);
          return;
        }
      }

      // Normal toggle
      if (this.selectedUuids.has(uuid)) {
        this.selectedUuids.delete(uuid);
        // If we deselect the last selected, clear it
        if (this.lastSelectedUuid === uuid) {
          this.lastSelectedUuid = null;
        }
      } else {
        this.selectedUuids.add(uuid);
        this.lastSelectedUuid = uuid;
      }
      // Trigger reactivity
      this.selectedUuids = new Set(this.selectedUuids);
    },

    cards() {
      return Array.from(this._root.querySelectorAll('div.grid > div[data-uuid]'));
    },

    // UUIDs of the items on screen: the rows the table currently holds, or
    // the cards no filter has hidden.
    visibleItemUuids() {
      const items = this.tbody
        ? Array.from(this.tbody.querySelectorAll('tr[data-uuid]'))
        : this.cards().filter((card) => card.style.display !== 'none');
      return items.map((el) => el.dataset.uuid).filter(Boolean);
    },

    toggleSelectAll() {
      const visibleUuids = this.visibleItemUuids();

      const allSelected = visibleUuids.every(uuid => this.selectedUuids.has(uuid));

      if (allSelected) {
        // Deselect all visible
        visibleUuids.forEach(uuid => this.selectedUuids.delete(uuid));
      } else {
        // Select all visible
        visibleUuids.forEach(uuid => this.selectedUuids.add(uuid));
      }
      // Trigger reactivity
      this.selectedUuids = new Set(this.selectedUuids);
    },

    // Plain method, not an ES getter (see orderedColumns above).
    selectAllState() {
      const visibleUuids = this.visibleItemUuids();
      if (visibleUuids.length === 0) return 'none';

      const selectedCount = visibleUuids.filter(uuid => this.selectedUuids.has(uuid)).length;
      if (selectedCount === 0) return 'none';
      if (selectedCount === visibleUuids.length) return 'all';
      return 'partial';
    },

    clearSelection() {
      this.selectedUuids = new Set();
    },

    getSelectedUuids() {
      return Array.from(this.selectedUuids);
    },

    hasAction(uuid, actionId) {
      return (this.actionsMap[uuid] || []).some((a) => a.id === actionId);
    },

    // The rows or cards the listing holds for these uuids, as the items the
    // transfer helpers take.
    itemsFromUuids(uuids) {
      const items = [];
      for (const uuid of uuids) {
        const el = this.originalRows.find((row) => row.dataset.uuid === uuid);
        if (!el) continue;
        items.push({
          uuid,
          name: el.dataset.displayName || '',
          nodeType: el.dataset.nodeType || 'file',
          element: el,
        });
      }
      return items;
    },

    // The folder this listing shows, null for the root, undefined when the
    // listing is not a folder (favorites, recent, a tag, shared with me).
    listingFolder() {
      const browser = document.getElementById('folder-browser');
      if (!browser || browser.dataset.folderListing !== '1') return undefined;
      return browser.dataset.folder || null;
    },

    // A row or card starts a drag. Grabbing a selected item carries the
    // whole selection; a folder additionally offers the sidebar pin. The
    // move payload is only attached when every item can be cut - the
    // registry decides, the drop targets refuse the rest.
    onNodeDragStart(event, uuid, name, nodeType) {
      const dt = event.dataTransfer;
      if (nodeType === 'folder') {
        dt.setData('application/x-pin-folder', JSON.stringify({ uuid, name }));
      }
      const uuids = this.selectedUuids.has(uuid) ? this.getSelectedUuids() : [uuid];
      const items = this.itemsFromUuids(uuids);
      const movable = items.length > 0 && items.every((item) => this.hasAction(item.uuid, 'cut'));
      if (!movable && nodeType !== 'folder') {
        event.preventDefault();
        return;
      }
      // The pin zone asks for 'copy', a folder target for 'move': both have
      // to be members of what the source allows.
      dt.effectAllowed = movable ? 'copyMove' : 'copy';
      if (movable) {
        window.fileDragMove.start(
          dt,
          items.map(({ uuid, name, nodeType }) => ({ uuid, name, nodeType })),
          { sourceFolder: this.listingFolder(), sources: items.map((item) => item.element) }
        );
      }
    },

    getSelectedCount() {
      return this.selectedUuids.size;
    },

    // --- Footer (status bar) helpers ---
    totalCount() {
      return this.originalRows.length;
    },

    visibleCount() {
      const query = (this.searchQuery || '').trim().toLowerCase();
      return this.originalRows.filter((row) => this.matchesFilter(row, query)).length;
    },

    footerCountText() {
      const total = this.totalCount();
      const visible = this.visibleCount();
      if (visible === total) {
        return interpolate(ngettext('%(count)s item', '%(count)s items', total), { count: total }, true);
      }
      return interpolate(
        ngettext('%(visible)s of %(count)s item', '%(visible)s of %(count)s items', total),
        { visible, count: total },
        true,
      );
    },

    selectedSize() {
      let total = 0;
      for (const row of this.originalRows) {
        if (this.selectedUuids.has(row.dataset.uuid)) {
          total += parseInt(row.dataset.size || '0', 10) || 0;
        }
      }
      return total;
    },

    selectionSummaryText() {
      const count = this.selectedUuids.size;
      let text = interpolate(ngettext('%(count)s selected', '%(count)s selected', count), { count }, true);
      const size = this.selectedSize();
      if (size > 0) {
        // formatFileSize is the shared global from common/static/ui/js/filesize.js
        text += ` - ${formatFileSize(size)}`;
      }
      return text;
    },

    // Fetch actions for all visible rows from the API
    async fetchActions() {
      // Get elements with data-uuid from both list view (tr) and mosaic view (div)
      const uuids = this.originalRows.map((r) => r.dataset.uuid).filter(Boolean);
      if (uuids.length === 0) return;

      this.actionsLoading = true;
      try {
        // The endpoint answers at most 200 UUIDs per call; a larger listing
        // asks in slices. Each answer is merged as it lands: a row only
        // needs its own entry, so the first rows are usable while the rest
        // of the listing is still being answered.
        const slices = [];
        for (let i = 0; i < uuids.length; i += ACTIONS_BATCH_SIZE) {
          slices.push(uuids.slice(i, i + ACTIONS_BATCH_SIZE));
        }
        // allSettled: a slice lost to the network must not end the wait for
        // the ones still in flight, nor drop the loading state early.
        await Promise.allSettled(slices.map(async (slice) => {
          const part = await window.fileActions.fetchActions(slice);
          if (!part) return;
          // Merged as each slice lands: an item only needs its own entry, so
          // the first ones are usable while the rest are still answered.
          Object.assign(this.actionsMap, part);
          this._paintDraggable(part);
          this._computeBulkActions();
          if (window.fileDragMove) window.fileDragMove.rememberActions(part);
        }));
      } catch (e) {
        // silent
      } finally {
        this.actionsLoading = false;
      }
    },

    // Compute bulk actions: intersection of actions for all selected UUIDs, filtered to bulk-capable
    _computeBulkActions() {
      const uuids = this.getSelectedUuids();
      if (uuids.length === 0) {
        this.bulkActions = [];
        return;
      }

      const lists = uuids.map(uuid => this.actionsMap[uuid] || []);
      if (lists.some(l => l.length === 0)) {
        this.bulkActions = [];
        return;
      }

      // Start with first file's bulk-capable actions, intersect with the rest
      let common = lists[0].filter(a => a.bulk);
      for (let i = 1; i < lists.length; i++) {
        const ids = new Set(lists[i].filter(a => a.bulk).map(a => a.id));
        common = common.filter(a => ids.has(a.id));
        if (common.length === 0) break;
      }

      const catOrder = ['transfer', 'organize', 'edit', 'danger', 'trash'];
      this.bulkActions = common.sort((a, b) => catOrder.indexOf(a.category) - catOrder.indexOf(b.category));
    },

    executeBulkAction(action) {
      const uuids = this.getSelectedUuids();
      if (uuids.length === 0) return;

      switch (action.id) {
        case 'toggle_favorite': {
          const allFav = uuids.every(uuid => {
            const a = (this.actionsMap[uuid] || []).find(x => x.id === 'toggle_favorite');
            return a && a.state && a.state.is_favorite;
          });
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'favorite', uuids, add: !allFav }
          }));
          break;
        }
        case 'toggle_pin': {
          const allPinned = uuids.every(uuid => {
            const a = (this.actionsMap[uuid] || []).find(x => x.id === 'toggle_pin');
            return a && a.state && a.state.is_pinned;
          });
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'pin', uuids, add: !allPinned }
          }));
          break;
        }
        case 'toggle_sensitive': {
          const allSensitive = uuids.every(uuid => {
            const a = (this.actionsMap[uuid] || []).find(x => x.id === 'toggle_sensitive');
            return a && a.state && a.state.is_sensitive;
          });
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'sensitive', uuids, add: !allSensitive }
          }));
          break;
        }
        case 'download':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'download', uuids }
          }));
          break;
        case 'cut':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'cut', uuids }
          }));
          break;
        case 'copy':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'copy', uuids }
          }));
          break;
        case 'delete':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'delete', uuids }
          }));
          break;
        case 'restore':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'restore', uuids }
          }));
          break;
        case 'purge':
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'purge', uuids }
          }));
          break;
      }
    },

    bulkPaste() {
      window.dispatchEvent(new CustomEvent('bulk-action', {
        detail: { action: 'paste' }
      }));
    },

    // --- Keyboard shortcuts ---

    _isInputFocused() {
      const el = document.activeElement;
      if (!el) return false;
      const tag = el.tagName;
      if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
      if (tag === 'INPUT') {
        const type = (el.type || '').toLowerCase();
        // Checkboxes and radios don't accept text — allow shortcuts
        if (type === 'checkbox' || type === 'radio') return false;
        return true;
      }
      if (el.isContentEditable) return true;
      return false;
    },

    _getRowDataByUuid(uuid) {
      if (!uuid) return null;
      const row = this._root.querySelector(`[data-uuid="${uuid}"]`);
      return row ? this._itemData(row) : null;
    },

    _nodeHasAction(data, actionId) {
      return data && data.actions && data.actions.some(a => a.id === actionId);
    },

    _getActionState(data, actionId) {
      if (!data || !data.actions) return {};
      const action = data.actions.find(a => a.id === actionId);
      return action && action.state ? action.state : {};
    },

    _handleKeyboardShortcut(e) {
      // Never interfere with events inside open dialogs
      if (e.target.closest('dialog[open]')) return;
      // The viewer covering the listing owns the keys: a Delete there must
      // not reach the row selected or hovered behind it.
      if (document.getElementById('files-main')?.hasAttribute('data-viewer-covered')) return;
      // Skip when typing in text inputs
      if (this._isInputFocused()) return;

      const ctrl = e.ctrlKey || e.metaKey;
      const key = e.key;

      // Alt+Up to the parent folder (Alt+Left/Right are the browser's own
      // back and forward). Before any selection/hover checks.
      if (e.altKey && key === 'ArrowUp') {
        e.preventDefault();
        const parentUrl = document.getElementById('folder-browser')?.dataset.parentUrl;
        if (parentUrl) window.folderNav.navigateTo(parentUrl);
        return;
      }

      const count = this.getSelectedCount();

      // Determine target: selection takes priority, then hovered row
      const useHover = count === 0 && this.hoveredUuid;

      // --- Shortcuts that don't require any target ---

      // Ctrl+V: Paste
      if (ctrl && key === 'v') {
        if (window.fileClipboard.hasItems()) {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('folder-action', {
            detail: { action: 'paste' }
          }));
        }
        return;
      }

      // No target at all — nothing to act on
      if (count === 0 && !useHover) return;

      // --- Multi-selection shortcuts (selected items only) ---
      if (count > 1) {
        const uuids = this.getSelectedUuids();

        if (key === 'Delete') {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'delete', uuids }
          }));
          return;
        }

        if (ctrl && key === 'x') {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'cut', uuids }
          }));
          return;
        }

        if (ctrl && key === 'c') {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'copy', uuids }
          }));
          return;
        }

        if ((key === 'f' || key === 'F') && !ctrl) {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('bulk-action', {
            detail: { action: 'favorite', uuids, add: true }
          }));
          return;
        }

        // No single-item shortcuts for multi-selection
        return;
      }

      // --- Single target: 1 selected item OR hovered item ---
      const targetUuid = useHover ? this.hoveredUuid : this.getSelectedUuids()[0];
      const data = this._getRowDataByUuid(targetUuid);
      if (!data) return;

      // Delete
      if (key === 'Delete' && this._nodeHasAction(data, 'delete')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'delete', uuid: data.uuid, name: data.name, nodeType: data.nodeType }
        }));
        return;
      }

      // Ctrl+X: Cut
      if (ctrl && key === 'x' && this._nodeHasAction(data, 'cut')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'cut', uuid: data.uuid, name: data.name, nodeType: data.nodeType }
        }));
        return;
      }

      // Ctrl+C: Copy
      if (ctrl && key === 'c' && this._nodeHasAction(data, 'copy')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'copy', uuid: data.uuid, name: data.name, nodeType: data.nodeType }
        }));
        return;
      }

      // F: Toggle favorite
      if ((key === 'f' || key === 'F') && !ctrl && this._nodeHasAction(data, 'toggle_favorite')) {
        e.preventDefault();
        const state = this._getActionState(data, 'toggle_favorite');
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'toggle_favorite', uuid: data.uuid, state }
        }));
        return;
      }

      // Enter / Space: Open folder or view file
      if (key === 'Enter' || key === ' ') {
        e.preventDefault();
        if (this._nodeHasAction(data, 'open')) {
          window.folderNav.navigateTo(`/files/${data.uuid}`);
        } else if (this._nodeHasAction(data, 'view')) {
          window.dispatchEvent(new CustomEvent('open-file-viewer', {
            detail: { uuid: data.uuid, name: data.name, type: data.fileType }
          }));
        }
        return;
      }

      // F2: Rename
      if (key === 'F2' && this._nodeHasAction(data, 'rename')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'rename', uuid: data.uuid, name: data.name }
        }));
        return;
      }

      // P: Pin/unpin (folders only)
      if ((key === 'p' || key === 'P') && !ctrl && this._nodeHasAction(data, 'toggle_pin')) {
        e.preventDefault();
        const state = this._getActionState(data, 'toggle_pin');
        window.dispatchEvent(new CustomEvent('file-action', {
          detail: { action: 'toggle_pin', uuid: data.uuid, state }
        }));
        return;
      }

      // Ctrl+I: Properties
      if (ctrl && key === 'i' && this._nodeHasAction(data, 'properties')) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('open-properties', {
          detail: { uuid: data.uuid, nodeType: data.nodeType }
        }));
        return;
      }
    },

    initStorageKey() {
      const baseKey = this.storageKey;
      const columns = this.headerColumnIds();
      if (columns.length) {
        this.storageKey = `${baseKey}:${columns.join('|')}`;
      } else {
        this.storageKey = baseKey;
      }
    },

    _applyStateData(data) {
      if (!data) return;
      if (Array.isArray(data.columnOrder)) {
        this.columnOrder = data.columnOrder.slice();
      }
      if (data.columnVisibility && typeof data.columnVisibility === 'object') {
        this.columnVisibility = { ...this.columnVisibility, ...data.columnVisibility };
      }
      if (typeof data.sortField === 'string') {
        this.sortField = data.sortField;
      }
      if (data.sortDir === 'asc' || data.sortDir === 'desc') {
        this.sortDir = data.sortDir;
      }
    },

    // The saved controls are embedded in the page (#table-controls-data),
    // read on the first listing and kept across folder swaps, where a save
    // replaces them. null when the user never saved any.
    _serverState() {
      if (window._tableControlsCache === undefined) {
        let saved = null;
        const el = document.getElementById('table-controls-data');
        if (el) {
          try { saved = JSON.parse(el.textContent); } catch (e) { saved = null; }
        }
        window._tableControlsCache = saved;
      }
      return window._tableControlsCache;
    },

    loadState() {
      try {
        const raw = localStorage.getItem(this.storageKey);
        if (raw) this._applyStateData(JSON.parse(raw));
      } catch (e) { /* ignore */ }

      // The server copy wins over this browser's: it follows the user
      // across devices.
      const saved = this._serverState();
      if (!saved) return;
      this._applyStateData(saved);
      this.pruneMissingColumns();
      this._saveToLocalStorage();
      this._lastSyncedState = JSON.stringify(this._getStatePayload());
    },

    _getStatePayload() {
      return {
        sortField: this.sortField,
        sortDir: this.sortDir,
        columnOrder: this.columnOrder,
        columnVisibility: this.columnVisibility
      };
    },

    _saveToLocalStorage() {
      try {
        localStorage.setItem(this.storageKey, JSON.stringify(this._getStatePayload()));
      } catch (e) { /* ignore */ }
    },

    saveState() {
      // Always keep localStorage in sync (fast, synchronous)
      this._saveToLocalStorage();
      // Skip server persist during initial load
      if (this._initializing) return;
      // Debounce server persist (500ms) to batch rapid changes
      clearTimeout(this._saveTimer);
      this._saveTimer = setTimeout(() => {
        const payload = this._getStatePayload();
        const serialized = JSON.stringify(payload);
        if (serialized === this._lastSyncedState) return;
        const csrfToken = getCSRFToken();
        fetch('/api/v1/settings/files/table_controls', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ value: payload }),
        })
          .then(() => { this._lastSyncedState = serialized; window._tableControlsCache = payload; })
          .catch(() => {});
      }, 500);
    },

    // Column ids the rendered header declares; none without a table.
    headerColumnIds() {
      if (!this.table) return [];
      return Array.from(this.table.querySelectorAll('thead th[data-col]'))
        .map((cell) => cell.dataset.col)
        .filter(Boolean);
    },

    pruneMissingColumns() {
      // Without a header there is nothing to compare the saved columns
      // against, and pruning would wipe the list-view preference.
      if (!this.table) return;
      const present = new Set(this.headerColumnIds());
      this.columns = this.columns.filter((col) => present.has(col.id));
      this.columnOrder = this.columnOrder.filter((id) => present.has(id));

      const missing = this.columns
        .map((col) => col.id)
        .filter((id) => !this.columnOrder.includes(id));
      this.columnOrder.push(...missing);

      this.columns.forEach((col) => {
        if (!(col.id in this.columnVisibility)) {
          this.columnVisibility[col.id] = true;
        }
        if (col.required) {
          this.columnVisibility[col.id] = true;
        }
      });
    },

    applyAll() {
      if (!this.ready) return;
      this.applyColumns();
      this.applyRows();
    },

    applyColumns() {
      if (!this.ready) return;
      this.applyColumnVisibility();
      this.applyColumnOrder();
    },

    applyColumnVisibility() {
      if (!this.table) return;
      this.columns.forEach((col) => {
        const show = this.isColumnVisible(col.id);
        this.table.querySelectorAll(`[data-col="${col.id}"]`).forEach((cell) => {
          cell.style.display = show ? '' : 'none';
        });
      });
      this.updateEmptyRowColspan();
    },

    applyColumnOrder() {
      if (!this.table) return;
      const headerRow = this.table.querySelector('thead tr');
      if (headerRow) {
        this.reorderRowCells(headerRow);
      }
      if (!this.tbody) return;
      const rows = Array.from(this.tbody.querySelectorAll('tr')).filter((row) => !row.dataset.emptyRow);
      rows.forEach((row) => this.reorderRowCells(row));
    },

    reorderRowCells(row) {
      const cells = Array.from(row.children).filter((cell) => cell.dataset && cell.dataset.col);
      if (!cells.length) return;
      const cellMap = new Map(cells.map((cell) => [cell.dataset.col, cell]));
      this.columnOrder.forEach((id) => {
        const cell = cellMap.get(id);
        if (cell) {
          row.appendChild(cell);
        }
      });
    },

    isColumnVisible(id) {
      const column = this.columns.find((col) => col.id === id);
      if (column && column.required) return true;
      return this.columnVisibility[id] !== false;
    },

    setColumnVisible(id, visible) {
      const column = this.columns.find((col) => col.id === id);
      if (column && column.required) {
        this.columnVisibility[id] = true;
        return;
      }
      this.columnVisibility[id] = Boolean(visible);
      this.applyColumnVisibility();
      this.saveState();
    },

    moveColumn(id, direction) {
      const index = this.columnOrder.indexOf(id);
      if (index === -1) return;
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= this.columnOrder.length) return;
      this.columnOrder.splice(index, 1);
      this.columnOrder.splice(nextIndex, 0, id);
      this.applyColumnOrder();
      this.saveState();
    },

    resetColumns() {
      this.columnOrder = this.defaultColumnOrder.filter((id) => this.columns.some((col) => col.id === id));
      this.columnVisibility = { ...this.defaultColumnVisibility };
      this.columns.forEach((col) => {
        if (!(col.id in this.columnVisibility)) {
          this.columnVisibility[col.id] = true;
        }
        if (col.required) {
          this.columnVisibility[col.id] = true;
        }
      });
      this.applyColumns();
      this.saveState();
    },

    // Mirrors the `max-sm` half of `.popover-sheet`: below 640px a toolbar
    // popover is a modal bottom sheet, so it traps the focus; above, it is an
    // anchored popover the page stays reachable around.
    popoverIsSheet() {
      return window.matchMedia('(max-width: 639px)').matches;
    },

    // The name query is left out of the count: the search box shows it.
    activeFilterCount() {
      return (this.typeFilter !== 'all' ? 1 : 0) + this.tagFilter.length;
    },

    hasActiveFilters() {
      return this.activeFilterCount() > 0 || Boolean((this.searchQuery || '').trim());
    },

    clearFilters() {
      this.searchQuery = '';
      this.typeFilter = 'all';
      this.tagFilter = [];
    },

    typeFilterLabel() {
      return TYPE_FILTERS[this.typeFilter]?.label || '';
    },

    typeFilterIcon() {
      return TYPE_FILTERS[this.typeFilter]?.icon || '';
    },

    // The trash has no favorites: a trashed item cannot be starred.
    sortOptions(isTrash) {
      return SORT_OPTIONS.filter((option) => !(isTrash && option.id === 'favorite'));
    },

    sortButtonLabel() {
      if (this.sortField === 'default') return gettext('Sort');
      return SORT_OPTIONS.find((option) => option.id === this.sortField)?.label || gettext('Sort');
    },

    pickSort(field) {
      if (field === this.sortField && field !== 'default') {
        this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
        return;
      }
      this.sortField = field;
    },

    applyRows() {
      if (!this.ready) return;
      if (this.tbody) {
        const query = (this.searchQuery || '').trim().toLowerCase();
        const filtered = this.originalRows.filter((row) => this.matchesFilter(row, query));
        const ordered = this.sortItems(filtered);

        const fragment = document.createDocumentFragment();
        ordered.forEach((row) => fragment.appendChild(row));
        this.tbody.replaceChildren();
        this.tbody.appendChild(fragment);
        this.updateEmptyRow(ordered.length);
      }
      this.applyCards();
      this.saveState();
    },

    // Rows and cards both start in server order (originalRows), which is
    // what the default sort restores.
    sortItems(items) {
      if (this.sortField === 'default') return items;
      const dir = this.sortDir === 'asc' ? 1 : -1;
      return items.slice().sort((a, b) => this.compareRows(a, b, dir));
    },

    // Mosaic cards are filtered from here rather than through an `x-show`
    // binding: the binding evaluated once at mount and never re-ran, so
    // every filter silently applied to the list view only.
    applyCards() {
      if (this.tbody) return;
      const cards = this.sortItems(this.originalRows);
      cards.forEach((card) => {
        card.style.display = this.shouldShowCard(card) ? '' : 'none';
      });
      cards[0]?.parentElement?.append(...cards);
    },

    // Cards and rows obey the same rules and carry the same data-*
    // attributes, so the row predicate answers for both.
    shouldShowCard(el) {
      if (!el || !el.dataset) return true;
      return this.matchesFilter(el, (this.searchQuery || '').trim().toLowerCase());
    },

    matchesFilter(row, query) {
      if (row.dataset.emptyRow) return false;
      const name = row.dataset.name || '';
      if (!this.showHiddenFiles && name.startsWith('.')) {
        return false;
      }
      if (query) {
        if (!name.includes(query)) {
          return false;
        }
      }
      if (this.typeFilter === 'files' && row.dataset.nodeType !== 'file') {
        return false;
      }
      if (this.typeFilter === 'folders' && row.dataset.nodeType !== 'folder') {
        return false;
      }
      if (this.typeFilter === 'favorites' && row.dataset.favorite !== '1') {
        return false;
      }
      if (!this.matchesTagFilter(row.dataset.tags)) {
        return false;
      }
      return true;
    },

    // --- Tag filter (OR across selected tags, mirroring ?tags= on the API) ---
    matchesTagFilter(rawTags) {
      if (!this.tagFilter.length) return true;
      const tags = (rawTags || '').trim().split(/\s+/);
      return this.tagFilter.some((uuid) => tags.includes(uuid));
    },

    hasTagFilter(uuid) {
      return this.tagFilter.indexOf(uuid) !== -1;
    },

    toggleTagFilter(uuid) {
      this.tagFilter = this.hasTagFilter(uuid)
        ? this.tagFilter.filter((id) => id !== uuid)
        : [...this.tagFilter, uuid];
    },

    clearTagFilter() {
      this.tagFilter = [];
    },

    compareRows(a, b, dir) {
      const aValue = this.getSortValue(a);
      const bValue = this.getSortValue(b);
      if (typeof aValue === 'string' || typeof bValue === 'string') {
        const diff = String(aValue).localeCompare(String(bValue));
        if (diff !== 0) return diff * dir;
      } else {
        if (aValue < bValue) return -1 * dir;
        if (aValue > bValue) return 1 * dir;
      }
      const aName = a.dataset.name || '';
      const bName = b.dataset.name || '';
      return aName.localeCompare(bName) * dir;
    },

    getSortValue(row) {
      switch (this.sortField) {
        case 'name':
          return row.dataset.name || '';
        case 'size':
          return parseInt(row.dataset.size || '0', 10);
        case 'created':
          return parseInt(row.dataset.created || '0', 10);
        case 'modified':
          return parseInt(row.dataset.updated || '0', 10);
        case 'favorite':
          return parseInt(row.dataset.favorite || '0', 10);
        case 'type':
          return row.dataset.nodeType === 'folder' ? 0 : 1;
        default:
          return 0;
      }
    },

    getColumnCount() {
      return this.headerColumnIds().length || this.columnOrder.length || 1;
    },

    updateEmptyRowColspan() {
      if (!this.tbody) return;
      const emptyRow = this.tbody.querySelector('[data-empty-row]');
      if (!emptyRow) return;
      const cell = emptyRow.querySelector('td');
      if (!cell) return;
      cell.setAttribute('colspan', String(this.getColumnCount()));
    },

    updateEmptyRow(visibleCount) {
      if (!this.tbody) return;
      let emptyRow = this.tbody.querySelector('[data-empty-row]');
      if (visibleCount === 0) {
        if (!emptyRow) {
          emptyRow = document.createElement('tr');
          emptyRow.dataset.emptyRow = 'true';
          const cell = document.createElement('td');
          cell.className = 'py-6 text-center text-base-content/60';
          cell.textContent = gettext('No matching items');
          cell.setAttribute('colspan', String(this.getColumnCount()));
          emptyRow.appendChild(cell);
        }
        emptyRow.querySelector('td').setAttribute('colspan', String(this.getColumnCount()));
        this.tbody.appendChild(emptyRow);
      } else if (emptyRow) {
        emptyRow.remove();
      }
    }
  };
};

// --- Combined file table with view toggle ---
window.fileTableWithView = function fileTableWithView() {
  const tableControls = fileTableControls();
  const viewControls = viewToggle();

  // Merge init methods
  const tableInit = tableControls.init;
  const viewInit = viewControls.init;

  return {
    ...tableControls,
    ...viewControls,

    init() {
      // Call both init methods
      if (tableInit) tableInit.call(this);
      if (viewInit) viewInit.call(this);
    }
  };
};

// --- View toggle component ---
window.viewToggle = function viewToggle() {
  return {
    viewMode: 'list',
    mosaicTileSize: 3,
    compactList: false,

    init() {
      // Initialize from user preferences
      const prefs = window.getFilePrefs();
      this.viewMode = prefs.defaultViewMode || 'list';
      this.mosaicTileSize = prefs.mosaicTileSize || 3;
      this.compactList = prefs.compactList === true;

      // Listen for preference changes
      window.addEventListener('preferences-changed', (e) => {
        if (e.detail) {
          if (e.detail.defaultViewMode) this.viewMode = e.detail.defaultViewMode;
          if (e.detail.mosaicTileSize) this.mosaicTileSize = e.detail.mosaicTileSize;
          this.compactList = e.detail.compactList === true;
        }
      });

    },

    // Tile size computed helpers
    tileMinWidth() {
      return { 1: 100, 2: 140, 3: 180, 4: 230, 5: 290 }[this.mosaicTileSize] || 180;
    },
    tileGap() {
      return this.mosaicTileSize <= 2 ? 8 : 16;
    },
    tileIconSize() {
      return { 1: 28, 2: 36, 3: 48, 4: 64, 5: 80 }[this.mosaicTileSize] || 48;
    },
    // A column stretches past its minimum width until the next one fits;
    // the fallback after `auto` is a typical width, not the widest.
    tileImageSizes() {
      return `auto, ${Math.round(this.tileMinWidth() * 1.25)}px`;
    },

    // The listing is rendered in the saved view mode, so switching is a
    // save followed by a re-render; the folder veil covers the round trip.
    async setViewMode(mode) {
      const previous = this.viewMode;
      this.viewMode = mode;
      window._filePrefsCache.defaultViewMode = mode;
      if (await this._saveFilePrefs()) {
        this.$ajax(window.location.pathname + window.location.search, { target: 'folder-browser' });
        return;
      }
      this.viewMode = previous;
      window._filePrefsCache.defaultViewMode = previous;
      window.AppAlert?.error(gettext('Could not switch the view'));
    },

    setMosaicTileSize(size) {
      window._filePrefsCache.mosaicTileSize = parseInt(size);
      this._saveFilePrefs();
    },

    _saveFilePrefs() {
      const API_URL = '/api/v1/settings/files/preferences';
      const csrfToken = getCSRFToken();

      return fetch(API_URL, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ value: { ...window._filePrefsCache } }),
        credentials: 'same-origin',
      }).then((resp) => resp.ok, () => false);
    },
  };
};
