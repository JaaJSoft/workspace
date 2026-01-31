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

// Global clipboard for cut/copy/paste operations
window.fileClipboard = {
  items: [],  // Array of {uuid, name, nodeType}
  mode: null, // 'cut' or 'copy'

  cut(items) {
    this.items = items;
    this.mode = 'cut';
    window.dispatchEvent(new CustomEvent('clipboard-changed'));
  },

  copy(items) {
    this.items = items;
    this.mode = 'copy';
    window.dispatchEvent(new CustomEvent('clipboard-changed'));
  },

  clear() {
    this.items = [];
    this.mode = null;
    window.dispatchEvent(new CustomEvent('clipboard-changed'));
  },

  hasItems() {
    return this.items.length > 0;
  },

  getItems() {
    return this.items;
  },

  getMode() {
    return this.mode;
  },

  isCut() {
    return this.mode === 'cut';
  },

  isCopy() {
    return this.mode === 'copy';
  }
};

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
          case 'cut':
            this.cutToClipboard([{ uuid, name, nodeType }]);
            break;
          case 'copy':
            this.copyToClipboard([{ uuid, name, nodeType }]);
            break;
          case 'paste':
            this.pasteFromClipboard();
            break;
        }
      });

      // Listen for folder icon changes (from properties modal)
      window.addEventListener('folder-icons-changed', () => {
        this.refreshFolderBrowser();
      });

      // Listen for folder background actions (from background context menu)
      window.addEventListener('folder-action', (e) => {
        const { action } = e.detail;
        switch (action) {
          case 'createFolder':
            this.showCreateFolderDialog();
            break;
          case 'createFile':
            this.showCreateFileDialog();
            break;
          case 'upload':
            this.triggerUpload();
            break;
          case 'paste':
            this.pasteFromClipboard();
            break;
        }
      });

      // Listen for bulk actions
      window.addEventListener('bulk-action', (e) => {
        const { action, uuids, add } = e.detail;
        switch (action) {
          case 'delete':
            this.bulkDeleteItems(uuids);
            break;
          case 'favorite':
            this.bulkToggleFavorite(uuids, add);
            break;
          case 'cut':
            this.bulkCutToClipboard(uuids);
            break;
          case 'copy':
            this.bulkCopyToClipboard(uuids);
            break;
          case 'paste':
            this.pasteFromClipboard();
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

    triggerUpload() {
      const input = document.getElementById('file-upload-input');
      if (input) {
        input.click();
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

    // Bulk actions
    async bulkDeleteItems(uuids) {
      if (!uuids || uuids.length === 0) return;

      const count = uuids.length;
      const confirmed = await AppDialog.confirm({
        title: 'Delete Items',
        message: `Are you sure you want to delete ${count} item${count > 1 ? 's' : ''}? They will be moved to trash.`,
        confirmText: 'Delete',
        confirmClass: 'btn-error'
      });

      if (!confirmed) return;

      let successCount = 0;
      let errorCount = 0;

      for (const uuid of uuids) {
        try {
          const response = await fetch(`/api/v1/files/${uuid}`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': this.getCsrfToken() }
          });
          if (response.ok) {
            successCount++;
          } else {
            errorCount++;
          }
        } catch (error) {
          errorCount++;
        }
      }

      if (errorCount > 0) {
        this.showAlert('warning', `Deleted ${successCount} items, ${errorCount} failed`);
      } else {
        this.showAlert('success', `Deleted ${successCount} item${successCount > 1 ? 's' : ''}`);
      }

      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
      this.refreshFolderBrowser();
    },

    async bulkToggleFavorite(uuids, add) {
      if (!uuids || uuids.length === 0) return;

      let successCount = 0;
      let errorCount = 0;

      for (const uuid of uuids) {
        try {
          const response = await fetch(`/api/v1/files/${uuid}/favorite`, {
            method: add ? 'POST' : 'DELETE',
            headers: { 'X-CSRFToken': this.getCsrfToken() }
          });
          if (response.ok) {
            successCount++;
          } else {
            errorCount++;
          }
        } catch (error) {
          errorCount++;
        }
      }

      const action = add ? 'Added to' : 'Removed from';
      if (errorCount > 0) {
        this.showAlert('warning', `${action} favorites: ${successCount} succeeded, ${errorCount} failed`);
      } else {
        this.showAlert('success', `${action} favorites: ${successCount} item${successCount > 1 ? 's' : ''}`);
      }

      window.dispatchEvent(new CustomEvent('clear-file-selection'));
      this.refreshFolderBrowser();
    },

    // Clipboard operations
    cutToClipboard(items) {
      if (!items || items.length === 0) return;
      window.fileClipboard.cut(items);
      const count = items.length;
      this.showAlert('info', `${count} item${count > 1 ? 's' : ''} cut to clipboard`);
    },

    copyToClipboard(items) {
      if (!items || items.length === 0) return;
      window.fileClipboard.copy(items);
      const count = items.length;
      this.showAlert('info', `${count} item${count > 1 ? 's' : ''} copied to clipboard`);
    },

    bulkCutToClipboard(uuids) {
      if (!uuids || uuids.length === 0) return;
      const items = this._getItemsFromUuids(uuids);
      this.cutToClipboard(items);
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },

    bulkCopyToClipboard(uuids) {
      if (!uuids || uuids.length === 0) return;
      const items = this._getItemsFromUuids(uuids);
      this.copyToClipboard(items);
      window.dispatchEvent(new CustomEvent('clear-file-selection'));
    },

    _getItemsFromUuids(uuids) {
      return uuids.map(uuid => {
        const row = document.querySelector(`tr[data-uuid="${uuid}"]`);
        return {
          uuid,
          name: row?.dataset.name || '',
          nodeType: row?.dataset.nodeType || 'file'
        };
      });
    },

    async pasteFromClipboard() {
      const items = window.fileClipboard.getItems();
      if (!items || items.length === 0) {
        this.showAlert('warning', 'Clipboard is empty');
        return;
      }

      const isCopy = window.fileClipboard.isCopy();
      const targetFolderId = this.currentFolder || null;
      let successCount = 0;
      let errorCount = 0;

      for (const item of items) {
        try {
          let response;
          if (isCopy) {
            // Copy: duplicate the file/folder
            response = await fetch(`/api/v1/files/${item.uuid}/copy`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCsrfToken()
              },
              body: JSON.stringify({ parent: targetFolderId })
            });
          } else {
            // Cut: move the file/folder
            response = await fetch(`/api/v1/files/${item.uuid}`, {
              method: 'PATCH',
              headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCsrfToken()
              },
              body: JSON.stringify({ parent: targetFolderId })
            });
          }
          if (response.ok) {
            successCount++;
          } else {
            errorCount++;
          }
        } catch (error) {
          errorCount++;
        }
      }

      const action = isCopy ? 'Copied' : 'Moved';
      if (errorCount > 0) {
        this.showAlert('warning', `${action} ${successCount} items, ${errorCount} failed`);
      } else {
        this.showAlert('success', `${action} ${successCount} item${successCount > 1 ? 's' : ''}`);
      }

      // Only clear clipboard on cut (move), keep it for copy
      if (!isCopy) {
        window.fileClipboard.clear();
      }
      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
      this.refreshFolderBrowser();
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
    storageKey: 'fileTableControls:v4',
    searchQuery: '',
    typeFilter: 'all',
    sortField: 'default',
    sortDir: 'asc',
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
    ready: false,
    _initializing: true,
    _saveTimer: null,

    // Selection state
    selectedUuids: new Set(),

    // Clipboard state
    hasClipboardItems: false,

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
      this._initializing = false;

      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons({ nodes: [this.$el] });
        }
      });

      this.$watch('searchQuery', () => this.applyRows());
      this.$watch('typeFilter', () => this.applyRows());
      this.$watch('sortField', () => this.applyRows());
      this.$watch('sortDir', () => this.applyRows());

      // Clear selection after bulk actions
      window.addEventListener('clear-file-selection', () => {
        this.clearSelection();
      });

      // Track clipboard state
      window.addEventListener('clipboard-changed', () => {
        this.hasClipboardItems = window.fileClipboard.hasItems();
      });
      this.hasClipboardItems = window.fileClipboard.hasItems();
    },

    openContextMenu(event, nodeData) {
      event.preventDefault();
      // Dispatch event for context menu to listen
      window.dispatchEvent(new CustomEvent('open-context-menu', {
        detail: { event, nodeData }
      }));
    },

    openBackgroundContextMenu(event) {
      // Check if click is on a row or interactive element
      const target = event.target;
      if (target.closest('tr[data-uuid], button, a, input, select, textarea')) {
        return; // Let the row handle its own context menu
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

    toggleRowSelection(uuid) {
      if (this.selectedUuids.has(uuid)) {
        this.selectedUuids.delete(uuid);
      } else {
        this.selectedUuids.add(uuid);
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

    get selectAllState() {
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

    // Bulk actions
    bulkDelete() {
      const uuids = this.getSelectedUuids();
      if (uuids.length === 0) return;
      window.dispatchEvent(new CustomEvent('bulk-action', {
        detail: { action: 'delete', uuids }
      }));
    },

    bulkFavorite(add) {
      const uuids = this.getSelectedUuids();
      if (uuids.length === 0) return;
      window.dispatchEvent(new CustomEvent('bulk-action', {
        detail: { action: 'favorite', uuids, add }
      }));
    },

    bulkCut() {
      const uuids = this.getSelectedUuids();
      if (uuids.length === 0) return;
      window.dispatchEvent(new CustomEvent('bulk-action', {
        detail: { action: 'cut', uuids }
      }));
    },

    bulkCopy() {
      const uuids = this.getSelectedUuids();
      if (uuids.length === 0) return;
      window.dispatchEvent(new CustomEvent('bulk-action', {
        detail: { action: 'copy', uuids }
      }));
    },

    bulkPaste() {
      window.dispatchEvent(new CustomEvent('bulk-action', {
        detail: { action: 'paste' }
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

      // 2. Fetch authoritative state from server, reconcile if different
      fetch('/api/v1/settings/files/table_controls')
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (!data || !data.value) return;
          this._applyStateData(data.value);
          this.pruneMissingColumns();
          this.applyAll();
          // Keep localStorage in sync
          this._saveToLocalStorage();
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
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
          || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
          || '';
        fetch('/api/v1/settings/files/table_controls', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ value: this._getStatePayload() }),
        }).catch(() => {});
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

window.folderIconPicker = function folderIconPicker(uuid, initialIcon, initialColor) {
  return {
    uuid: uuid,
    selectedIcon: initialIcon || 'folder',
    selectedColor: initialColor || 'text-warning',
    saving: false,
    saved: false,
    saveTimeout: null,

    init() {
      // Initialize static icons after DOM and x-for templates are ready
      setTimeout(() => lucide.createIcons(), 100);
    },

    icons: [
      'folder', 'folder-open', 'briefcase', 'archive', 'box',
      'book', 'bookmark', 'heart', 'star', 'flag',
      'home', 'building', 'camera', 'music', 'video',
      'image', 'file-text', 'code', 'database', 'server',
      'cloud', 'download', 'upload', 'settings', 'wrench',
      'lock', 'unlock', 'shield', 'key', 'user',
      'users', 'mail', 'send', 'inbox', 'calendar',
      'clock', 'zap', 'rocket', 'gift', 'shopping-bag',
      'circle-dollar-sign', 'credit-card', 'gamepad-2', 'graduation-cap', 'trophy'
    ],

    colors: [
      { name: 'Yellow', class: 'text-warning' },
      { name: 'Blue', class: 'text-info' },
      { name: 'Green', class: 'text-success' },
      { name: 'Red', class: 'text-error' },
      { name: 'Purple', class: 'text-secondary' },
      { name: 'Pink', class: 'text-pink-500' },
      { name: 'Orange', class: 'text-orange-500' },
      { name: 'Cyan', class: 'text-cyan-500' },
      { name: 'Gray', class: 'text-base-content/60' },
    ],

    selectIcon(icon) {
      this.selectedIcon = icon;
      this.save();
      this.updatePreviewIcon();
    },

    selectColor(color) {
      this.selectedColor = color;
      this.save();
      this.updatePreviewIcon();
    },

    updatePreviewIcon() {
      const container = this.$refs.previewIcon;
      if (!container) return;

      // Clear and recreate the icon element
      while (container.firstChild) {
        container.removeChild(container.firstChild);
      }

      const icon = document.createElement('i');
      icon.setAttribute('data-lucide', this.selectedIcon);
      icon.className = 'w-8 h-8 ' + this.selectedColor;
      container.appendChild(icon);

      this.$nextTick(() => {
        lucide.createIcons({ nodes: container.querySelectorAll('[data-lucide]') });
      });
    },

    async save() {
      // Debounce saves
      if (this.saveTimeout) clearTimeout(this.saveTimeout);
      this.saved = false;

      this.saveTimeout = setTimeout(async () => {
        this.saving = true;
        try {
          const response = await fetch(`/api/v1/files/${this.uuid}`, {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': this.getCsrfToken()
            },
            body: JSON.stringify({
              icon: this.selectedIcon,
              color: this.selectedColor
            })
          });

          if (response.ok) {
            this.saved = true;
            // Trigger pinned folders and folder browser refresh
            window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
            window.dispatchEvent(new CustomEvent('folder-icons-changed'));
            setTimeout(() => { this.saved = false; }, 2000);
          }
        } catch (error) {
          console.error('Failed to save icon:', error);
        } finally {
          this.saving = false;
        }
      }, 300);
    },

    getCsrfToken() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
             document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1];
    }
  };
};

window.pinnedFoldersSection = function pinnedFoldersSection() {
  return {
    dragOver: false,
    dragCounter: 0,
    pinnedCount: 0,
    // For reordering pinned folders
    draggingPinned: null,
    dragOverPinned: null,

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
    },

    // Pinned folder reordering methods
    onPinnedDragStart(event, uuid) {
      this.draggingPinned = uuid;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('application/x-reorder-pinned', uuid);
    },

    onPinnedDragEnd(event) {
      this.draggingPinned = null;
      this.dragOverPinned = null;
    },

    onPinnedDragOver(event, uuid) {
      if (!this.draggingPinned || this.draggingPinned === uuid) {
        this.dragOverPinned = null;
        return;
      }
      event.dataTransfer.dropEffect = 'move';
      this.dragOverPinned = uuid;
    },

    async onPinnedDrop(event, targetUuid) {
      const sourceUuid = this.draggingPinned;
      if (!sourceUuid || sourceUuid === targetUuid) {
        this.draggingPinned = null;
        this.dragOverPinned = null;
        return;
      }

      // Get current order from DOM
      const list = document.getElementById('pinned-folders-list');
      const items = Array.from(list.querySelectorAll('li.pinned-folder-item'));
      const uuids = items.map(li => li.dataset.pinnedUuid);

      // Calculate new order
      const sourceIndex = uuids.indexOf(sourceUuid);
      const targetIndex = uuids.indexOf(targetUuid);
      if (sourceIndex === -1 || targetIndex === -1) return;

      // Remove source and insert at target position
      uuids.splice(sourceIndex, 1);
      uuids.splice(targetIndex, 0, sourceUuid);

      // Optimistically reorder in DOM
      const sourceItem = items[sourceIndex];
      const targetItem = items[targetIndex];
      if (sourceIndex < targetIndex) {
        targetItem.after(sourceItem);
      } else {
        targetItem.before(sourceItem);
      }

      this.draggingPinned = null;
      this.dragOverPinned = null;

      // Save new order to server
      try {
        const response = await fetch('/api/v1/files/pinned/reorder', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': this.getCsrfToken()
          },
          body: JSON.stringify({ order: uuids })
        });
        if (!response.ok) {
          // Revert on error - refresh from server
          this.refreshPinnedSection();
        }
      } catch (error) {
        // Revert on error - refresh from server
        this.refreshPinnedSection();
      }
    }
  };
};
