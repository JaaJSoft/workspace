/**
 * Mail application — Alpine.js component
 */
function mailApp() {
  return {
    // State
    accounts: [],
    folders: {},        // { accountUuid: [folder, ...] }
    messages: [],
    selectedFolder: null,
    selectedMessage: null,
    messageDetail: null,
    selectedMessages: [],
    expandedAccounts: {},

    // UI
    collapsed: false,
    loadingMessages: false,
    loadingMoreMessages: false,
    loadingDetail: false,
    syncingAccounts: {},  // { accountUuid: true }
    hasMoreMessages: false,
    currentPage: 1,
    totalMessages: 0,

    // Add / Edit account
    newAccount: _defaultNewAccount(),
    accountError: '',
    addingAccount: false,
    editAccount: null,
    editAccountError: '',
    savingAccount: false,

    // Filters
    filters: {
      search: '',
      unread: false,
      starred: false,
      attachments: false,
    },
    _searchTimer: null,

    // Compose
    compose: _defaultCompose(),
    showCcBcc: false,

    init() {
      // Load accounts from embedded data
      try {
        const el = document.getElementById('accounts-data');
        if (el) this.accounts = JSON.parse(el.textContent);
      } catch (e) {}

      // Auto-expand all accounts and load folders
      for (const acc of this.accounts) {
        this.expandedAccounts[acc.uuid] = true;
        this.syncingAccounts[acc.uuid] = false;
        this.loadFolders(acc.uuid);
      }

      // Check URL params for deep linking
      const params = new URLSearchParams(window.location.search);
      const msgId = params.get('message');
      if (msgId) {
        this._openMessageById(msgId);
      }
    },

    // ----- CSRF -----
    _csrf() {
      return document.cookie.split('; ')
        .find(c => c.startsWith('csrftoken='))?.split('=')[1] || '';
    },

    async _fetch(url, opts = {}) {
      opts.headers = {
        ...opts.headers,
        'X-CSRFToken': this._csrf(),
      };
      if (opts.body && !(opts.body instanceof FormData)) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.body);
      }
      opts.credentials = 'same-origin';
      const res = await fetch(url, opts);
      return res;
    },

    // ----- Folders -----
    async loadFolders(accountUuid) {
      const res = await this._fetch(`/api/v1/mail/folders?account=${accountUuid}`);
      if (res.ok) {
        const data = await res.json();
        this.folders[accountUuid] = data;
      }
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
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

    // ----- Messages -----
    async selectFolder(folder) {
      this.selectedFolder = folder;
      this.selectedMessage = null;
      this.messageDetail = null;
      this._updateUrl(null);
      this.selectedMessages = [];
      this.currentPage = 1;
      this._resetFilters();
      await this.loadMessages();
    },

    _buildMessagesUrl() {
      let url = `/api/v1/mail/messages?folder=${this.selectedFolder.uuid}&page=${this.currentPage}`;
      if (this.filters.search) url += `&search=${encodeURIComponent(this.filters.search)}`;
      if (this.filters.unread) url += '&unread=1';
      if (this.filters.starred) url += '&starred=1';
      if (this.filters.attachments) url += '&attachments=1';
      return url;
    },

    async loadMessages() {
      if (!this.selectedFolder) return;
      this.loadingMessages = true;
      const res = await this._fetch(this._buildMessagesUrl());
      if (res.ok) {
        const data = await res.json();
        this.messages = data.results;
        this.totalMessages = data.count;
        this.hasMoreMessages = data.count > this.currentPage * data.page_size;
      }
      this.loadingMessages = false;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
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
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    async refreshFolder() {
      if (!this.selectedFolder) return;
      const accountUuid = this.selectedFolder.account_id;
      await this.syncAccount(accountUuid);
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
      this.selectedMessage = msg;
      this.loadingDetail = true;
      this._updateUrl(msg.uuid);
      const res = await this._fetch(`/api/v1/mail/messages/${msg.uuid}`);
      if (res.ok) {
        this.messageDetail = await res.json();
        // Auto-mark as read
        if (!msg.is_read) {
          this.toggleRead(this.messageDetail, true);
          msg.is_read = true;
        }
      }
      this.loadingDetail = false;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    async _openMessageById(uuid) {
      const res = await this._fetch(`/api/v1/mail/messages/${uuid}`);
      if (res.ok) {
        this.messageDetail = await res.json();
        this.selectedMessage = this.messageDetail;
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

    // ----- Flags -----
    async toggleRead(msg, forceRead) {
      const newVal = forceRead !== undefined ? forceRead : !msg.is_read;
      const wasRead = msg.is_read;
      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, {
        method: 'PATCH',
        body: { is_read: newVal },
      });
      msg.is_read = newVal;
      // Update in list too
      const listMsg = this.messages.find(m => m.uuid === msg.uuid);
      if (listMsg) listMsg.is_read = newVal;
      // Update folder unread count
      if (this.selectedFolder && wasRead !== newVal) {
        this.selectedFolder.unread_count += newVal ? -1 : 1;
      }
    },

    async toggleStar(msg) {
      const newVal = !msg.is_starred;
      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, {
        method: 'PATCH',
        body: { is_starred: newVal },
      });
      msg.is_starred = newVal;
      const listMsg = this.messages.find(m => m.uuid === msg.uuid);
      if (listMsg) listMsg.is_starred = newVal;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
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

      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, { method: 'DELETE' });
      this.messages = this.messages.filter(m => m.uuid !== msg.uuid);
      if (this.selectedMessage?.uuid === msg.uuid) {
        this.selectedMessage = null;
        this.messageDetail = null;
        this._updateUrl(null);
      }
      // Update folder counts
      if (this.selectedFolder) {
        this.selectedFolder.message_count--;
        if (!msg.is_read) this.selectedFolder.unread_count--;
      }
    },

    // ----- Batch actions -----
    toggleSelectMessage(uuid) {
      const idx = this.selectedMessages.indexOf(uuid);
      if (idx === -1) this.selectedMessages.push(uuid);
      else this.selectedMessages.splice(idx, 1);
    },

    async batchAction(action) {
      if (this.selectedMessages.length === 0) return;
      await this._fetch('/api/v1/mail/messages/batch-action', {
        method: 'POST',
        body: { message_ids: this.selectedMessages, action },
      });
      // Refresh messages and folder counts
      this.selectedMessages = [];
      await this.loadMessages();
      if (this.selectedFolder) {
        await this.loadFolders(this.selectedFolder.account_id);
        // Re-select folder to refresh counts in sidebar
        const flds = this.folders[this.selectedFolder.account_id] || [];
        const updated = flds.find(f => f.uuid === this.selectedFolder.uuid);
        if (updated) this.selectedFolder = updated;
      }
      if (this.messageDetail && action === 'delete') {
        this.selectedMessage = null;
        this.messageDetail = null;
        this._updateUrl(null);
      }
    },

    // ----- Compose -----
    async showCompose(defaults = {}) {
      this.compose = { ..._defaultCompose(), ...defaults };
      if (this.accounts.length > 0 && !this.compose.account_id) {
        this.compose.account_id = this.accounts[0].uuid;
      }
      this.showCcBcc = false;

      // If no defaults (fresh compose), check localStorage for a saved draft
      if (!defaults.to && !defaults.subject && !defaults.body) {
        const saved = this._getLocalStorageDraft();
        if (saved && (saved.to || saved.subject || saved.body)) {
          const restore = await AppDialog.confirm({
            title: 'Restore draft',
            message: 'You have an unsaved draft. Would you like to restore it?',
            okLabel: 'Restore',
            cancelLabel: 'Discard',
            icon: 'file-edit',
            iconClass: 'bg-info/10 text-info',
          });
          if (restore) {
            this.compose.to = saved.to || '';
            this.compose.cc = saved.cc || '';
            this.compose.bcc = saved.bcc || '';
            this.compose.subject = saved.subject || '';
            this.compose.body = saved.body || '';
            this.compose.draft_id = saved.draft_id || null;
            this.compose.is_reply = saved.is_reply || false;
            if (saved.account_id) this.compose.account_id = saved.account_id;
            if (saved.cc || saved.bcc) this.showCcBcc = true;
          } else {
            this._clearLocalStorageDraft();
          }
        }
      }

      document.getElementById('mail-compose-dialog').showModal();
    },

    async closeCompose() {
      if (this.compose._saveTimer) clearTimeout(this.compose._saveTimer);
      if (this._hasComposeContent()) {
        await this._saveDraft();
      } else {
        this._clearLocalStorageDraft();
      }
      document.getElementById('mail-compose-dialog').close();
      this.compose = _defaultCompose();
    },

    replyTo(msg) {
      const from = msg.from_address?.email || '';
      const subject = msg.subject?.startsWith('Re:') ? msg.subject : `Re: ${msg.subject || ''}`;
      this.showCompose({
        to: from,
        subject,
        body: `\n\n---\nOn ${this.formatFullDate(msg.date)}, ${msg.from_address?.name || from} wrote:\n> ${(msg.body_text || msg.snippet || '').replace(/\n/g, '\n> ')}`,
        account_id: msg.account_id,
        is_reply: true,
      });
    },

    forwardMessage(msg) {
      const subject = msg.subject?.startsWith('Fwd:') ? msg.subject : `Fwd: ${msg.subject || ''}`;
      this.showCompose({
        subject,
        body: `\n\n---\nForwarded message from ${msg.from_address?.name || msg.from_address?.email || 'Unknown'}:\n\n${msg.body_text || msg.snippet || ''}`,
        account_id: msg.account_id,
        is_reply: true,
      });
    },

    handleComposeFiles(event) {
      this.compose.attachments = [...this.compose.attachments, ...event.target.files];
    },

    async sendEmail() {
      this.compose.sending = true;
      this.compose.error = '';

      // Parse comma-separated addresses
      const toList = this.compose.to.split(',').map(s => s.trim()).filter(Boolean);
      const ccList = this.compose.cc ? this.compose.cc.split(',').map(s => s.trim()).filter(Boolean) : [];
      const bccList = this.compose.bcc ? this.compose.bcc.split(',').map(s => s.trim()).filter(Boolean) : [];

      const formData = new FormData();
      formData.append('account_id', this.compose.account_id);
      formData.append('subject', this.compose.subject);
      formData.append('body_text', this.compose.body);
      // Generate simple HTML from text
      const htmlBody = this.compose.body.replace(/\n/g, '<br>');
      formData.append('body_html', htmlBody);
      for (const addr of toList) formData.append('to', addr);
      for (const addr of ccList) formData.append('cc', addr);
      for (const addr of bccList) formData.append('bcc', addr);
      for (const file of this.compose.attachments) formData.append('attachments', file);

      const res = await fetch('/api/v1/mail/messages/send', {
        method: 'POST',
        headers: { 'X-CSRFToken': this._csrf() },
        credentials: 'same-origin',
        body: formData,
      });

      if (res.ok) {
        if (this.compose._saveTimer) clearTimeout(this.compose._saveTimer);
        const draftId = this.compose.draft_id;
        this._clearLocalStorageDraft();
        document.getElementById('mail-compose-dialog').close();
        this.compose = _defaultCompose();
        // Delete the draft after sending
        if (draftId) this._deleteDraft(draftId);
      } else {
        const data = await res.json().catch(() => ({}));
        this.compose.error = data.error || 'Failed to send email';
        this.compose.sending = false;
      }
    },

    // ----- Drafts -----
    _hasComposeContent() {
      return !!(this.compose.to || this.compose.subject || this.compose.body);
    },

    _scheduleDraftSave() {
      if (this.compose._saveTimer) clearTimeout(this.compose._saveTimer);
      if (!this._hasComposeContent()) return;
      this.compose._saveTimer = setTimeout(() => this._saveDraft(), 30000);
    },

    async _saveDraft() {
      if (this.compose.saving || this.compose.sending) return;
      if (!this._hasComposeContent()) return;

      this.compose.saving = true;

      const toList = this.compose.to ? this.compose.to.split(',').map(s => s.trim()).filter(Boolean) : [];
      const ccList = this.compose.cc ? this.compose.cc.split(',').map(s => s.trim()).filter(Boolean) : [];
      const bccList = this.compose.bcc ? this.compose.bcc.split(',').map(s => s.trim()).filter(Boolean) : [];
      const htmlBody = this.compose.body.replace(/\n/g, '<br>');

      const payload = {
        account_id: this.compose.account_id,
        to: toList,
        cc: ccList,
        bcc: bccList,
        subject: this.compose.subject,
        body_text: this.compose.body,
        body_html: htmlBody,
      };
      if (this.compose.draft_id) payload.draft_id = this.compose.draft_id;

      try {
        const res = await this._fetch('/api/v1/mail/drafts', {
          method: 'POST',
          body: payload,
        });

        if (res.ok) {
          const data = await res.json();
          this.compose.draft_id = data.uuid;
          this.compose.last_saved = Date.now();
          // Refresh drafts folder counts
          this._refreshDraftsFolderCounts();
        }
      } catch (e) {
        // Silent fail — save to localStorage as fallback
      }

      // Always save to localStorage as fallback
      this._saveComposeToLocalStorage();
      this.compose.saving = false;
    },

    _saveComposeToLocalStorage() {
      try {
        const data = {
          account_id: this.compose.account_id,
          to: this.compose.to,
          cc: this.compose.cc,
          bcc: this.compose.bcc,
          subject: this.compose.subject,
          body: this.compose.body,
          draft_id: this.compose.draft_id,
          is_reply: this.compose.is_reply,
          saved_at: Date.now(),
        };
        localStorage.setItem('mail_compose_draft', JSON.stringify(data));
      } catch (e) {}
    },

    _clearLocalStorageDraft() {
      try { localStorage.removeItem('mail_compose_draft'); } catch (e) {}
    },

    _getLocalStorageDraft() {
      try {
        const raw = localStorage.getItem('mail_compose_draft');
        if (!raw) return null;
        const data = JSON.parse(raw);
        // Expire after 24h
        if (Date.now() - data.saved_at > 86400000) {
          this._clearLocalStorageDraft();
          return null;
        }
        return data;
      } catch (e) { return null; }
    },

    async _refreshDraftsFolderCounts() {
      for (const acc of this.accounts) {
        const flds = this.folders[acc.uuid] || [];
        const draftsFolder = flds.find(f => f.folder_type === 'drafts');
        if (draftsFolder) {
          await this.loadFolders(acc.uuid);
          if (this.selectedFolder?.uuid === draftsFolder.uuid) {
            await this.loadMessages();
          }
          break;
        }
      }
    },

    async _deleteDraft(draftId) {
      try {
        await this._fetch(`/api/v1/mail/drafts/${draftId}`, { method: 'DELETE' });
        this._refreshDraftsFolderCounts();
      } catch (e) {}
    },

    // ----- Accounts -----
    showAddAccount() {
      this.newAccount = _defaultNewAccount();
      this.accountError = '';
      document.getElementById('mail-add-account-dialog').showModal();
    },

    closeAddAccount() {
      document.getElementById('mail-add-account-dialog').close();
    },

    async addAccount() {
      this.addingAccount = true;
      this.accountError = '';

      const res = await this._fetch('/api/v1/mail/accounts', {
        method: 'POST',
        body: this.newAccount,
      });

      if (res.ok) {
        const account = await res.json();
        this.accounts.push(account);
        this.expandedAccounts[account.uuid] = true;
        await this.loadFolders(account.uuid);
        this.closeAddAccount();

        // Trigger initial sync
        this.syncAccount(account.uuid);
      } else {
        const data = await res.json().catch(() => ({}));
        this.accountError = data.detail || JSON.stringify(data) || 'Failed to add account';
      }
      this.addingAccount = false;
    },

    async syncAccount(uuid) {
      this.syncingAccounts[uuid] = true;
      try {
        await this._fetch(`/api/v1/mail/accounts/${uuid}/sync`, { method: 'POST' });
        await this.loadFolders(uuid);
        if (this.selectedFolder?.account_id === uuid) {
          await this.loadMessages();
        }
      } finally {
        this.syncingAccounts[uuid] = false;
      }
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    async testAccount(uuid) {
      const res = await this._fetch(`/api/v1/mail/accounts/${uuid}/test`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        const imapStatus = data.imap.success ? 'OK' : `Failed: ${data.imap.error}`;
        const smtpStatus = data.smtp.success ? 'OK' : `Failed: ${data.smtp.error}`;
        await AppDialog.message({
          title: 'Connection Test',
          message: `IMAP: ${imapStatus}\nSMTP: ${smtpStatus}`,
          icon: data.imap.success && data.smtp.success ? 'check-circle' : 'alert-triangle',
          iconClass: data.imap.success && data.smtp.success ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning',
        });
      }
    },

    async removeAccount(uuid) {
      const ok = await AppDialog.confirm({
        title: 'Remove Account',
        message: 'This will delete the account and all synced messages. Continue?',
        okLabel: 'Remove',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;

      await this._fetch(`/api/v1/mail/accounts/${uuid}`, { method: 'DELETE' });
      this.accounts = this.accounts.filter(a => a.uuid !== uuid);
      delete this.folders[uuid];
      if (this.selectedFolder?.account_id === uuid) {
        this.selectedFolder = null;
        this.messages = [];
        this.selectedMessage = null;
        this.messageDetail = null;
        this._updateUrl(null);
      }
    },

    showEditAccount(account) {
      this.editAccount = {
        uuid: account.uuid,
        email: account.email,
        display_name: account.display_name || '',
        imap_host: account.imap_host,
        imap_port: account.imap_port,
        imap_use_ssl: account.imap_use_ssl,
        smtp_host: account.smtp_host,
        smtp_port: account.smtp_port,
        smtp_use_tls: account.smtp_use_tls,
        username: account.username,
        password: '',
      };
      this.editAccountError = '';
      document.getElementById('mail-edit-account-dialog').showModal();
    },

    closeEditAccount() {
      document.getElementById('mail-edit-account-dialog').close();
      this.editAccount = null;
    },

    async saveAccount() {
      this.savingAccount = true;
      this.editAccountError = '';

      const payload = { ...this.editAccount };
      const uuid = payload.uuid;
      delete payload.uuid;
      delete payload.email;
      if (!payload.password) delete payload.password;

      const res = await this._fetch(`/api/v1/mail/accounts/${uuid}`, {
        method: 'PATCH',
        body: payload,
      });

      if (res.ok) {
        const updated = await res.json();
        const idx = this.accounts.findIndex(a => a.uuid === uuid);
        if (idx !== -1) this.accounts[idx] = updated;
        this.closeEditAccount();
      } else {
        const data = await res.json().catch(() => ({}));
        this.editAccountError = data.detail || JSON.stringify(data) || 'Failed to save account';
      }
      this.savingAccount = false;
    },

    // ----- Keyboard -----
    handleKeydown(e) {
      // Don't handle if in an input/textarea
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      // Don't handle if a dialog is open
      if (document.querySelector('dialog[open]')) return;

      switch (e.key) {
        case 'j': this._navigateMessages(1); break;
        case 'k': this._navigateMessages(-1); break;
        case 'c': e.preventDefault(); this.showCompose(); break;
        case 'r':
          if (this.messageDetail) { e.preventDefault(); this.replyTo(this.messageDetail); }
          break;
        case 'f':
          if (this.messageDetail) { e.preventDefault(); this.forwardMessage(this.messageDetail); }
          break;
        case 'Escape':
          if (this.selectedMessage) {
            this.selectedMessage = null;
            this.messageDetail = null;
            this._updateUrl(null);
          }
          break;
      }
    },

    _navigateMessages(direction) {
      if (!this.messages.length) return;
      const currentIdx = this.messages.findIndex(m => m.uuid === this.selectedMessage?.uuid);
      let nextIdx = currentIdx + direction;
      if (nextIdx < 0) nextIdx = 0;
      if (nextIdx >= this.messages.length) nextIdx = this.messages.length - 1;
      this.selectMessage(this.messages[nextIdx]);
    },

    // ----- URL -----
    _updateUrl(messageUuid) {
      const url = new URL(window.location);
      if (messageUuid) {
        url.searchParams.set('message', messageUuid);
      } else {
        url.searchParams.delete('message');
      }
      history.replaceState(null, '', url);
    },

    // ----- UI helpers -----
    highlightSearch(text) {
      if (!text) return '';
      const escaped = String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const q = this.filters.search?.trim();
      if (!q) return escaped;
      const re = new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
      return escaped.replace(re, '<mark class="bg-warning/40 text-inherit rounded-sm px-0.5">$1</mark>');
    },

    toggleCollapse() {
      this.collapsed = !this.collapsed;
    },

    isMobile() {
      return window.innerWidth < 1024;
    },

    formatDate(dateStr) {
      if (!dateStr) return '';
      const d = new Date(dateStr);
      const now = new Date();
      const diff = now - d;
      if (diff < 86400000 && d.getDate() === now.getDate()) {
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
      if (diff < 604800000) {
        return d.toLocaleDateString([], { weekday: 'short' });
      }
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    },

    formatFullDate(dateStr) {
      if (!dateStr) return '';
      return new Date(dateStr).toLocaleString([], {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    },

    formatSize(bytes) {
      if (!bytes) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB'];
      let i = 0;
      while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
      return `${bytes.toFixed(i ? 1 : 0)} ${units[i]}`;
    },
  };
}

function _defaultNewAccount() {
  return {
    email: '', display_name: '',
    imap_host: '', imap_port: 993, imap_use_ssl: true,
    smtp_host: '', smtp_port: 587, smtp_use_tls: true,
    username: '', password: '',
  };
}

function _defaultCompose() {
  return {
    account_id: '', to: '', cc: '', bcc: '',
    subject: '', body: '', is_reply: false,
    attachments: [], sending: false, error: '',
    draft_id: null, saving: false, last_saved: null,
    _saveTimer: null,
  };
}
