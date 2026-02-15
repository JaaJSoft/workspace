/* ── Mail contact card popover ─────────────────────────────── */

/**
 * Build a mail contact card DOM node (no innerHTML — all safe DOM methods).
 * @param {string} name - Contact display name (may be empty)
 * @param {string} email - Contact email address
 * @returns {HTMLElement}
 */
function _cleanName(raw) {
  if (!raw || typeof raw !== 'string') return '';
  return raw.replace(/^[^a-zA-Z\u00C0-\u024F]+|[^a-zA-Z\u00C0-\u024F]+$/g, '').trim();
}

function _initial(str) {
  if (!str) return '?';
  var m = str.match(/[a-zA-Z\u00C0-\u024F]/);
  return m ? m[0].toUpperCase() : str[0].toUpperCase();
}

function _buildMailCard(name, email) {
  name = _cleanName(name);
  email = (email && typeof email === 'string') ? email.trim() : '';

  var root = document.createElement('div');
  root.className = 'p-3 w-64';

  // Avatar + info row
  var row = document.createElement('div');
  row.className = 'flex items-center gap-3 mb-2';

  var avatarWrap = document.createElement('div');
  avatarWrap.className = 'avatar placeholder';
  var avatarInner = document.createElement('div');
  avatarInner.className = 'w-10 h-10 bg-warning/15 text-warning rounded-full flex items-center justify-center font-semibold';
  avatarInner.textContent = _initial(name || email);
  avatarWrap.appendChild(avatarInner);
  row.appendChild(avatarWrap);

  var info = document.createElement('div');
  info.className = 'min-w-0 flex-1';
  var nameEl = document.createElement('div');
  nameEl.className = 'font-semibold text-sm truncate';
  nameEl.textContent = name || email;
  info.appendChild(nameEl);
  if (name) {
    var emailEl = document.createElement('div');
    emailEl.className = 'text-xs text-base-content/50 truncate';
    emailEl.textContent = email;
    info.appendChild(emailEl);
  }
  row.appendChild(info);
  root.appendChild(row);

  // Action buttons
  var actions = document.createElement('div');
  actions.className = 'flex gap-1';

  var copyBtn = document.createElement('button');
  copyBtn.className = 'btn btn-ghost btn-xs flex-1 gap-1';
  var copyIcon = document.createElement('i');
  copyIcon.setAttribute('data-lucide', 'copy');
  copyIcon.className = 'w-3 h-3';
  copyBtn.appendChild(copyIcon);
  copyBtn.appendChild(document.createTextNode(' Copy email'));
  copyBtn.addEventListener('click', function() {
    navigator.clipboard.writeText(email);
    copyBtn.textContent = 'Copied!';
    setTimeout(function() {
      copyBtn.textContent = '';
      copyBtn.appendChild(copyIcon);
      copyBtn.appendChild(document.createTextNode(' Copy email'));
      lucide?.createIcons({ nodes: [copyIcon] });
    }, 1500);
  });
  actions.appendChild(copyBtn);

  var sendBtn = document.createElement('button');
  sendBtn.className = 'btn btn-ghost btn-xs flex-1 gap-1';
  var sendIcon = document.createElement('i');
  sendIcon.setAttribute('data-lucide', 'send');
  sendIcon.className = 'w-3 h-3';
  sendBtn.appendChild(sendIcon);
  sendBtn.appendChild(document.createTextNode(' Send email'));
  sendBtn.addEventListener('click', function() {
    // If already on the mail page, dispatch a custom event to open compose
    var composeDialog = document.getElementById('mail-compose-dialog');
    if (composeDialog) {
      document.dispatchEvent(new CustomEvent('mail:compose', { detail: { to: email } }));
    } else {
      // Navigate to mail with compose query param
      window.location.href = '/mail?compose=' + encodeURIComponent(email);
    }
  });
  actions.appendChild(sendBtn);

  root.appendChild(actions);
  return root;
}

/**
 * @param {HTMLElement} wrapper
 * @param {string|object} nameOrAddr - display name string, or {name, email} object
 * @param {string} [email] - email string (if first arg is a name string)
 */
