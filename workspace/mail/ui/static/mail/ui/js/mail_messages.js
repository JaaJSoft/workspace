// Mail messages: list, pagination, selection, flags (read/star), delete,
// batch actions, filters, search, message context menu, attachment save,
// move messages, move dialog, message navigation.
window.mailMessagesMixin = function mailMessagesMixin() {
  return {
    // ----- Messages -----
    _getAccountEmail(accountId) {
      const acc = this.accounts.find(a => a.uuid === accountId);
      return acc ? (acc.display_name || acc.email) : '';
    },

    _buildMessagesUrl() {
      let url;
      if (this.unifiedInbox) {
        url = `/api/v1/mail/messages?inbox=all&page=${this.currentPage}`;
      } else if (this.selectedLabel) {
        url = `/api/v1/mail/messages?label=${this.selectedLabel.uuid}&page=${this.currentPage}`;
      } else {
        url = `/api/v1/mail/messages?folder=${this.selectedFolder.uuid}&page=${this.currentPage}`;
      }
      if (this.filters.search) url += `&search=${encodeURIComponent(this.filters.search)}`;
      if (this.filters.unread) url += '&unread=1';
      if (this.filters.starred) url += '&starred=1';
      if (this.filters.attachments) url += '&attachments=1';
      return url;
    },

    async loadMessages() {
      if (!this.selectedFolder && !this.selectedLabel && !this.unifiedInbox) return;
      this.loadingMessages = true;
      const res = await this._fetch(this._buildMessagesUrl());
      if (res.ok) {
        const data = await res.json();
        this.messages = data.results;
        this.totalMessages = data.count;
        this.hasMoreMessages = data.count > this.currentPage * data.page_size;
      }
      this.loadingMessages = false;

    },

    async loadMoreMessages() {
      this.loadingMoreMessages = true;
      this.currentPage++;
      const res = await this._fetch(this._buildMessagesUrl());
      if (res.ok) {
        const data = await res.json();
        this.messages = [...this.messages, ...data.results];
        this.hasMoreMessages = data.count > this.currentPage * data.page_size;
      }
      this.loadingMoreMessages = false;

    },

    // ----- Filters -----
    _hasActiveFilters() {
      return !!(this.filters.search || this.filters.unread || this.filters.starred || this.filters.attachments);
    },

    _resetFilters() {
      this.filters = { search: '', unread: false, starred: false, attachments: false };
      if (this._searchTimer) { clearTimeout(this._searchTimer); this._searchTimer = null; }
    },

    applyFilters() {
      this.currentPage = 1;
      this.selectedMessages = [];
      this.loadMessages();
    },

    toggleFilter(name) {
      this.filters[name] = !this.filters[name];
      this.applyFilters();
    },

    onSearchInput() {
      if (this._searchTimer) clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => this.applyFilters(), 400);
    },

    clearFilters() {
      this._resetFilters();
      this.applyFilters();
    },

    // ----- Message detail -----
    async selectMessage(msg) {
      // Clear AI state from previous message
      this.aiSummary = null;
      this.aiSummarizing = false;
      if (this._aiPollInterval) clearInterval(this._aiPollInterval);

      this.selectedMessage = msg;
      this.loadingDetail = true;
      this._updateUrl(msg.uuid, {push: this.isMobile()});
      const res = await this._fetch(`/api/v1/mail/messages/${msg.uuid}`);
      if (res.ok) {
        this.messageDetail = await res.json();
        if (this.messageDetail.ai_summary_html) {
          this.aiSummary = this.messageDetail.ai_summary_html;
        }
        // Auto-mark as read
        if (!msg.is_read) {
          this.toggleRead(this.messageDetail, true);
          msg.is_read = true;
        }
      }
      this.loadingDetail = false;

    },

    async _openMessageById(uuid) {
      if (!isValidUuid(uuid)) return;
      const res = await this._fetch(`/api/v1/mail/messages/${uuid}`);
      if (res.ok) {
        this.messageDetail = await res.json();
        this.selectedMessage = this.messageDetail;
        if (this.messageDetail.ai_summary_html) {
          this.aiSummary = this.messageDetail.ai_summary_html;
        }
        // Load the folder
        const folderId = this.messageDetail.folder_id;
        const accountId = this.messageDetail.account_id;
        await this.loadFolders(accountId);
        const flds = this.folders[accountId] || [];
        const folder = flds.find(f => f.uuid === folderId);
        if (folder) {
          this.selectedFolder = folder;
          await this.loadMessages();
        }
      }
    },

    // ----- Shared optimistic helpers -----

    /**
     * Adjust unread count on the relevant folder and labels for a message.
     * @param {object} msg - message object (needs account_id, folder_id, labels)
     * @param {number} delta - +1 (became unread) or -1 (became read / removed)
     */
    _adjustUnreadCount(msg, delta) {
      const accountId = msg.account_id || this.selectedFolder?.account_id || this.selectedLabel?.account_id;
      // Folder unread count
      if (this.selectedFolder) {
        this.selectedFolder.unread_count = Math.max(0, (this.selectedFolder.unread_count || 0) + delta);
      } else if (this.unifiedInbox) {
        const accFolders = this.folders[accountId] || [];
        const folder = accFolders.find(f => f.uuid === msg.folder_id);
        if (folder) folder.unread_count = Math.max(0, (folder.unread_count || 0) + delta);
      }
      // Label unread counts
      const msgLabels = msg.labels || [];
      const accountLabels = accountId ? (this.labels[accountId] || []) : [];
      for (const ml of msgLabels) {
        const lbl = accountLabels.find(l => l.uuid === ml.uuid);
        if (lbl) lbl.unread_count = Math.max(0, (lbl.unread_count || 0) + delta);
      }
    },

    /**
     * Optimistically remove messages from the current list.
     * Adjusts unread counts, clears selection/detail, updates totalMessages.
     * @param {string[]} msgUuids - UUIDs of messages to remove
     */
    _optimisticRemoveMessages(msgUuids) {
      const removed = this.messages.filter(m => msgUuids.includes(m.uuid));
      this.messages = this.messages.filter(m => !msgUuids.includes(m.uuid));
      this.selectedMessages = this.selectedMessages.filter(id => !msgUuids.includes(id));
      this.totalMessages = Math.max(0, this.totalMessages - removed.length);
      // Adjust unread counts for removed unread messages
      for (const msg of removed) {
        if (!msg.is_read) this._adjustUnreadCount(msg, -1);
      }
      // Clear detail if the viewed message was removed
      if (this.messageDetail && msgUuids.includes(this.messageDetail.uuid)) {
        this.selectedMessage = null;
        this.messageDetail = null;
        this._updateUrl(null);
      }
    },

    // ----- Flags -----
    async toggleRead(msg, forceRead) {
      this.actionInProgress = true;
      const newVal = forceRead !== undefined ? forceRead : !msg.is_read;
      // Optimistic UI update
      if (msg.is_read !== newVal) {
        const listMsg = this.messages.find(m => m.uuid === msg.uuid);
        const ref = listMsg || msg;
        ref.is_read = newVal;
        if (listMsg && msg !== listMsg) msg.is_read = newVal;
        this._adjustUnreadCount(ref, newVal ? -1 : 1);
      }
      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, {
        method: 'PATCH',
        body: { is_read: newVal },
      });
      this.actionInProgress = false;
    },

    async toggleStar(msg) {
      this.actionInProgress = true;
      // Optimistic UI update
      const newVal = !msg.is_starred;
      msg.is_starred = newVal;
      const listMsg = this.messages.find(m => m.uuid === msg.uuid);
      if (listMsg && listMsg !== msg) listMsg.is_starred = newVal;
      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, {
        method: 'PATCH',
        body: { is_starred: newVal },
      });
      this.actionInProgress = false;
    },

    async deleteMessage(msg) {
      const ok = await AppDialog.confirm({
        title: 'Delete message',
        message: 'Move this message to trash?',
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;

      this.actionInProgress = true;
      // Optimistic UI update
      this._optimisticRemoveMessages([msg.uuid]);
      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, { method: 'DELETE' });
      this.actionInProgress = false;
    },

    // ----- Batch actions -----
    toggleSelectMessage(uuid) {
      const idx = this.selectedMessages.indexOf(uuid);
      if (idx === -1) this.selectedMessages.push(uuid);
      else this.selectedMessages.splice(idx, 1);
    },

    async batchAction(action, targetFolderId) {
      if (this.selectedMessages.length === 0) return;
      this.batchInProgress = true;
      const msgUuids = [...this.selectedMessages];
      const affectedMsgs = this.messages.filter(m => msgUuids.includes(m.uuid));

      // Optimistic UI update
      if (action === 'delete' || action === 'move') {
        this._optimisticRemoveMessages(msgUuids);
      } else if (action === 'mark_read' || action === 'mark_unread') {
        const markRead = action === 'mark_read';
        for (const msg of affectedMsgs) {
          if (msg.is_read !== markRead) {
            msg.is_read = markRead;
            this._adjustUnreadCount(msg, markRead ? -1 : 1);
          }
        }
        if (this.messageDetail && msgUuids.includes(this.messageDetail.uuid)) {
          this.messageDetail.is_read = markRead;
        }
        this.selectedMessages = [];
      } else if (action === 'star' || action === 'unstar') {
        const starred = action === 'star';
        for (const msg of affectedMsgs) msg.is_starred = starred;
        if (this.messageDetail && msgUuids.includes(this.messageDetail.uuid)) {
          this.messageDetail.is_starred = starred;
        }
        this.selectedMessages = [];
      }

      const body = { message_ids: msgUuids, action };
      if (action === 'move' && targetFolderId) {
        body.target_folder_id = targetFolderId;
      }
      await this._fetch('/api/v1/mail/messages/batch-action', {
        method: 'POST',
        body,
      });
      this.batchInProgress = false;
    },

    // ----- Message context menu -----
    openMessageContextMenu(event, msg) {
      event.preventDefault();
      const menu = document.getElementById('message-context-menu');
      if (!menu) return;

      this.msgCtx.msg = msg;
      this.msgCtx.open = true;

      this.$nextTick(() => {
        const rect = menu.getBoundingClientRect();
        let x = event.clientX;
        let y = event.clientY;
        if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 10;
        if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 10;
        this.msgCtx.x = x;
        this.msgCtx.y = y;

      });
    },

    async msgCtxAction(action) {
      const msg = this.msgCtx.msg;
      if (!msg) return;
      this.msgCtx.open = false;

      switch (action) {
        case 'reply':
          this.replyTo(msg);
          break;
        case 'reply_all':
          this.replyAll(msg);
          break;
        case 'forward':
          this.forwardMessage(msg);
          break;
        case 'toggle_read':
          await this.toggleRead(msg);
          break;
        case 'toggle_star':
          await this.toggleStar(msg);
          break;
        case 'move':
          await this._showMoveDialog(
            this.selectedMessages.length > 0 && this.selectedMessages.includes(msg.uuid)
              ? [...this.selectedMessages] : [msg.uuid]
          );
          break;
        case 'delete':
          await this.deleteMessage(msg);
          break;
      }
    },

    _getMsgCtxTargetIds() {
      const msg = this.msgCtx.msg;
      if (!msg) return [];
      return this.selectedMessages.length > 0 && this.selectedMessages.includes(msg.uuid)
        ? [...this.selectedMessages] : [msg.uuid];
    },

    _getMsgCtxAccountLabels() {
      const accountId = this.selectedFolder?.account_id || this.selectedLabel?.account_id;
      return accountId ? (this.labels[accountId] || []) : [];
    },

    _msgCtxHasLabel(labelUuid) {
      const ids = this._getMsgCtxTargetIds();
      // Check if ALL targeted messages have the label
      return ids.every(id => {
        const msg = this.messages.find(m => m.uuid === id);
        return msg?.labels?.some(l => l.uuid === labelUuid);
      });
    },

    // ----- Move messages -----
    async moveMessages(msgUuids, targetFolder) {
      if (!msgUuids || !msgUuids.length || !targetFolder) return;
      this.batchInProgress = true;
      // Optimistic UI update
      this._optimisticRemoveMessages(msgUuids);
      await this._fetch('/api/v1/mail/messages/batch-action', {
        method: 'POST',
        body: {
          message_ids: msgUuids,
          action: 'move',
          target_folder_id: targetFolder.uuid,
        },
      });
      this.batchInProgress = false;
    },

    async _showMoveDialog(msgUuids) {
      if (!msgUuids || !msgUuids.length) return;
      const refMsg = this.messages.find(m => msgUuids.includes(m.uuid)) || msgUuids[0];
      const targets = this.getMoveTargetFolders(refMsg);
      if (!targets.length) return;

      const options = targets.map(f => ({
        label: '\u00A0\u00A0'.repeat(f._depth || 0) + f.display_name,
        value: f.uuid,
      }));

      const count = msgUuids.length;
      const selected = await AppDialog.select({
        title: 'Move to',
        message: count === 1 ? 'Select a destination folder.' : `Move ${count} messages to:`,
        options,
        okLabel: 'Move',
        okClass: 'btn-warning',
        icon: 'folder-input',
        iconClass: 'bg-warning/10 text-warning',
      });
      if (!selected) return;

      const targetFolder = targets.find(f => f.uuid === selected);
      if (targetFolder) {
        await this.moveMessages(msgUuids, targetFolder);
      }
    },

    // ----- Save attachment to Files -----
    async saveAttachmentToFiles(attachmentUuid) {
      const folder = await AppDialog.folderPicker({
        title: 'Save to Files',
        message: 'Choose a destination folder.',
        okLabel: 'Save',
        okClass: 'btn-warning',
        icon: 'folder-down',
        iconClass: 'bg-warning/10 text-warning',
      });
      if (!folder) return;

      const body = {};
      if (folder.uuid) body.folder_id = folder.uuid;

      try {
        const res = await this._fetch(`/api/v1/mail/attachments/${attachmentUuid}/save-to-files`, {
          method: 'POST',
          body,
        });
        if (res.ok) {
          AppDialog.message({ title: 'Saved', message: 'Attachment saved to Files.', icon: 'check-circle', iconClass: 'bg-success/10 text-success' });
        } else {
          const err = await res.json().catch(() => ({}));
          AppDialog.error({ message: err.detail || 'Failed to save attachment.' });
        }
      } catch (e) {
        AppDialog.error({ message: 'Failed to save attachment.' });
      }
    },

    // ----- Message navigation -----
    _navigateMessages(direction) {
      if (!this.messages.length) return;
      const currentIdx = this.messages.findIndex(m => m.uuid === this.selectedMessage?.uuid);
      let nextIdx = currentIdx + direction;
      if (nextIdx < 0) nextIdx = 0;
      if (nextIdx >= this.messages.length) nextIdx = this.messages.length - 1;
      this.selectMessage(this.messages[nextIdx]);
    },
  };
};
