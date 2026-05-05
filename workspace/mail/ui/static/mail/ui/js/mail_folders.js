// Mail folders: load, tree, expand/collapse, selection, refresh, classify,
// folder context menu actions (rename, move, hide, delete, restore),
// folder icon picker, drag-and-drop targets, hidden folders dialog.
window.mailFoldersMixin = function mailFoldersMixin() {
  return {
    // ----- Folders -----
    async loadFolders(accountUuid) {
      const res = await this._fetch(`/api/v1/mail/folders?account=${accountUuid}`);
      if (res.ok) {
        const data = await res.json();
        this.folders[accountUuid] = data;
      }

    },

    getFolders(accountUuid) {
      const flds = this.folders[accountUuid] || [];
      // Sort: inbox first, then by type, then alpha
      const order = ['inbox', 'drafts', 'sent', 'archive', 'spam', 'trash', 'other'];
      return [...flds].sort((a, b) => {
        const ai = order.indexOf(a.folder_type);
        const bi = order.indexOf(b.folder_type);
        if (ai !== bi) return ai - bi;
        return a.display_name.localeCompare(b.display_name);
      });
    },

    toggleAccountExpanded(uuid) {
      this.expandedAccounts[uuid] = !this.expandedAccounts[uuid];

    },

    folderIcon(type) {
      const map = {
        inbox: 'inbox',
        sent: 'send',
        drafts: 'file-edit',
        trash: 'trash-2',
        spam: 'alert-triangle',
        archive: 'archive',
        other: 'folder',
      };
      return map[type] || 'folder';
    },

    // ----- Folder tree -----
    getFolderTree(accountUuid) {
      const flds = this.folders[accountUuid] || [];
      const specialTypes = new Set(['inbox', 'sent', 'drafts', 'trash', 'spam', 'archive']);
      const typeOrder = ['inbox', 'drafts', 'sent', 'archive', 'spam', 'trash', 'other'];

      // Separate special vs other folders
      const special = [];
      const other = [];
      for (const f of flds) {
        if (specialTypes.has(f.folder_type)) special.push(f);
        else other.push(f);
      }

      // Sort special by type order
      special.sort((a, b) => typeOrder.indexOf(a.folder_type) - typeOrder.indexOf(b.folder_type));

      // Sort "other" by full IMAP name so parents always come before children
      other.sort((a, b) => a.name.localeCompare(b.name));

      const tree = [];
      const nodeMap = {};

      // Add special folders at root
      for (const folder of special) {
        const node = { folder, children: [], depth: 0 };
        tree.push(node);
        nodeMap[folder.name] = node;
      }

      // Build hierarchy for "other" folders
      for (const folder of other) {
        const sep = folder.name.includes('/') ? '/' : (folder.name.includes('.') ? '.' : null);
        let parentNode = null;
        let depth = 0;
        if (sep) {
          const lastSep = folder.name.lastIndexOf(sep);
          if (lastSep > 0) {
            const parentName = folder.name.substring(0, lastSep);
            parentNode = nodeMap[parentName];
            if (parentNode) {
              depth = parentNode.depth + 1;
            }
          }
        }

        const node = { folder, children: [], depth };
        nodeMap[folder.name] = node;

        if (parentNode) {
          parentNode.children.push(node);
        } else {
          tree.push(node);
        }
      }

      return tree;
    },

    _flattenTree(nodes) {
      const result = [];
      for (const node of nodes) {
        result.push(node);
        // Default to expanded for parent folders (unless explicitly collapsed)
        const expanded = node.children.length > 0 &&
          (this.expandedFolders[node.folder.name] === undefined || this.expandedFolders[node.folder.name]);
        if (expanded) {
          result.push(...this._flattenTree(node.children));
        }
      }
      return result;
    },

    getFlatFolderTree(accountUuid) {
      return this._flattenTree(this.getFolderTree(accountUuid));
    },

    toggleFolderExpanded(folderName) {
      // Default is expanded (true), so toggling undefined -> false
      const current = this.expandedFolders[folderName] === undefined ? true : this.expandedFolders[folderName];
      this.expandedFolders[folderName] = !current;

    },

    getSubtreeUnreadCount(node) {
      let count = node.folder.unread_count || 0;
      for (const child of node.children) {
        count += this.getSubtreeUnreadCount(child);
      }
      return count;
    },

    // ----- Folder selection -----
    async selectUnifiedInbox() {
      this.unifiedInbox = true;
      this.selectedFolder = null;
      this.selectedLabel = null;
      this.selectedMessage = null;
      this.messageDetail = null;
      this._updateUrl(null, {push: this.isMobile()});
      this.selectedMessages = [];
      this.currentPage = 1;
      this._resetFilters();
      this._closeDrawerOnMobile();
      await this.loadMessages();
    },

    getTotalInboxUnread() {
      let total = 0;
      for (const accId in this.folders) {
        for (const f of this.folders[accId]) {
          if (f.folder_type === 'inbox') total += (f.unread_count || 0);
        }
      }
      return total;
    },

    async selectFolder(folder) {
      this.unifiedInbox = false;
      this.selectedFolder = folder;
      this.selectedLabel = null;
      this.selectedMessage = null;
      this.messageDetail = null;
      this._updateUrl(null, {push: this.isMobile()});
      this.selectedMessages = [];
      this.currentPage = 1;
      this._resetFilters();
      this._closeDrawerOnMobile();
      await this.loadMessages();
    },

    async refreshFolder() {
      if (this.unifiedInbox) {
        // Sync all accounts in parallel
        await Promise.all(this.accounts.map(acc => this.syncAccount(acc.uuid)));
        this.loadMessages();
        return;
      }
      const accountUuid = this.selectedFolder?.account_id || this.selectedLabel?.account_id;
      if (!accountUuid) return;
      await this.syncAccount(accountUuid);
      if (this.selectedLabel) {
        await this.fetchLabels(accountUuid);
        this.loadMessages();
      }
    },

    async classifyFolder() {
      if (this.classifyingFolder) return;
      this.classifyingFolder = true;
      try {
        const csrfToken = getCSRFToken();
        const body = {};
        if (this.selectedFolder) body.folder_id = this.selectedFolder.uuid;
        const resp = await fetch('/api/v1/ai/tasks/mail/classify', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify(body),
        });
        if (resp.ok) {
          setTimeout(() => this.loadMessages(), 3000);
        }
      } catch (e) {
        console.warn('Classify failed:', e);
      } finally {
        this.classifyingFolder = false;
      }
    },

    // ----- Folder context menu -----
    openFolderContextMenu(event, folder) {
      event.preventDefault();
      const menu = document.getElementById('folder-context-menu');
      if (!menu) return;

      this.folderCtx.folder = folder;
      this.folderCtx.open = true;

      this.$nextTick(() => {
        const rect = menu.getBoundingClientRect();
        let x = event.clientX;
        let y = event.clientY;
        if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 10;
        if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 10;
        this.folderCtx.x = x;
        this.folderCtx.y = y;

      });
    },

    async folderCtxAction(action) {
      const folder = this.folderCtx.folder;
      this.folderCtx.open = false;
      if (!folder) return;

      switch (action) {
        case 'refresh':
          this.selectFolder(folder);
          break;
        case 'mark_all_read':
          await this._markFolderAllRead(folder);
          break;
        case 'change_icon':
          this.showFolderIconPicker(folder);
          break;
        case 'create':
          await this._createFolder(folder.account_id, folder);
          break;
        case 'rename':
          await this._renameFolder(folder);
          break;
        case 'move':
          await this._moveFolder(folder);
          break;
        case 'hide':
          await this._hideFolder(folder);
          break;
        case 'delete':
          await this._deleteFolder(folder);
          break;
        case 'sync':
          this.syncAccount(folder.account_id);
          break;
      }
    },

    async _markFolderAllRead(folder) {
      const res = await this._fetch(`/api/v1/mail/folders/${folder.uuid}/mark-read`, {
        method: 'POST',
      });
      if (res.ok) {
        // Update local state
        folder.unread_count = 0;
        this.messages.forEach(m => { m.is_read = true; });
        if (this.messageDetail) this.messageDetail.is_read = true;
      }
    },

    async _createFolder(accountUuid, parentFolder) {
      const isSubfolder = parentFolder && parentFolder.folder_type === 'other';
      const title = isSubfolder ? 'New subfolder' : 'New folder';
      const message = isSubfolder
        ? `Create a subfolder in "${parentFolder.display_name}".`
        : 'Enter a name for the new folder.';

      const name = await AppDialog.prompt({
        title,
        message,
        placeholder: 'Folder name',
        okLabel: 'Create',
        okClass: 'btn-warning',
        icon: 'folder-plus',
        iconClass: 'bg-warning/10 text-warning',
      });
      if (!name) return;

      const body = { account_id: accountUuid, name };
      if (isSubfolder) {
        body.parent_name = parentFolder.name;
      }

      const res = await this._fetch('/api/v1/mail/folders', {
        method: 'POST',
        body,
      });

      if (res.ok) {
        // Auto-expand parent so the new subfolder is visible
        if (isSubfolder) {
          this.expandedFolders[parentFolder.name] = true;
        }
        await this.loadFolders(accountUuid);

      } else {
        const data = await res.json().catch(() => ({}));
        await AppDialog.error({ message: data.detail || 'Failed to create folder' });
      }
    },

    async _renameFolder(folder) {
      if (folder.folder_type !== 'other') return;

      const name = await AppDialog.prompt({
        title: 'Rename folder',
        value: folder.display_name,
        okLabel: 'Rename',
        okClass: 'btn-warning',
        icon: 'pencil',
        iconClass: 'bg-warning/10 text-warning',
      });
      if (!name || name === folder.display_name) return;

      const res = await this._fetch(`/api/v1/mail/folders/${folder.uuid}`, {
        method: 'PATCH',
        body: { display_name: name },
      });

      if (res.ok) {
        const updated = await res.json();
        // Update local state
        const flds = this.folders[folder.account_id] || [];
        const idx = flds.findIndex(f => f.uuid === folder.uuid);
        if (idx !== -1) flds[idx] = { ...flds[idx], ...updated };
        if (this.selectedFolder?.uuid === folder.uuid) {
          Object.assign(this.selectedFolder, updated);
        }

      } else {
        const data = await res.json().catch(() => ({}));
        await AppDialog.error({ message: data.detail || 'Failed to rename folder' });
      }
    },

    async _deleteFolder(folder) {
      if (folder.folder_type !== 'other') return;

      const ok = await AppDialog.confirm({
        title: 'Delete folder',
        message: `Delete "${folder.display_name}" and all its messages? This cannot be undone.`,
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;

      const res = await this._fetch(`/api/v1/mail/folders/${folder.uuid}`, {
        method: 'DELETE',
      });

      if (res.ok) {
        // Remove from local state
        const flds = this.folders[folder.account_id];
        if (flds) {
          this.folders[folder.account_id] = flds.filter(f => f.uuid !== folder.uuid);
        }
        if (this.selectedFolder?.uuid === folder.uuid) {
          this.selectedFolder = null;
          this.messages = [];
          this.selectedMessage = null;
          this.messageDetail = null;
          this._updateUrl(null);
        }

      } else {
        const data = await res.json().catch(() => ({}));
        await AppDialog.error({ message: data.detail || 'Failed to delete folder' });
      }
    },

    async _moveFolder(folder) {
      if (folder.folder_type !== 'other') return;

      const accountUuid = folder.account_id;
      const allFolders = this.folders[accountUuid] || [];

      // Collect own descendants (cannot move into self or own children).
      // IMAP delimiters vary by server (Gmail uses '/', Dovecot often '.').
      // The real delimiter is on the account but not exposed in the folder
      // serializer, so we test both common separators - the names of a given
      // account's children only use one of them, so no false positives in
      // practice.
      const ownPrefixes = [folder.name + '/', folder.name + '.'];
      const excluded = new Set([folder.uuid]);
      for (const f of allFolders) {
        if (ownPrefixes.some(p => f.name.startsWith(p))) excluded.add(f.uuid);
      }

      // Build target options: root + eligible folders
      const options = [{ label: '/ (Root)', value: '' }];
      const tree = this.getFolderTree(accountUuid);
      const flatten = (nodes, depth) => {
        for (const node of nodes) {
          if (!excluded.has(node.folder.uuid)) {
            const indent = '\u00A0\u00A0'.repeat(depth);
            options.push({
              label: indent + node.folder.display_name,
              value: node.folder.name,
            });
          }
          flatten(node.children, depth + 1);
        }
      };
      flatten(tree, 0);

      // Determine current parent for pre-selection. The IMAP delimiter is
      // not exposed at this layer, so check both common separators ('/' and
      // '.') and use whichever appears - same heuristic as ownPrefixes above.
      const lastSep = Math.max(
        folder.name.lastIndexOf('/'),
        folder.name.lastIndexOf('.'),
      );
      const currentParent = lastSep > 0 ? folder.name.substring(0, lastSep) : '';

      const selected = await AppDialog.select({
        title: 'Move folder',
        message: `Move "${folder.display_name}" to:`,
        options,
        value: currentParent,
        okLabel: 'Move',
        okClass: 'btn-warning',
        icon: 'folder-input',
        iconClass: 'bg-warning/10 text-warning',
      });
      if (selected === null || selected === undefined) return;

      const res = await this._fetch(`/api/v1/mail/folders/${folder.uuid}`, {
        method: 'PATCH',
        body: { parent_name: selected },
      });

      if (res.ok) {
        await this.loadFolders(accountUuid);
        // If the moved folder was selected, update reference
        if (this.selectedFolder?.uuid === folder.uuid) {
          const flds = this.folders[accountUuid] || [];
          const updated = flds.find(f => f.uuid === folder.uuid);
          if (updated) this.selectedFolder = updated;
        }

      } else {
        const data = await res.json().catch(() => ({}));
        await AppDialog.error({ message: data.detail || 'Failed to move folder' });
      }
    },

    // ----- Hide/unhide folders -----
    async _hideFolder(folder) {
      if (folder.folder_type !== 'other') return;

      const ok = await AppDialog.confirm({
        title: 'Hide folder',
        message: `Hide "${folder.display_name}"? It will no longer appear in the sidebar or search results. You can restore it from the account menu.`,
        okLabel: 'Hide',
        okClass: 'btn-warning',
        icon: 'eye-off',
        iconClass: 'bg-warning/10 text-warning',
      });
      if (!ok) return;

      const res = await this._fetch(`/api/v1/mail/folders/${folder.uuid}`, {
        method: 'PATCH',
        body: { is_hidden: true },
      });

      if (res.ok) {
        const flds = this.folders[folder.account_id];
        if (flds) {
          this.folders[folder.account_id] = flds.filter(f => f.uuid !== folder.uuid);
        }
        if (this.selectedFolder?.uuid === folder.uuid) {
          this.selectedFolder = null;
          this.messages = [];
          this.selectedMessage = null;
          this.messageDetail = null;
          this._updateUrl(null);
        }

      } else {
        const data = await res.json().catch(() => ({}));
        await AppDialog.error({ message: data.detail || 'Failed to hide folder' });
      }
    },

    async _showHiddenFolders(account) {
      const res = await this._fetch(`/api/v1/mail/folders?account=${account.uuid}&show_hidden=true`);
      if (!res.ok) return;
      const allFolders = await res.json();
      this.hiddenFolders = allFolders.filter(f => f.is_hidden);
      this.hiddenFoldersSearch = '';
      document.getElementById('mail-hidden-folders-dialog').showModal();

    },

    async restoreFolder(folder) {
      const res = await this._fetch(`/api/v1/mail/folders/${folder.uuid}`, {
        method: 'PATCH',
        body: { is_hidden: false },
      });
      if (res.ok) {
        this.hiddenFolders = this.hiddenFolders.filter(f => f.uuid !== folder.uuid);
        await this.loadFolders(folder.account_id);

        if (this.hiddenFolders.length === 0) {
          document.getElementById('mail-hidden-folders-dialog').close();
        }
      } else {
        const data = await res.json().catch(() => ({}));
        await AppDialog.error({ message: data.detail || 'Failed to restore folder' });
      }
    },

    // ----- Folder icon picker -----
    showFolderIconPicker(folder) {
      this.folderIconEdit = {
        uuid: folder.uuid,
        name: folder.display_name,
        icon: folder.icon || null,
        color: folder.color || null,
      };
      document.getElementById('mail-folder-icon-dialog').showModal();

    },

    onFolderIconSaved(icon, color) {
      // Update the folder in local state
      for (const accountUuid of Object.keys(this.folders)) {
        const flds = this.folders[accountUuid];
        if (!flds) continue;
        const folder = flds.find(f => f.uuid === this.folderIconEdit.uuid);
        if (folder) {
          folder.icon = icon;
          folder.color = color;
          break;
        }
      }
      if (this.selectedFolder?.uuid === this.folderIconEdit.uuid) {
        this.selectedFolder.icon = icon;
        this.selectedFolder.color = color;
      }
    },

    // ----- Move target list -----
    getMoveTargetFolders(msg) {
      if (!msg) return [];
      const accountId = msg.account_id || this.selectedFolder?.account_id;
      if (!accountId) return [];
      const currentFolderId = msg.folder_id || this.selectedFolder?.uuid;
      const tree = this.getFolderTree(accountId);
      const result = [];
      const flatten = (nodes, depth) => {
        for (const node of nodes) {
          if (node.folder.uuid !== currentFolderId) {
            result.push({ ...node.folder, _depth: depth });
          }
          flatten(node.children, depth + 1);
        }
      };
      flatten(tree, 0);
      return result;
    },

    // ----- Drag & drop (folders) -----
    onMsgDragStart(event, msg) {
      // If the dragged message is in the selection, drag all selected; otherwise just this one
      let ids;
      if (this.selectedMessages.length > 0 && this.selectedMessages.includes(msg.uuid)) {
        ids = [...this.selectedMessages];
      } else {
        ids = [msg.uuid];
      }
      this._draggingMsgIds = ids;
      event.dataTransfer.effectAllowed = 'copyMove';
      event.dataTransfer.setData('text/plain', JSON.stringify(ids));
      // Custom drag image label
      const count = ids.length;
      const label = document.createElement('div');
      label.textContent = count === 1 ? '1 message' : `${count} messages`;
      label.className = 'badge badge-warning badge-sm';
      label.style.position = 'absolute';
      label.style.top = '-9999px';
      document.body.appendChild(label);
      event.dataTransfer.setDragImage(label, 0, 0);
      setTimeout(() => label.remove(), 0);
    },

    onMsgDragEnd(event) {
      this._draggingMsgIds = null;
      this.dragOverFolder = null;
      this.dragOverLabel = null;
    },

    onFolderDragOver(event, folder) {
      // Only highlight if it's not the current folder
      if (folder.uuid !== this.selectedFolder?.uuid) {
        event.dataTransfer.dropEffect = 'move';
        this.dragOverFolder = folder;
      } else {
        event.dataTransfer.dropEffect = 'none';
      }
    },

    onFolderDragLeave(event, folder) {
      if (this.dragOverFolder?.uuid === folder.uuid) {
        this.dragOverFolder = null;
      }
    },

    onFolderDrop(event, folder) {
      this.dragOverFolder = null;
      if (folder.uuid === this.selectedFolder?.uuid) return;
      try {
        const raw = event.dataTransfer.getData('text/plain');
        const ids = JSON.parse(raw);
        if (Array.isArray(ids) && ids.length > 0) {
          this.moveMessages(ids, folder);
        }
      } catch (e) {}
      this._draggingMsgIds = null;
    },
  };
};
