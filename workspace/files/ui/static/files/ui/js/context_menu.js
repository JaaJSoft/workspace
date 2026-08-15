window.contextMenu = function contextMenu() {
  return {
    isOpen: false,
    position: { x: 0, y: 0 },
    nodeData: null,
    isBackgroundMenu: false,
    hasClipboardItems: false,

    get isBulkSelection() {
      return !!(this.nodeData
        && Array.isArray(this.nodeData.selectionUuids)
        && this.nodeData.selectionUuids.length > 1);
    },

    init() {
      // Listen for context menu events (on files/folders)
      window.addEventListener('open-context-menu', (e) => {
        this.open(e.detail.event, e.detail.nodeData);
      });

      // Listen for background context menu events (on empty area)
      window.addEventListener('open-background-context-menu', (e) => {
        this.openBackground(e.detail.event);
      });

      // Listen for clipboard changes
      window.addEventListener('clipboard-changed', () => {
        this.hasClipboardItems = window.fileClipboard.hasItems();
      });

      // Initialize clipboard state
      this.hasClipboardItems = window.fileClipboard.hasItems();
    },

    open(event, nodeData) {
      event.preventDefault();

      // Update node data first
      this.nodeData = nodeData;
      this.isBackgroundMenu = false;

      // If already open, just update position and return
      if (this.isOpen) {
        this.updatePosition(event);
        return;
      }

      this.isOpen = true;

      // Calculate position to avoid overflow
      this.$nextTick(() => {
        this.updatePosition(event);
      });
    },

    openBackground(event) {
      event.preventDefault();

      this.nodeData = null;
      this.isBackgroundMenu = true;

      if (this.isOpen) {
        this.updatePosition(event);
        return;
      }

      this.isOpen = true;

      this.$nextTick(() => {
        this.updatePosition(event);
      });
    },

    updatePosition(event) {
      const menu = this.$el;
      const menuRect = menu.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;

      let x = event.clientX;
      let y = event.clientY;

      // Adjust if menu would overflow right edge
      if (x + menuRect.width > viewportWidth) {
        x = viewportWidth - menuRect.width - 10;
      }

      // Adjust if menu would overflow bottom edge
      if (y + menuRect.height > viewportHeight) {
        y = viewportHeight - menuRect.height - 10;
      }

      this.position = { x, y };
    },

    close() {
      this.isOpen = false;
      this.nodeData = null;
    },

    iconClass(action) {
      if (action.id === 'toggle_favorite' && action.state && action.state.is_favorite) {
        return 'text-warning fill-current';
      }
      if (action.id === 'toggle_pin' && action.state && action.state.is_pinned) {
        return 'text-primary fill-current';
      }
      return '';
    },

    executeAction(action) {
      const nd = this.nodeData;

      // Multi-selection: route through the bulk-action event bus so the
      // chosen action applies to every selected file, not just the one
      // under the cursor.
      if (this.isBulkSelection) {
        const uuids = nd.selectionUuids;
        let detail = null;
        switch (action.id) {
          case 'toggle_favorite':
            detail = { action: 'favorite', uuids, add: !!action._bulkAdd };
            break;
          case 'toggle_pin':
            detail = { action: 'pin', uuids, add: !!action._bulkAdd };
            break;
          case 'delete':
          case 'restore':
          case 'purge':
          case 'cut':
          case 'copy':
          case 'download':
            detail = { action: action.id, uuids };
            break;
        }
        if (detail) {
          window.dispatchEvent(new CustomEvent('bulk-action', { detail }));
        }
        this.close();
        return;
      }

      switch (action.id) {
        case 'view':
          window.dispatchEvent(new CustomEvent('open-file-viewer', {
            detail: { uuid: nd.uuid, name: nd.name, type: nd.fileType }
          }));
          break;
        case 'open':
          document.querySelector(`a[href="/files/${nd.uuid}"]`)?.click();
          break;
        case 'copy_link': {
          const url = new URL(window.location.origin + window.location.pathname);
          url.searchParams.set('open', nd.uuid);
          navigator.clipboard.writeText(url.toString()).then(() => {
            if (window.AppAlert) {
              window.AppAlert.success('Link copied to clipboard', { duration: 2000 });
            }
          }).catch(err => {
            console.error('Failed to copy link:', err);
            if (window.AppAlert) {
              window.AppAlert.error('Failed to copy link');
            }
          });
          break;
        }
        case 'share':
          window.dispatchEvent(new CustomEvent('open-share-modal', {
            detail: { uuid: nd.uuid, name: nd.name }
          }));
          break;
        case 'properties':
          window.dispatchEvent(new CustomEvent('open-properties', {
            detail: { uuid: nd.uuid, nodeType: nd.nodeType }
          }));
          break;
        default:
          // All other actions dispatch file-action event
          window.dispatchEvent(new CustomEvent('file-action', {
            detail: {
              action: action.id,
              uuid: nd.uuid,
              name: nd.name,
              nodeType: nd.nodeType,
              isGroupFolder: !!nd.isGroupFolder,
              state: action.state || {}
            }
          }));
      }
      this.close();
    },

    // Background menu actions
    createFolder() {
      window.dispatchEvent(new CustomEvent('folder-action', {
        detail: { action: 'createFolder' }
      }));
      this.close();
    },

    createFile() {
      window.dispatchEvent(new CustomEvent('folder-action', {
        detail: { action: 'createFile' }
      }));
      this.close();
    },

    uploadFiles() {
      window.dispatchEvent(new CustomEvent('folder-action', {
        detail: { action: 'upload' }
      }));
      this.close();
    },

    pasteHere() {
      window.dispatchEvent(new CustomEvent('folder-action', {
        detail: { action: 'paste' }
      }));
      this.close();
    }
  };
};
