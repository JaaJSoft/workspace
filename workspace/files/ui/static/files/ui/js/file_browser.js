window.sidebarCollapse = function sidebarCollapse() {
  return {
    collapsed: localStorage.getItem('sidebarCollapsed') === 'true',
    activeView: null,

    isMobile() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },
    
    init() {
      if (this.isMobile()) {
        this.collapsed = true;
      }
      this.syncActiveView();
      window.addEventListener('popstate', () => this.syncActiveView());
      window.matchMedia('(max-width: 1023px)').addEventListener('change', (event) => {
        if (event.matches) {
          this.collapsed = true;
        }
      });

      // Initialize Lucide icons on load
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons();
        }
      });
      
      // Re-initialize Lucide icons when state changes
      this.$watch('collapsed', () => {
        // Wait for transition to complete before recreating icons
        setTimeout(() => {
          if (typeof lucide !== 'undefined') {
            lucide.createIcons();
          }
        }, 350); // Slightly longer than transition duration (300ms)
      });
    },
    
    toggleCollapse() {
      if (this.isMobile()) {
        return;
      }
      this.collapsed = !this.collapsed;
      localStorage.setItem('sidebarCollapsed', this.collapsed);
      
      // Immediate icon refresh for visible elements
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons();
        }
      });
    },

    syncActiveView() {
      const path = window.location.pathname.replace(/\/+$/, '');
      const params = new URLSearchParams(window.location.search);
      const favorites = (params.get('favorites') || '').toLowerCase();
      const recent = (params.get('recent') || '').toLowerCase();
      if (['1', 'true', 'yes'].includes(favorites)) {
        this.activeView = 'favorites';
        return;
      }
      if (['1', 'true', 'yes'].includes(recent)) {
        this.activeView = 'recent';
        return;
      }
      if (path === '/files/trash') {
        this.activeView = 'trash';
        return;
      }
      if (path === '/files' || path.startsWith('/files/')) {
        this.activeView = 'root';
        return;
      }
      this.activeView = null;
    },

    setActiveView(view) {
      this.activeView = view;
    }
  }
}

