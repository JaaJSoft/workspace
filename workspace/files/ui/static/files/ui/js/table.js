window.fileTableControls = function fileTableControls() {
  return {
    storageKey: 'fileTableControls:v4',
    searchQuery: '',
    typeFilter: 'all',
    sortField: window._filePrefsCache.defaultSort || 'default',
    sortDir: window._filePrefsCache.defaultSortDir || 'asc',
    columns: [
      { id: 'select', label: 'Select', required: false },
      { id: 'icon', label: 'Type', required: true },
      { id: 'name', label: 'Name', required: true },
      { id: 'favorite', label: 'Fav', required: false },
      { id: 'size', label: 'Size', required: false },
      { id: 'created', label: 'Created', required: false },
      { id: 'modified', label: 'Modified', required: false },
      { id: 'actions', label: 'Actions', required: false }
    ],
    defaultColumnOrder: ['select', 'icon', 'name', 'favorite', 'size', 'created', 'modified', 'actions'],
    defaultColumnVisibility: {
      select: true,
      icon: true,
      name: true,
      favorite: true,
      size: true,
      created: false,
      modified: true,
      actions: true
    },
    columnOrder: ['select', 'icon', 'name', 'favorite', 'size', 'created', 'modified', 'actions'],
    columnVisibility: {
      select: true,
      icon: true,
      name: true,
      favorite: true,
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
      this.table = this.$el.querySelector('table');
      if (!this.table) {
        return;
      }
      this.tbody = this.table.querySelector('tbody');
      if (!this.tbody) {
        return;
      }
      this.originalRows = Array.from(this.tbody.querySelectorAll('tr'));
      this.initStorageKey();
      this.loadState();
      this.pruneMissingColumns();
      this.ready = true;
      this.applyAll();
      this._initializing = false;

      this.$watch('searchQuery', () => this.applyRows());
      this.$watch('typeFilter', () => this.applyRows());
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

      // Compute bulk actions from actionsMap when selection changes
      this.$watch('selectedUuids', () => this._computeBulkActions());

      // Fetch actions for all visible rows
      this.fetchActions();

      // Keyboard shortcuts (single listener — remove previous on re-init)
      if (window._fileShortcutHandler) {
        window.removeEventListener('keydown', window._fileShortcutHandler);
      }
      window._fileShortcutHandler = (e) => this._handleKeyboardShortcut(e);
      window.addEventListener('keydown', window._fileShortcutHandler);
    },

    openFolderFromRow(event) {
      if (!event) return;
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (target && target.closest('a, button, input, select, textarea, label, [data-stop-row-click]')) {
        return;
      }
      // If items are selected, clicking should toggle selection instead of opening
      if (this.selectedUuids.size > 0) {
        const row = event.currentTarget;
        const uuid = row?.dataset?.uuid;
        if (uuid) {
          this.toggleRowSelection(uuid, event.shiftKey);
        }
        return;
      }
      // Normal behavior: open folder
      const row = event.currentTarget;
      const link = row?.querySelector('[data-folder-link]');
      if (link) {
        link.click();
      }
    },

    openFileFromRow(event, uuid, name, fileType) {
      if (!event) return;
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (target && target.closest('a, button, input, select, textarea, label, [data-stop-row-click]')) {
        return;
      }
      // If items are selected, clicking should toggle selection instead of opening
      if (this.selectedUuids.size > 0) {
        this.toggleRowSelection(uuid, event.shiftKey);
        return;
      }
      // Normal behavior: open file viewer
      window.dispatchEvent(new CustomEvent('open-file-viewer', {
        detail: { uuid, name, type: fileType }
      }));
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

      // Dispatch event for context menu to listen
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
        return action;
      });
    },

    openBackgroundContextMenu(event) {
      // Check if click is on a row/card or interactive element
      const target = event.target;
      if (target.closest('tr[data-uuid], div[data-uuid].group, button, a, input, select, textarea')) {
        return; // Let the row/card handle its own context menu
      }

      event.preventDefault();
      // Dispatch event for background context menu
      window.dispatchEvent(new CustomEvent('open-background-context-menu', {
        detail: { event }
      }));
    },

    // Selection methods
    isSelected(uuid) {
      return this.selectedUuids.has(uuid);
    },

    toggleRowSelection(uuid, shiftKey = false) {
      // Shift-click: select range from last selected to current
      if (shiftKey && this.lastSelectedUuid && this.lastSelectedUuid !== uuid) {
        // Get all visible items in current view (filtered rows in list view, or all items in mosaic view)
        let allUuids = [];

        // Try to get from visible rows (after filtering/sorting)
        if (this.visibleRows && this.visibleRows.length > 0) {
          allUuids = this.visibleRows.map(r => r.dataset?.uuid).filter(Boolean);
        } else {
          // Fallback: get from DOM
          // For list view: tbody > tr[data-uuid]
          // For mosaic view: .grid > div[data-uuid]
          const tbody = document.querySelector('tbody tr[data-uuid]');
          const gridContainer = document.querySelector('div.grid div[data-uuid]');

          if (tbody) {
            // List view
            const items = Array.from(document.querySelectorAll('tbody tr[data-uuid]'));
            allUuids = items.map(r => r.dataset.uuid).filter(Boolean);
          } else if (gridContainer) {
            // Mosaic view
            const items = Array.from(document.querySelectorAll('div.grid > div[data-uuid]'));
            allUuids = items.map(r => r.dataset.uuid).filter(Boolean);
          }
        }

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

    toggleSelectAll() {
      const visibleRows = Array.from(this.tbody.querySelectorAll('tr[data-uuid]'));
      const visibleUuids = visibleRows.map(r => r.dataset.uuid).filter(Boolean);

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
      if (!this.tbody) return 'none';
      const visibleRows = Array.from(this.tbody.querySelectorAll('tr[data-uuid]'));
      const visibleUuids = visibleRows.map(r => r.dataset.uuid).filter(Boolean);
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
        return `${total} item${total === 1 ? '' : 's'}`;
      }
      return `${visible} of ${total} item${total === 1 ? '' : 's'}`;
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
      let text = `${count} selected`;
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
      const rows = this.originalRows.length
        ? this.originalRows
        : Array.from((this.tbody || this.$el).querySelectorAll('tr[data-uuid], div[data-uuid]'));
      const uuids = rows.map(r => r.dataset.uuid).filter(Boolean);
      if (uuids.length === 0) return;

      this.actionsLoading = true;
      try {
        const csrfToken = getCSRFToken();
        const resp = await fetch('/api/v1/files/actions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ uuids }),
        });
        if (resp.ok) {
          this.actionsMap = await resp.json();
          this._computeBulkActions();
        }
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
        case 'download':
          this._bulkDownload(uuids);
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

    async _bulkDownload(uuids) {
      try {
        const csrfToken = getCSRFToken();
        const resp = await fetch('/api/v1/files/bulk-download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ uuids }),
        });
        if (!resp.ok) return;
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'download.zip';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) {
        // silent
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
      const row = document.querySelector(`tr[data-uuid="${uuid}"]`);
      if (!row) return null;
      return {
        uuid,
        name: row.dataset.displayName || row.dataset.name || '',
        nodeType: row.dataset.nodeType || 'file',
        fileType: row.dataset.fileType || '',
        actions: this.actionsMap[uuid] || [],
      };
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
      // Skip when typing in text inputs
      if (this._isInputFocused()) return;

      const ctrl = e.ctrlKey || e.metaKey;
      const key = e.key;

      // Alt+Arrow navigation shortcuts (before any selection/hover checks)
      if (e.altKey && key === 'ArrowLeft') {
        e.preventDefault();
        window.folderNav.back();
        return;
      }
      if (e.altKey && key === 'ArrowRight') {
        e.preventDefault();
        window.folderNav.forward();
        return;
      }
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
          const link = document.querySelector(`tr[data-uuid="${data.uuid}"] a[data-folder-link]`);
          if (link) link.click();
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
      const columns = Array.from(this.table.querySelectorAll('thead th[data-col]'))
        .map((cell) => cell.dataset.col)
        .filter(Boolean);
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

    loadState() {
      // 1. Apply localStorage immediately (synchronous, avoids flicker)
      try {
        const raw = localStorage.getItem(this.storageKey);
        if (raw) this._applyStateData(JSON.parse(raw));
      } catch (e) { /* ignore */ }

      // 2. Apply cached server state if available (already fetched this session)
      if (window._tableControlsCache) {
        this._applyStateData(window._tableControlsCache);
        this.pruneMissingColumns();
        this.applyAll();
        this._saveToLocalStorage();
        this._lastSyncedState = JSON.stringify(this._getStatePayload());
        return;
      }

      // 3. First load: fetch from server, cache for subsequent navigations
      fetch('/api/v1/settings/files/table_controls')
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (!data || !data.value) return;
          window._tableControlsCache = data.value;
          this._applyStateData(data.value);
          this.pruneMissingColumns();
          this.applyAll();
          this._saveToLocalStorage();
          this._lastSyncedState = JSON.stringify(this._getStatePayload());
        })
        .catch(() => {});
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

    pruneMissingColumns() {
      const present = new Set(
        Array.from(this.table.querySelectorAll('thead th[data-col]')).map((cell) => cell.dataset.col)
      );
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

    resetAll() {
      this.searchQuery = '';
      this.typeFilter = 'all';
      this.sortField = window._filePrefsCache.defaultSort || 'default';
      this.sortDir = window._filePrefsCache.defaultSortDir || 'asc';
      this.resetColumns();
      this.applyRows();
    },

    toggleSortDir() {
      this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
    },

    applyRows() {
      if (!this.ready || !this.tbody) return;
      const query = (this.searchQuery || '').trim().toLowerCase();
      const filtered = this.originalRows.filter((row) => this.matchesFilter(row, query));
      let ordered = filtered;

      if (this.sortField !== 'default') {
        const dir = this.sortDir === 'asc' ? 1 : -1;
        ordered = filtered.slice().sort((a, b) => this.compareRows(a, b, dir));
      }

      const fragment = document.createDocumentFragment();
      ordered.forEach((row) => fragment.appendChild(row));
      this.tbody.replaceChildren();
      this.tbody.appendChild(fragment);
      this.updateEmptyRow(ordered.length);
      this.saveState();
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
      return true;
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
      const headerCells = this.table.querySelectorAll('thead th[data-col]');
      if (headerCells.length) return headerCells.length;
      return this.columnOrder.length || 1;
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
          cell.textContent = 'No matching items';
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

    // Method to check if a card should be visible in mosaic view (respects filters).
    // Reads all values from the element's data-* attributes to avoid inlining
    // (and escaping) strings in the Alpine expression.
    shouldShowCard(el) {
      if (!el || !el.dataset) return true;
      const name = el.dataset.name || '';
      const nodeType = el.dataset.nodeType || '';
      const isFavorite = el.dataset.favorite || '0';
      // Hidden files filter
      if (!this.showHiddenFiles && name.startsWith('.')) {
        return false;
      }
      // Search query filter
      const query = (this.searchQuery || '').trim().toLowerCase();
      if (query && !name.includes(query)) {
        return false;
      }
      // Type filter
      if (this.typeFilter === 'files' && nodeType !== 'file') {
        return false;
      }
      if (this.typeFilter === 'folders' && nodeType !== 'folder') {
        return false;
      }
      if (this.typeFilter === 'favorites' && isFavorite !== '1') {
        return false;
      }
      return true;
    },

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

    setViewMode(mode) {
      this.viewMode = mode;
      window._filePrefsCache.defaultViewMode = mode;
      this._saveFilePrefs();
    },

    setMosaicTileSize(size) {
      window._filePrefsCache.mosaicTileSize = parseInt(size);
      this._saveFilePrefs();
    },

    _saveFilePrefs() {
      const API_URL = '/api/v1/settings/files/preferences';
      const csrfToken = getCSRFToken();

      fetch(API_URL, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ value: { ...window._filePrefsCache } }),
        credentials: 'same-origin',
      }).catch(() => {});
    },

    navigateToFolder(event, url) {
      // Only navigate if not clicking checkbox or action button
      if (event.target.closest('input[type="checkbox"]') ||
          event.target.closest('button') ||
          event.target.closest('[data-stop-row-click]')) {
        return;
      }
      // If items are selected, clicking should toggle selection instead of opening
      if (this.selectedUuids.size > 0) {
        const card = event.currentTarget;
        const uuid = card?.dataset?.uuid;
        if (uuid) {
          this.toggleRowSelection(uuid, event.shiftKey);
        }
        return;
      }
      // Normal behavior: navigate to folder
      const link = document.querySelector('#folder-nav-push');
      if (link) {
        link.href = url;
        link.click();
      }
    },

    openFileFromCard(event, uuid, name, fileType) {
      // Only open if not clicking checkbox or action button
      if (event.target.closest('input[type="checkbox"]') ||
          event.target.closest('button') ||
          event.target.closest('[data-stop-row-click]')) {
        return;
      }
      // If items are selected, clicking should toggle selection instead of opening
      if (this.selectedUuids.size > 0) {
        this.toggleRowSelection(uuid, event.shiftKey);
        return;
      }
      // Normal behavior: open file viewer
      window.dispatchEvent(new CustomEvent('open-file-viewer', {
        detail: { uuid, name, type: fileType }
      }));
    }
  };
};
