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
    hasMoreMessages: false,
    currentPage: 1,
    totalMessages: 0,

    // Add account
    newAccount: _defaultNewAccount(),
    accountError: '',
    addingAccount: false,

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
      this.selectedMessages = [];
      this.currentPage = 1;
      await this.loadMessages();
    },

    async loadMessages() {
      if (!this.selectedFolder) return;
      this.loadingMessages = true;
      const res = await this._fetch(
        `/api/v1/mail/messages?folder=${this.selectedFolder.uuid}&page=${this.currentPage}`
      );
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
      const res = await this._fetch(
        `/api/v1/mail/messages?folder=${this.selectedFolder.uuid}&page=${this.currentPage}`
      );
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
      // Sync account first
      const accountUuid = this.selectedFolder.account_id;
      await this._fetch(`/api/v1/mail/accounts/${accountUuid}/sync`, { method: 'POST' });
      await this.loadFolders(accountUuid);
      await this.loadMessages();
    },

    // ----- Message detail -----
    async selectMessage(msg) {
      this.selectedMessage = msg;
      this.loadingDetail = true;
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
      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, {
        method: 'PATCH',
        body: { is_read: newVal },
      });
      msg.is_read = newVal;
      // Update in list too
      const listMsg = this.messages.find(m => m.uuid === msg.uuid);
      if (listMsg) listMsg.is_read = newVal;
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
      // Refresh
      this.selectedMessages = [];
      await this.loadMessages();
      if (this.messageDetail && action === 'delete') {
        this.selectedMessage = null;
        this.messageDetail = null;
      }
    },

    // ----- Compose -----
    showCompose(defaults = {}) {
      this.compose = { ..._defaultCompose(), ...defaults };
      if (this.accounts.length > 0 && !this.compose.account_id) {
        this.compose.account_id = this.accounts[0].uuid;
      }
      this.showCcBcc = false;
      document.getElementById('mail-compose-dialog').showModal();
    },

    closeCompose() {
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
        this.closeCompose();
      } else {
        const data = await res.json().catch(() => ({}));
        this.compose.error = data.error || 'Failed to send email';
      }
      this.compose.sending = false;
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
      await this._fetch(`/api/v1/mail/accounts/${uuid}/sync`, { method: 'POST' });
      await this.loadFolders(uuid);
      if (this.selectedFolder?.account_id === uuid) {
        await this.loadMessages();
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
      }
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

    // ----- UI helpers -----
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
  };
}