window.fileBrowser = function fileBrowser() {
  return {
    get currentFolder() {
      const folderEl = document.getElementById('folder-browser');
      return folderEl?.dataset.folder || '';
    },

    _initFileActions() {
      // Listen for file actions from context menu
      window.addEventListener('file-action', (e) => {
        const { action, uuid, name, nodeType, isFavorite, isPinned } = e.detail;

        switch (action) {
          case 'toggleFavorite':
            this.toggleFavorite(uuid, isFavorite);
            break;
          case 'togglePin':
            this.togglePin(uuid, isPinned);
            break;
          case 'rename':
            this.showRenameDialog(uuid, name);
            break;
          case 'delete':
            this.confirmDelete(uuid, name, nodeType);
            break;
          case 'restore':
            this.confirmRestore(uuid, name, nodeType);
            break;
          case 'purge':
            this.confirmPurge(uuid, name, nodeType);
            break;
        }
      });
    },

    openFolderFromRow(event) {
      if (!event) return;
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (target && target.closest('a, button, input, select, textarea, label, [data-stop-row-click]')) {
        return;
      }
      const row = event.currentTarget;
      const link = row?.querySelector('[data-folder-link]');
      if (link) {
        link.click();
      }
    },

    openFileFromRow(event, uuid, name, mimeType) {
      if (!event) return;
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (target && target.closest('a, button, input, select, textarea, label, [data-stop-row-click]')) {
        return;
      }
      window.dispatchEvent(new CustomEvent('open-file-viewer', {
        detail: { uuid, name, mime_type: mimeType }
      }));
    },

    openContextMenu(event, nodeData) {
      event.preventDefault();
      // Dispatch event for context menu to listen
      window.dispatchEvent(new CustomEvent('open-context-menu', {
        detail: { event, nodeData }
      }));
    },

    showCreateFolderDialog() {
      const dialog = document.getElementById('create-folder-dialog');
      const input = dialog.querySelector('input');
      input.value = '';
      dialog.showModal();
      setTimeout(() => input.focus(), 100);
    },

    showCreateFileDialog(defaultType = 'txt') {
      const dialog = document.getElementById('create-file-dialog');
      if (!dialog) return;
      const nameInput = dialog.querySelector('[x-ref="fileName"]');
      const typeSelect = dialog.querySelector('select');
      const customInput = dialog.querySelector('[x-ref="customExt"]');
      if (nameInput) {
        nameInput.value = '';
      }
      if (customInput) {
        customInput.value = '';
      }
      if (typeSelect) {
        typeSelect.value = defaultType;
        typeSelect.dispatchEvent(new Event('change', { bubbles: true }));
      }
      dialog.showModal();
      setTimeout(() => nameInput && nameInput.focus(), 100);
    },

    showRenameDialog(uuid, name) {
      const dialog = document.getElementById('rename-dialog');
      window.dispatchEvent(new CustomEvent('open-rename', { detail: { uuid, name } }));
      dialog.showModal();
      setTimeout(() => dialog.querySelector('input').focus(), 100);
    },

    async confirmDelete(uuid, name, nodeType) {
      const confirmed = await AppDialog.confirm({
        title: `Delete ${nodeType}?`,
        message: `Move "${name}" to trash?${nodeType === 'folder' ? ' This will also move all contents.' : ''}`,
        okLabel: 'Move to trash',
        okClass: 'btn-error'
      });
      if (confirmed) {
        this.deleteItem(uuid);
      }
    },

    async createFolder(name) {
      try {
        const response = await fetch('/api/v1/files', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': this.getCsrfToken()
          },
          body: JSON.stringify({
            name: name,
            node_type: 'folder',
            parent: this.currentFolder || null
          })
        });
        if (response.ok) {
          document.getElementById('create-folder-dialog').close();
          this.refreshFolderBrowser();
        } else {
          const data = await response.json();
          this.showAlert('error', data.detail || 'Failed to create folder');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to create folder');
      }
    },

    async createFile(name, fileType, customExt) {
      const trimmedName = (name || '').trim();
      if (!trimmedName) {
        this.showAlert('error', 'File name is required');
        return;
      }

      const typeMap = {
        txt: { ext: 'txt', mime: 'text/plain' },
        md: { ext: 'md', mime: 'text/markdown' },
        json: { ext: 'json', mime: 'application/json' },
        csv: { ext: 'csv', mime: 'text/csv' }
      };

      let extension = '';
      let mimeType = '';
      if (fileType === 'custom') {
        extension = (customExt || '').trim().replace(/^\./, '');
        if (!extension) {
          this.showAlert('error', 'Custom extension is required');
          return;
        }
        mimeType = 'application/octet-stream';
      } else if (typeMap[fileType]) {
        extension = typeMap[fileType].ext;
        mimeType = typeMap[fileType].mime;
      }

      let finalName = trimmedName;
      const lowerName = trimmedName.toLowerCase();
      if (extension) {
        const extSuffix = `.${extension.toLowerCase()}`;
        if (!lowerName.endsWith(extSuffix) && !trimmedName.includes('.')) {
          finalName = `${trimmedName}.${extension}`;
        } else if (fileType === 'custom' && !lowerName.endsWith(extSuffix)) {
          finalName = `${trimmedName}.${extension}`;
        }
      }

      const file = new File([''], finalName, {
        type: mimeType || 'application/octet-stream'
      });
      const formData = new FormData();
      formData.append('name', finalName);
      formData.append('node_type', 'file');
      formData.append('content', file);
      if (mimeType) {
        formData.append('mime_type', mimeType);
      }
      if (this.currentFolder) {
        formData.append('parent', this.currentFolder);
      }

      try {
        const response = await fetch('/api/v1/files', {
          method: 'POST',
          headers: {
            'X-CSRFToken': this.getCsrfToken()
          },
          body: formData
        });
        if (response.ok) {
          document.getElementById('create-file-dialog').close();
          this.refreshFolderBrowser();
        } else {
          let data = {};
          try {
            data = await response.json();
          } catch (error) {
            data = {};
          }
          this.showAlert('error', data.detail || 'Failed to create file');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to create file');
      }
    },

    async handleUpload(event) {
      const files = event.target.files;
      if (!files.length) return;

      for (const file of files) {
        const formData = new FormData();
        formData.append('name', file.name);
        formData.append('node_type', 'file');
        formData.append('content', file);
        if (this.currentFolder) {
          formData.append('parent', this.currentFolder);
        }

        try {
          const response = await fetch('/api/v1/files', {
            method: 'POST',
            headers: {
              'X-CSRFToken': this.getCsrfToken()
            },
            body: formData
          });
          if (!response.ok) {
            const data = await response.json();
            this.showAlert('error', `Failed to upload ${file.name}: ${data.detail || 'Unknown error'}`);
          }
        } catch (error) {
          this.showAlert('error', `Failed to upload ${file.name}`);
        }
      }
          this.refreshFolderBrowser();
    },

    async renameItem(uuid, newName) {
      try {
        const response = await fetch(`/api/v1/files/${uuid}`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': this.getCsrfToken()
          },
          body: JSON.stringify({ name: newName })
        });
        if (response.ok) {
          document.getElementById('rename-dialog').close();
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          const data = await response.json();
          this.showAlert('error', data.detail || 'Failed to rename');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to rename');
      }
    },

    async deleteItem(uuid) {
      try {
        const response = await fetch(`/api/v1/files/${uuid}`, {
          method: 'DELETE',
          headers: {
            'X-CSRFToken': this.getCsrfToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          this.showAlert('error', 'Failed to delete');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to delete');
      }
    },

    async confirmRestore(uuid, name, nodeType) {
      const confirmed = await AppDialog.confirm({
        title: `Restore ${nodeType}?`,
        message: `Restore "${name}" from trash?`,
        okLabel: 'Restore',
        okClass: 'btn-primary'
      });
      if (confirmed) {
        this.restoreItem(uuid);
      }
    },

    async restoreItem(uuid) {
      try {
        const response = await fetch(`/api/v1/files/${uuid}/restore`, {
          method: 'POST',
          headers: {
            'X-CSRFToken': this.getCsrfToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          this.showAlert('error', 'Failed to restore');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to restore');
      }
    },

    async confirmPurge(uuid, name, nodeType) {
      const confirmed = await AppDialog.confirm({
        title: `Delete ${nodeType} permanently?`,
        message: `This will permanently delete "${name}" and cannot be undone.`,
        okLabel: 'Delete permanently',
        okClass: 'btn-error'
      });
      if (confirmed) {
        this.purgeItem(uuid);
      }
    },

    async purgeItem(uuid) {
      try {
        const response = await fetch(`/api/v1/files/${uuid}/purge`, {
          method: 'DELETE',
          headers: {
            'X-CSRFToken': this.getCsrfToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          this.showAlert('error', 'Failed to delete permanently');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to delete permanently');
      }
    },

    async confirmCleanTrash() {
      const confirmed = await AppDialog.confirm({
        title: 'Empty trash?',
        message: 'This will permanently delete all items in trash and cannot be undone.',
        okLabel: 'Empty trash',
        okClass: 'btn-error'
      });
      if (confirmed) {
        this.cleanTrash(true);
      }
    },

    async cleanTrash(force = false) {
      const url = force ? '/api/v1/files/trash/clean?force=1' : '/api/v1/files/trash/clean';
      try {
        const response = await fetch(url, {
          method: 'DELETE',
          headers: {
            'X-CSRFToken': this.getCsrfToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
        } else {
          this.showAlert('error', 'Failed to clean trash');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to clean trash');
      }
    },

    async toggleFavorite(uuid, isFavorite) {
      if (!uuid) return;
      try {
        const response = await fetch(`/api/v1/files/${uuid}/favorite`, {
          method: isFavorite ? 'DELETE' : 'POST',
          headers: {
            'X-CSRFToken': this.getCsrfToken()
          }
        });
        if (response.ok) {
          this.refreshFolderBrowser();
          return;
        }
        let data = {};
        try {
          data = await response.json();
        } catch (error) {
          data = {};
        }
        this.showAlert('error', data.detail || 'Failed to update favorites');
      } catch (error) {
        this.showAlert('error', 'Failed to update favorites');
      }
    },

    async togglePin(uuid, isPinned) {
      if (!uuid) return;
      try {
        const response = await fetch(`/api/v1/files/${uuid}/pin`, {
          method: isPinned ? 'DELETE' : 'POST',
          headers: {
            'X-CSRFToken': this.getCsrfToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
          return;
        }
        let data = {};
        try {
          data = await response.json();
        } catch (error) {
          data = {};
        }
        this.showAlert('error', data.detail || 'Failed to update pin');
      } catch (error) {
        this.showAlert('error', 'Failed to update pin');
      }
    },

    async pinFolder(uuid) {
      if (!uuid) return;
      try {
        const response = await fetch(`/api/v1/files/${uuid}/pin`, {
          method: 'POST',
          headers: {
            'X-CSRFToken': this.getCsrfToken()
          }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.refreshFolderBrowser();
          return;
        }
        let data = {};
        try {
          data = await response.json();
        } catch (error) {
          data = {};
        }
        this.showAlert('error', data.detail || 'Failed to pin folder');
      } catch (error) {
        this.showAlert('error', 'Failed to pin folder');
      }
    },

    refreshFolderBrowser() {
      const refreshLink = document.querySelector('[data-refresh-folder-browser]');
      if (refreshLink) {
        refreshLink.click();
        return;
      }
      const target = document.getElementById('folder-browser');
      if (!target) return;
      fetch(window.location.href, {
        headers: {
          'X-Alpine-Request': 'true'
        }
      })
        .then((response) => {
          if (!response.ok) {
            this.showAlert('error', 'Failed to refresh items');
            return null;
          }
          return response.text();
        })
        .then((html) => {
          if (!html) return;
          const wrapper = document.createElement('div');
          wrapper.innerHTML = html;
          const fresh = wrapper.querySelector('#folder-browser');
          if (!fresh) {
            this.showAlert('error', 'Failed to refresh items');
            return;
          }
          target.replaceWith(fresh);
          if (window.Alpine?.initTree) {
            window.Alpine.initTree(fresh);
          }
          if (window.lucide?.createIcons) {
            window.lucide.createIcons({ nodes: [fresh] });
          }
        })
        .catch(() => this.showAlert('error', 'Failed to refresh items'));
    },

    showAlert(type, message) {
      if (window.AppAlert && typeof window.AppAlert.show === 'function') {
        window.AppAlert.show({
          type: type || 'info',
          message: message || '',
          duration: type === 'error' ? 8000 : 5000,
        });
        return;
      }
      console.warn('AppAlert is not available:', message);
    },

    getCsrfToken() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
             document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1];
    },

    init() {
      this._initFileActions();
      // Listen for form submissions from dialogs
      window.addEventListener('create-folder', (e) => this.createFolder(e.detail.name));
      window.addEventListener('create-file', (e) => this.createFile(e.detail.name, e.detail.fileType, e.detail.customExt));
      window.addEventListener('rename-item', (e) => this.renameItem(e.detail.uuid, e.detail.name));
    }
  };
};

window.fileTableControls = function fileTableControls() {
  return {
    storageKey: 'fileTableControls:v2',
    searchQuery: '',
    typeFilter: 'all',
    sortField: 'default',
    sortDir: 'asc',
    columns: [
      { id: 'icon', label: 'Type', required: true },
      { id: 'name', label: 'Name', required: true },
      { id: 'favorite', label: 'Fav', required: false },
      { id: 'size', label: 'Size', required: false },
      { id: 'modified', label: 'Modified', required: false },
      { id: 'actions', label: 'Actions', required: false }
    ],
    defaultColumnOrder: ['icon', 'name', 'favorite', 'size', 'modified', 'actions'],
    defaultColumnVisibility: {
      icon: true,
      name: true,
      favorite: true,
      size: true,
      modified: true,
      actions: true
    },
    columnOrder: ['icon', 'name', 'favorite', 'size', 'modified', 'actions'],
    columnVisibility: {
      icon: true,
      name: true,
      favorite: true,
      size: true,
      modified: true,
      actions: true
    },
    table: null,
    tbody: null,
    originalRows: [],
    ready: false,

    get orderedColumns() {
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

      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons({ nodes: [this.$el] });
        }
      });

      this.$watch('searchQuery', () => this.applyRows());
      this.$watch('typeFilter', () => this.applyRows());
      this.$watch('sortField', () => this.applyRows());
      this.$watch('sortDir', () => this.applyRows());
    },

    openContextMenu(event, nodeData) {
      event.preventDefault();
      // Dispatch event for context menu to listen
      window.dispatchEvent(new CustomEvent('open-context-menu', {
        detail: { event, nodeData }
      }));
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

    loadState() {
      try {
        const raw = localStorage.getItem(this.storageKey);
        if (!raw) return;
        const data = JSON.parse(raw);
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
      } catch (error) {
        // Ignore malformed state
      }
    },

    saveState() {
      try {
        const payload = {
          sortField: this.sortField,
          sortDir: this.sortDir,
          columnOrder: this.columnOrder,
          columnVisibility: this.columnVisibility
        };
        localStorage.setItem(this.storageKey, JSON.stringify(payload));
      } catch (error) {
        // Ignore storage failures
      }
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
      this.sortField = 'default';
      this.sortDir = 'asc';
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
      if (query) {
        const name = row.dataset.name || '';
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

window.pinnedFoldersSection = function pinnedFoldersSection() {
  return {
    dragOver: false,
    dragCounter: 0,
    pinnedCount: 0,

    init() {
      // Set initial count from server-rendered list
      const list = document.getElementById('pinned-folders-list');
      this.pinnedCount = list ? list.children.length : 0;

      window.addEventListener('pinned-folders-changed', () => this.refreshPinnedSection());

      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons({ nodes: [this.$el] });
        }
      });
    },

    getCsrfToken() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
             document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1];
    },

    onDragOver(event) {
      if (!event.dataTransfer.types.includes('application/x-pin-folder')) return;
      event.dataTransfer.dropEffect = 'link';
    },

    onDragEnter(event) {
      if (!event.dataTransfer.types.includes('application/x-pin-folder')) return;
      this.dragCounter++;
      this.dragOver = true;
    },

    onDragLeave(event) {
      this.dragCounter--;
      if (this.dragCounter <= 0) {
        this.dragCounter = 0;
        this.dragOver = false;
      }
    },

    async onDrop(event) {
      this.dragOver = false;
      this.dragCounter = 0;
      const raw = event.dataTransfer.getData('application/x-pin-folder');
      if (!raw) return;
      try {
        const data = JSON.parse(raw);
        if (!data.uuid) return;
        const response = await fetch(`/api/v1/files/${data.uuid}/pin`, {
          method: 'POST',
          headers: { 'X-CSRFToken': this.getCsrfToken() }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          const refreshLink = document.querySelector('[data-refresh-folder-browser]');
          if (refreshLink) refreshLink.click();
        } else {
          let errData = {};
          try { errData = await response.json(); } catch (e) {}
          if (window.AppAlert) {
            window.AppAlert.show({ type: 'error', message: errData.detail || 'Failed to pin folder', duration: 5000 });
          }
        }
      } catch (error) {
        if (window.AppAlert) {
          window.AppAlert.show({ type: 'error', message: 'Failed to pin folder', duration: 5000 });
        }
      }
    },

    async refreshPinnedSection() {
      try {
        const response = await fetch('/files/pinned');
        if (!response.ok) return;
        const html = await response.text();
        const currentList = document.getElementById('pinned-folders-list');
        if (!currentList) return;

        // Parse HTML safely using DOMParser and extract children
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const newItems = Array.from(doc.body.children);

        // Replace content with parsed DOM nodes
        currentList.replaceChildren(...newItems);
        this.pinnedCount = currentList.querySelectorAll('li').length;

        // Re-initialize Lucide icons
        if (typeof lucide !== 'undefined') {
          lucide.createIcons({ nodes: [currentList] });
        }
        // Re-init Alpine on new content
        if (window.Alpine?.initTree) {
          window.Alpine.initTree(currentList);
        }
      } catch (error) {
        // Silent fail for sidebar refresh
      }
    }
  };
};
