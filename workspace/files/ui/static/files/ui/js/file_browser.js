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
      if (['1', 'true', 'yes'].includes(favorites)) {
        this.activeView = 'favorites';
        return;
      }
      if (path === '/files') {
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
        message: `Are you sure you want to delete "${name}"?${nodeType === 'folder' ? ' This will also delete all contents.' : ''}`,
        okLabel: 'Delete',
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
          this.refreshFolderBrowser();
        } else {
          this.showAlert('error', 'Failed to delete');
        }
      } catch (error) {
        this.showAlert('error', 'Failed to delete');
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
      const alert = InlineAlert.create({ type, message, dismissible: true, className: 'mb-4' });
      this.$refs.alertsContainer.appendChild(alert);
    },

    getCsrfToken() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
             document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1];
    },

    init() {
      // Listen for form submissions from dialogs
      window.addEventListener('create-folder', (e) => this.createFolder(e.detail.name));
      window.addEventListener('create-file', (e) => this.createFile(e.detail.name, e.detail.fileType, e.detail.customExt));
      window.addEventListener('rename-item', (e) => this.renameItem(e.detail.uuid, e.detail.name));
    }
  };
};
