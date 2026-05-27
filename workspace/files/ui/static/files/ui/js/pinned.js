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
      window.addEventListener('group-folders-changed', () => this.refreshGroupFoldersSection());

      // Listen for pinned folder context menu events
      window.addEventListener('open-pinned-folder-context-menu', (e) => {
        this.openContextMenu(e.detail.event, {
          uuid: e.detail.uuid,
          name: e.detail.name,
          nodeType: 'folder',
          fileType: '',
          isFavorite: e.detail.isFavorite,
          isViewable: false,
          isTrash: false,
          isPinned: true
        });
      });

      // Listen for group folder context menu events
      window.addEventListener('open-group-folder-context-menu', (e) => {
        this.openContextMenu(e.detail.event, {
          uuid: e.detail.uuid,
          name: e.detail.name,
          nodeType: 'folder',
          fileType: '',
          isFavorite: e.detail.isFavorite,
          isViewable: false,
          isTrash: false,
          isPinned: false,
          isGroupFolder: true,
        });
      });

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
          headers: { 'X-CSRFToken': getCSRFToken() }
        });
        if (response.ok) {
          window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
          this.$ajax(window.location.pathname + window.location.search, { target: 'folder-browser' });
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

    refreshPinnedSection() {
      this.$el.addEventListener('ajax:after', () => {
        const list = document.getElementById('pinned-folders-list');
        if (list) this.pinnedCount = list.querySelectorAll('li').length;
      }, { once: true });
      this.$ajax('/files/pinned', { target: 'pinned-folders-list' });
    },

    refreshGroupFoldersSection() {
      this.$ajax('/files/group-folders', { target: 'group-folders-section' });
    },

    async openContextMenu(event, nodeData) {
      event.preventDefault();

      // Load actions for the pinned folder
      try {
        const csrfToken = getCSRFToken();
        const resp = await fetch('/api/v1/files/actions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ uuids: [nodeData.uuid] }),
        });
        if (resp.ok) {
          const actionsMap = await resp.json();
          nodeData.actions = actionsMap[nodeData.uuid] || [];
        }
      } catch (e) {
        // Fallback to empty actions
        nodeData.actions = [];
      }

      window.dispatchEvent(new CustomEvent('open-context-menu', {
        detail: { event, nodeData }
      }));
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
            'X-CSRFToken': getCSRFToken()
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