window._mailCardShow = function(wrapper, nameOrAddr, email) {
  // Support both _mailCardShow(el, {name, email}) and _mailCardShow(el, name, email)
  var name;
  if (nameOrAddr && typeof nameOrAddr === 'object') {
    name = nameOrAddr.name;
    email = nameOrAddr.email;
  } else {
    name = nameOrAddr;
  }
  window._mailCardCancelHide(wrapper);
  var existing = wrapper._mailCardPopover;
  if (existing && existing.style.display !== 'none' && existing.style.opacity === '1') return;
  if (wrapper._showTimeout) clearTimeout(wrapper._showTimeout);

  wrapper._showTimeout = setTimeout(function() {
    wrapper._showTimeout = null;
    var popover = wrapper._mailCardPopover;
    if (!popover) {
      popover = document.createElement('div');
      popover.className = 'fixed z-[9999] bg-base-100 rounded-xl shadow-lg ring-1 ring-base-300';
      popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
      popover.style.opacity = '0';
      popover.addEventListener('mouseenter', function() { window._mailCardCancelHide(wrapper); });
      popover.addEventListener('mouseleave', function() { window._mailCardScheduleHide(wrapper); });
      document.body.appendChild(popover);
      wrapper._mailCardPopover = popover;
    }
    popover.textContent = '';
    popover.appendChild(_buildMailCard(name, email));

    var pos = _computePopoverPosition(wrapper);
    popover.style.left = pos.left + 'px';
    popover.style.top = pos.top + 'px';
    wrapper._placement = pos.placement;

    popover.style.display = '';
    popover.style.transition = 'none';
    _applyPopoverTransform(popover, pos.placement, false);
    void popover.offsetHeight;
    popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
    _applyPopoverTransform(popover, pos.placement, true);
    lucide?.createIcons({ nodes: popover.querySelectorAll('[data-lucide]') });
  }, 500);
};

window._mailCardScheduleHide = function(wrapper) {
  if (wrapper._showTimeout) { clearTimeout(wrapper._showTimeout); wrapper._showTimeout = null; }
  wrapper._hideTimeout = setTimeout(function() {
    var popover = wrapper._mailCardPopover;
    if (popover) {
      _applyPopoverTransform(popover, wrapper._placement || 'bottom', false);
      wrapper._closeTimeout = setTimeout(function() { popover.style.display = 'none'; }, 150);
    }
  }, 200);
};

window._mailCardCancelHide = function(wrapper) {
  if (wrapper._hideTimeout) { clearTimeout(wrapper._hideTimeout); wrapper._hideTimeout = null; }
  if (wrapper._closeTimeout) { clearTimeout(wrapper._closeTimeout); wrapper._closeTimeout = null; }
  var popover = wrapper._mailCardPopover;
  if (popover && popover.style.display !== 'none') {
    _applyPopoverTransform(popover, wrapper._placement || 'bottom', true);
  }
};

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
    actionInProgress: false,
    batchInProgress: false,
    hasMoreMessages: false,
    currentPage: 1,
    totalMessages: 0,

    // Add / Edit account
    newAccount: _defaultNewAccount(),
    accountError: '',
    addingAccount: false,
    autoDiscovering: false,
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

    // Folder context menu
    folderCtx: { open: false, x: 0, y: 0, folder: null },

    // Message context menu
    msgCtx: { open: false, x: 0, y: 0, msg: null },

    // Drag & drop
    dragOverFolder: null,
    _draggingMsgIds: null,

    // Folder tree
    expandedFolders: {},

    // Folder icon edit
    folderIconEdit: { uuid: null, name: '', icon: null, color: null },

    // Compose
    compose: _defaultCompose(),
    showCcBcc: false,

    // Autocomplete
    _autocomplete: { results: [], highlight: -1, show: false, loading: false, field: null, _timer: null },

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

      // ?compose=email@example.com — open compose with pre-filled "to"
      const composeTo = params.get('compose');
      if (composeTo) {
        this.$nextTick(() => this.showCompose({ to: composeTo }));
        // Clean the URL
        const url = new URL(window.location);
        url.searchParams.delete('compose');
        history.replaceState(null, '', url);
      }

      // Listen for mail:compose events from contact card popovers
      document.addEventListener('mail:compose', (e) => {
        this.showCompose(e.detail || {});
      });
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
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
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
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    getSubtreeUnreadCount(node) {
      let count = node.folder.unread_count || 0;
      for (const child of node.children) {
        count += this.getSubtreeUnreadCount(child);
      }
      return count;
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
      this.actionInProgress = true;
      const newVal = forceRead !== undefined ? forceRead : !msg.is_read;
      const wasRead = msg.is_read;
      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, {
        method: 'PATCH',
        body: { is_read: newVal },
      });
      msg.is_read = newVal;
      const listMsg = this.messages.find(m => m.uuid === msg.uuid);
      if (listMsg) listMsg.is_read = newVal;
      if (this.selectedFolder && wasRead !== newVal) {
        this.selectedFolder.unread_count += newVal ? -1 : 1;
      }
      this.actionInProgress = false;
    },

    async toggleStar(msg) {
      this.actionInProgress = true;
      const newVal = !msg.is_starred;
      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, {
        method: 'PATCH',
        body: { is_starred: newVal },
      });
      msg.is_starred = newVal;
      const listMsg = this.messages.find(m => m.uuid === msg.uuid);
      if (listMsg) listMsg.is_starred = newVal;
      this.actionInProgress = false;
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

      this.actionInProgress = true;
      await this._fetch(`/api/v1/mail/messages/${msg.uuid}`, { method: 'DELETE' });
      this.messages = this.messages.filter(m => m.uuid !== msg.uuid);
      if (this.selectedMessage?.uuid === msg.uuid) {
        this.selectedMessage = null;
        this.messageDetail = null;
        this._updateUrl(null);
      }
      if (this.selectedFolder) {
        this.selectedFolder.message_count--;
        if (!msg.is_read) this.selectedFolder.unread_count--;
      }
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
      const body = { message_ids: this.selectedMessages, action };
      if (action === 'move' && targetFolderId) {
        body.target_folder_id = targetFolderId;
      }
      await this._fetch('/api/v1/mail/messages/batch-action', {
        method: 'POST',
        body,
      });
      this.selectedMessages = [];
      await this.loadMessages();
      if (this.selectedFolder) {
        await this.loadFolders(this.selectedFolder.account_id);
        const flds = this.folders[this.selectedFolder.account_id] || [];
        const updated = flds.find(f => f.uuid === this.selectedFolder.uuid);
        if (updated) this.selectedFolder = updated;
      }
      if (this.messageDetail && (action === 'delete' || action === 'move')) {
        this.selectedMessage = null;
        this.messageDetail = null;
        this._updateUrl(null);
      }
      this.batchInProgress = false;
    },

    // ----- Compose -----
    async showCompose(defaults = {}) {
      this.compose = { ..._defaultCompose(), ...defaults };
      // Normalize to/cc/bcc to arrays
      this.compose.to = _parseEmails(this.compose.to);
      this.compose.cc = _parseEmails(this.compose.cc);
      this.compose.bcc = _parseEmails(this.compose.bcc);
      if (this.accounts.length > 0 && !this.compose.account_id) {
        this.compose.account_id = this.accounts[0].uuid;
      }
      this.showCcBcc = !!(this.compose.cc.length || this.compose.bcc.length);

      // If no defaults (fresh compose), check localStorage for a saved draft
      if ((!defaults.to || (Array.isArray(defaults.to) && !defaults.to.length)) && !defaults.subject && !defaults.body) {
        const saved = this._getLocalStorageDraft();
        if (saved && ((saved.to && saved.to.length) || saved.subject || saved.body)) {
          const restore = await AppDialog.confirm({
            title: 'Restore draft',
            message: 'You have an unsaved draft. Would you like to restore it?',
            okLabel: 'Restore',
            cancelLabel: 'Discard',
            icon: 'file-edit',
            iconClass: 'bg-info/10 text-info',
          });
          if (restore) {
            this.compose.to = _parseEmails(saved.to);
            this.compose.cc = _parseEmails(saved.cc);
            this.compose.bcc = _parseEmails(saved.bcc);
            this.compose.subject = saved.subject || '';
            this.compose.body = saved.body || '';
            this.compose.draft_id = saved.draft_id || null;
            this.compose.is_reply = saved.is_reply || false;
            if (saved.account_id) this.compose.account_id = saved.account_id;
            if (saved.cc?.length || saved.bcc?.length) this.showCcBcc = true;
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

    replyAll(msg) {
      const from = msg.from_address?.email || '';
      const subject = msg.subject?.startsWith('Re:') ? msg.subject : `Re: ${msg.subject || ''}`;
      // Collect all "to" addresses except our own account
      const account = this.accounts.find(a => a.uuid === msg.account_id);
      const myEmail = account?.email?.toLowerCase() || '';
      const toAddrs = [from, ...(msg.to_addresses || []).map(a => a.email)]
        .filter(e => e && e.toLowerCase() !== myEmail);
      const ccAddrs = (msg.cc_addresses || []).map(a => a.email)
        .filter(e => e && e.toLowerCase() !== myEmail);
      this.showCompose({
        to: [...new Set(toAddrs)],
        cc: [...new Set(ccAddrs)],
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

    // ----- Tag input helpers -----
    _tagInput: { to: '', cc: '', bcc: '' },

    addTag(field, value) {
      const v = (value || '').trim();
      if (!v) return;
      if (!this.compose[field].includes(v)) {
        this.compose[field].push(v);
      }
      this._tagInput[field] = '';
      this._acClose();
    },

    removeTag(field, index) {
      this.compose[field].splice(index, 1);
    },

    handleTagKeydown(event, field) {
      const val = this._tagInput[field];

      // Autocomplete navigation
      if (this._acIsOpen(field)) {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          this._autocomplete.highlight = Math.min(this._autocomplete.highlight + 1, this._autocomplete.results.length - 1);
          return;
        }
        if (event.key === 'ArrowUp') {
          event.preventDefault();
          this._autocomplete.highlight = Math.max(this._autocomplete.highlight - 1, -1);
          return;
        }
        if (event.key === 'Enter' && this._autocomplete.highlight >= 0) {
          event.preventDefault();
          this._acSelect(this._autocomplete.results[this._autocomplete.highlight], field);
          return;
        }
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          this._acClose();
          return;
        }
      }

      if ((event.key === 'Enter' || event.key === ',' || event.key === ';' || event.key === 'Tab') && val.trim()) {
        event.preventDefault();
        this.addTag(field, val);
      } else if (event.key === 'Backspace' && !val && this.compose[field].length) {
        this.compose[field].pop();
      }
    },

    handleTagPaste(event, field) {
      event.preventDefault();
      const text = (event.clipboardData || window.clipboardData).getData('text');
      const emails = _parseEmails(text);
      for (const e of emails) this.addTag(field, e);
    },

    handleComposeFiles(event) {
      this.compose.attachments = [...this.compose.attachments, ...event.target.files];
    },

    // ----- Autocomplete -----
    _acSearch(field) {
      if (this._autocomplete._timer) clearTimeout(this._autocomplete._timer);
      const q = (this._tagInput[field] || '').trim();
      if (q.length < 2) {
        this._acClose();
        return;
      }
      this._autocomplete.field = field;
      this._autocomplete._timer = setTimeout(async () => {
        this._autocomplete.loading = true;
        try {
          let url = `/api/v1/mail/contacts/autocomplete?q=${encodeURIComponent(q)}`;
          if (this.compose.account_id) url += `&account_id=${this.compose.account_id}`;
          const res = await this._fetch(url);
          if (res.ok) {
            const data = await res.json();
            // Filter out emails already added in any field
            const existing = new Set([
              ...this.compose.to, ...this.compose.cc, ...this.compose.bcc,
            ].map(e => e.toLowerCase()));
            this._autocomplete.results = data.filter(c => !existing.has(c.email.toLowerCase()));
            this._autocomplete.highlight = -1;
            this._autocomplete.show = this._autocomplete.results.length > 0;
          }
        } catch (e) {
          this._autocomplete.show = false;
        }
        this._autocomplete.loading = false;
      }, 300);
    },

    _acClose() {
      if (this._autocomplete._timer) clearTimeout(this._autocomplete._timer);
      this._autocomplete = { results: [], highlight: -1, show: false, loading: false, field: null, _timer: null };
    },

    _acSelect(contact, field) {
      const f = field || this._autocomplete.field;
      if (f) this.addTag(f, contact.email);
    },

    _acIsOpen(field) {
      return this._autocomplete.show && this._autocomplete.field === field;
    },

    async sendEmail() {
      // Commit any pending input
      if (this._tagInput.to) this.addTag('to', this._tagInput.to);
      if (this._tagInput.cc) this.addTag('cc', this._tagInput.cc);
      if (this._tagInput.bcc) this.addTag('bcc', this._tagInput.bcc);

      if (!this.compose.to.length) {
        this.compose.error = 'Please add at least one recipient';
        return;
      }

      this.compose.sending = true;
      this.compose.error = '';

      const formData = new FormData();
      formData.append('account_id', this.compose.account_id);
      formData.append('subject', this.compose.subject);
      formData.append('body_text', this.compose.body);
      const htmlBody = this.compose.body.replace(/\n/g, '<br>');
      formData.append('body_html', htmlBody);
      for (const addr of this.compose.to) formData.append('to', addr);
      for (const addr of this.compose.cc) formData.append('cc', addr);
      for (const addr of this.compose.bcc) formData.append('bcc', addr);
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
      return !!(this.compose.to.length || this.compose.subject || this.compose.body);
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

      const htmlBody = this.compose.body.replace(/\n/g, '<br>');

      const payload = {
        account_id: this.compose.account_id,
        to: this.compose.to,
        cc: this.compose.cc,
        bcc: this.compose.bcc,
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

    async autodiscoverSettings() {
      const email = (this.newAccount.email || '').trim();
      if (!email) return;

      this.autoDiscovering = true;
      this.accountError = '';

      try {
        const res = await this._fetch('/api/v1/mail/autodiscover', {
          method: 'POST',
          body: { email },
        });

        if (res.ok) {
          const data = await res.json();
          this.newAccount.imap_host = data.imap_host;
          this.newAccount.imap_port = data.imap_port;
          this.newAccount.imap_use_ssl = data.imap_use_ssl;
          this.newAccount.smtp_host = data.smtp_host;
          this.newAccount.smtp_port = data.smtp_port;
          this.newAccount.smtp_use_tls = data.smtp_use_tls;
          if (!this.newAccount.username) {
            this.newAccount.username = email;
          }
        } else {
          this.accountError = 'Could not auto-detect settings for this email. Please fill in manually.';
        }
      } catch (e) {
        this.accountError = 'Auto-detection failed. Please fill in settings manually.';
      }

      this.autoDiscovering = false;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
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
        if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [menu] });
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
        case 'delete':
          await this._deleteFolder(folder);
          break;
        case 'sync':
          this.syncAccount(folder.account_id);
          break;
      }
    },

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
        if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [menu] });
      });
    },

    async msgCtxAction(action) {
      const msg = this.msgCtx.msg;
      this.msgCtx.open = false;
      if (!msg) return;

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

    // ----- Move messages -----
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

    async moveMessages(msgUuids, targetFolder) {
      if (!msgUuids || !msgUuids.length || !targetFolder) return;
      this.batchInProgress = true;
      await this._fetch('/api/v1/mail/messages/batch-action', {
        method: 'POST',
        body: {
          message_ids: msgUuids,
          action: 'move',
          target_folder_id: targetFolder.uuid,
        },
      });
      // Remove moved messages from the current list
      this.messages = this.messages.filter(m => !msgUuids.includes(m.uuid));
      this.selectedMessages = this.selectedMessages.filter(id => !msgUuids.includes(id));
      // Clear detail if the viewed message was moved
      if (this.messageDetail && msgUuids.includes(this.messageDetail.uuid)) {
        this.selectedMessage = null;
        this.messageDetail = null;
        this._updateUrl(null);
      }
      // Refresh folder counts
      if (this.selectedFolder) {
        await this.loadFolders(this.selectedFolder.account_id);
        const flds = this.folders[this.selectedFolder.account_id] || [];
        const updated = flds.find(f => f.uuid === this.selectedFolder.uuid);
        if (updated) this.selectedFolder = updated;
      }
      this.batchInProgress = false;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    // ----- Drag & drop -----
    onMsgDragStart(event, msg) {
      // If the dragged message is in the selection, drag all selected; otherwise just this one
      let ids;
      if (this.selectedMessages.length > 0 && this.selectedMessages.includes(msg.uuid)) {
        ids = [...this.selectedMessages];
      } else {
        ids = [msg.uuid];
      }
      this._draggingMsgIds = ids;
      event.dataTransfer.effectAllowed = 'move';
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
        this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
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
        this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
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
        this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      } else {
        const data = await res.json().catch(() => ({}));
        await AppDialog.error({ message: data.detail || 'Failed to delete folder' });
      }
    },

    async _moveFolder(folder) {
      if (folder.folder_type !== 'other') return;

      const accountUuid = folder.account_id;
      const allFolders = this.folders[accountUuid] || [];

      // Collect own descendants (cannot move into self or own children)
      const delimiter = folder.name.includes('/') ? '/' : '.';
      const ownPrefix = folder.name + delimiter;
      const excluded = new Set([folder.uuid]);
      for (const f of allFolders) {
        if (f.name.startsWith(ownPrefix)) excluded.add(f.uuid);
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

      // Determine current parent for pre-selection
      const lastSep = folder.name.lastIndexOf(delimiter);
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
        this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      } else {
        const data = await res.json().catch(() => ({}));
        await AppDialog.error({ message: data.detail || 'Failed to move folder' });
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
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
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
      // Key change in x-for triggers element recreation; wait for Alpine + DOM before Lucide
      this.$nextTick(() => {
        setTimeout(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); }, 50);
      });
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
        case 'a':
          if (this.messageDetail) { e.preventDefault(); this.replyAll(this.messageDetail); }
          break;
        case 'f':
          if (this.messageDetail) { e.preventDefault(); this.forwardMessage(this.messageDetail); }
          break;
        case 's':
          if (this.messageDetail) { e.preventDefault(); this.toggleStar(this.messageDetail); }
          break;
        case 'u':
          if (this.messageDetail) { e.preventDefault(); this.toggleRead(this.messageDetail); }
          break;
        case '#':
          if (this.messageDetail) { e.preventDefault(); this.deleteMessage(this.messageDetail); }
          break;
        case 'Escape':
          if (this.selectedMessage) {
            this.selectedMessage = null;
            this.messageDetail = null;
            this._updateUrl(null);
          }
          break;
        case '?':
          e.preventDefault();
          const dlg = document.getElementById('mail-help-dialog');
          if (dlg) { dlg.showModal(); lucide?.createIcons(); }
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
    cleanName(raw) {
      return _cleanName(raw);
    },

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
      const opts = { month: 'short', day: 'numeric' };
      if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
      return d.toLocaleDateString([], opts);
    },

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
    account_id: '', to: [], cc: [], bcc: [],
    subject: '', body: '', is_reply: false,
    attachments: [], sending: false, error: '',
    draft_id: null, saving: false, last_saved: null,
    _saveTimer: null,
  };
}

/**
 * Parse a comma/semicolon separated string of emails into an array of trimmed, non-empty strings.
 */
function _parseEmails(str) {
  if (Array.isArray(str)) return str.filter(Boolean);
  if (!str || typeof str !== 'string') return [];
  return str.split(/[,;]\s*/).map(s => s.trim()).filter(Boolean);
}
