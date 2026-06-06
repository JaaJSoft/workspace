/**
 * Mail application — Alpine.js component
 *
 * The main app object is composed from domain mixins (defined in their own
 * files and loaded BEFORE this script in the template). Each mixin returns
 * an object literal which is spread into the root, so all mixins share the
 * same `this`. State stays here; methods live in the mixins.
 */
function _eagerAccounts() {
  try {
    const el = document.getElementById('accounts-data');
    if (el) return JSON.parse(el.textContent);
  } catch (e) {}
  return [];
}

function mailApp() {
  return {
    // State
    accounts: _eagerAccounts(),
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
    classifyingFolder: false,
    hasMoreMessages: false,
    currentPage: 1,
    totalMessages: 0,
    _messagesRequestId: 0,

    // Add / Edit account
    newAccount: _defaultNewAccount(),
    accountError: '',
    addingAccount: false,
    autoDiscovering: false,
    oauthProviders: [],
    editAccount: null,
    editAccountError: '',
    savingAccount: false,

    // Preferences (reactive copy)
    mailPrefs: { ...window._mailPrefsCache },

    // Filters
    filters: {
      search: '',
      unread: false,
      starred: false,
      attachments: false,
    },
    _searchTimer: null,

    // Account context menu
    accountCtx: { open: false, x: 0, y: 0, account: null },

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

    // Hidden folders
    hiddenFolders: [],
    hiddenFoldersSearch: '',

    // Compose
    compose: _defaultCompose(),
    showCcBcc: false,

    // Labels
    labels: {},
    selectedLabel: null,
    unifiedInbox: false,
    labelModal: { accountId: null, uuid: null, name: '', color: 'ghost', icon: '', saving: false, error: '' },
    labelCtx: { open: false, x: 0, y: 0, label: null },
    dragOverLabel: null,

    // Mail rules (per-account, opened via account context menu)
    rulesAccount: null,
    rulesList: [],
    rulesSearch: '',
    rulesEditing: null,
    rulesForm: {
      mode: 'simple',
      simpleCondition: { field: 'from', op: 'contains', value: '' },
      simpleAction: { type: 'mark_read' },
      advancedConditionsText: '',
      advancedActionsText: '',
      error: '',
      saving: false,
    },
    rulesShowAdvancedHelp: false,

    // Apply-a-rule-to-a-folder sub-view (sibling of the edit form)
    rulesApplying: null,
    rulesApplyFolderId: '',
    rulesApplyResult: null,
    rulesApplyBusy: false,

    // AI features
    aiSummarizing: false,
    aiSummary: null,
    _aiPollInterval: null,
    showAICompose: false,
    aiComposePrompt: '',
    aiComposing: false,
    _aiComposePollInterval: null,

    // Autocomplete
    _autocomplete: { results: [], highlight: -1, show: false, loading: false, field: null, _timer: null, _requestId: 0 },

    // ── Compose mailApp from domain mixins ─────────────────
    // Each mixin returns an object literal with its own methods. They all
    // share the same `this` at runtime — state stays in this root object.
    ...mailAccountsMixin(),
    ...mailFoldersMixin(),
    ...mailMessagesMixin(),
    ...mailComposeMixin(),
    ...mailLabelsMixin(),
    ...mailAiMixin(),
    ...mailRulesMixin(),
    ...mailRulesFormMixin(),

    // ── Computed ───────────────────────────────────────────
    // Defined on the root (not a mixin) so the getter survives object spread —
    // `{...mixin()}` copies a getter as a fixed value at spread time, but a
    // getter declared directly on the literal stays a getter on the result,
    // which Alpine can react to when its dependencies change.
    get filteredHiddenFolders() {
      const q = (this.hiddenFoldersSearch || '').toLowerCase();
      if (!q) return this.hiddenFolders;
      return this.hiddenFolders.filter(f => f.display_name.toLowerCase().includes(q));
    },

    async init() {
      // Load preferences
      await window._mailPrefsReady;
      this.mailPrefs = { ...window._mailPrefsCache };
      window.addEventListener('mail:preferences-changed', (e) => {
        this.mailPrefs = { ...e.detail };
      });

      // Load accounts from embedded data
      try {
        const el = document.getElementById('accounts-data');
        if (el) this.accounts = JSON.parse(el.textContent);
      } catch (e) {}

      // Load all folders and labels, then restore URL state
      const folderLoads = [];
      const labelLoads = [];
      for (const acc of this.accounts) {
        this.expandedAccounts[acc.uuid] = true;
        this.syncingAccounts[acc.uuid] = false;
        folderLoads.push(this.loadFolders(acc.uuid));
        labelLoads.push(this.fetchLabels(acc.uuid));
      }
      await Promise.all([...folderLoads, ...labelLoads]);

      // Restore state from URL params
      const params = new URLSearchParams(window.location.search);
      const folderId = params.get('folder');
      const labelId = params.get('label');
      const msgId = params.get('message');

      if (folderId) {
        const folder = this._findFolderById(folderId);
        if (folder) {
          await this.selectFolder(folder);
          if (msgId) this._openMessageById(msgId);
        } else {
          this.selectUnifiedInbox();
        }
      } else if (labelId) {
        const label = this._findLabelById(labelId);
        if (label) {
          this.selectLabel(label);
          if (msgId) this._openMessageById(msgId);
        } else {
          this.selectUnifiedInbox();
        }
      } else if (msgId) {
        this._openMessageById(msgId);
      } else if (this.accounts.length > 0) {
        this.selectUnifiedInbox();
      }

      // Handle browser back/forward on mobile
      window.addEventListener('popstate', () => {
        const p = new URLSearchParams(window.location.search);
        const folderId = p.get('folder');
        const labelId = p.get('label');
        const msgId = p.get('message');

        if (msgId) {
          this._openMessageById(msgId);
          return;
        }

        // Clear message selection
        this.selectedMessage = null;
        this.messageDetail = null;
        this.selectedMessages = [];
        this.currentPage = 1;
        this._resetFilters();

        // Restore folder/label/unified inbox state
        if (folderId) {
          const folder = this._findFolderById(folderId);
          if (folder) {
            this.unifiedInbox = false;
            this.selectedFolder = folder;
            this.selectedLabel = null;
            this.loadMessages();
          }
        } else if (labelId) {
          const label = this._findLabelById(labelId);
          if (label) {
            this.unifiedInbox = false;
            this.selectedLabel = label;
            this.selectedFolder = null;
            this.loadMessages();
          }
        } else {
          this.unifiedInbox = true;
          this.selectedFolder = null;
          this.selectedLabel = null;
          this.loadMessages();
        }
      });

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

      // Load OAuth providers
      const oauthEl = document.getElementById('oauth-providers-data');
      if (oauthEl) {
        try { this.oauthProviders = JSON.parse(oauthEl.textContent); } catch(e) {}
      }

      // Listen for OAuth2 popup result
      const handleOAuth2Result = async (data) => {
        if (data?.type === 'oauth2-success' && data.account) {
          this.accounts.push(data.account);
          this.expandedAccounts[data.account.uuid] = true;
          await this.loadFolders(data.account.uuid);
          await this.fetchLabels(data.account.uuid);
          this.closeAddAccount();
          this.syncAccount(data.account.uuid);

        } else if (data?.type === 'oauth2-error') {
          this.accountError = data.error || 'OAuth2 connection failed';
        }
      };

      // BroadcastChannel: works even when window.opener is null (cross-origin redirects)
      try {
        const bc = new BroadcastChannel('oauth2');
        bc.onmessage = (event) => handleOAuth2Result(event.data);
      } catch(e) {}

      // Fallback: postMessage via opener (same-origin popups)
      window.addEventListener('message', (event) => {
        if (event.origin !== window.location.origin) return;
        handleOAuth2Result(event.data);
      });
    },

    // ── Shared HTTP helper ─────────────────────────────────
    async _fetch(url, opts = {}) {
      opts.headers = {
        ...opts.headers,
        'X-CSRFToken': getCSRFToken(),
      };
      if (opts.body && !(opts.body instanceof FormData)) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.body);
      }
      opts.credentials = 'same-origin';
      const res = await fetch(url, opts);
      return res;
    },

    // ── Keyboard ───────────────────────────────────────────
    handleKeydown(e) {
      // Don't handle if in an input/textarea
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      // Don't handle if a dialog is open
      if (document.querySelector('dialog[open]')) return;
      // Don't intercept browser shortcuts (Ctrl/Cmd+key)
      if (e.ctrlKey || e.metaKey || e.altKey) return;

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
            if (this.isMobile()) {
              history.back();
            } else {
              this.selectedMessage = null;
              this.messageDetail = null;
              this._updateUrl(null);
            }
          }
          break;
        case '?':
          e.preventDefault();
          {
            const dlg = document.getElementById('mail-help-dialog');
            if (dlg) { dlg.showModal(); }
          }
          break;
      }
    },

    // ── URL state sync ─────────────────────────────────────
    _updateUrl(messageUuid, {push = false} = {}) {
      const url = new URL(window.location);
      url.search = '';

      if (this.selectedFolder) {
        url.searchParams.set('folder', this.selectedFolder.uuid);
      } else if (this.selectedLabel) {
        url.searchParams.set('label', this.selectedLabel.uuid);
      }
      // unified inbox = no folder/label params (default)

      if (messageUuid) {
        url.searchParams.set('message', messageUuid);
      }

      if (push) {
        history.pushState(null, '', url);
      } else {
        history.replaceState(null, '', url);
      }
    },

    _findFolderById(uuid) {
      for (const accId in this.folders) {
        const found = (this.folders[accId] || []).find(f => f.uuid === uuid);
        if (found) return found;
      }
      return null;
    },

    _findLabelById(uuid) {
      for (const accId in this.labels) {
        const found = (this.labels[accId] || []).find(l => l.uuid === uuid);
        if (found) return found;
      }
      return null;
    },

    // ── UI helpers ─────────────────────────────────────────
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

    _closeDrawerOnMobile() {
      if (this.isMobile()) {
        const toggle = document.getElementById('mail-drawer');
        if (toggle) toggle.checked = false;
      }
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

    formatFullDate(dateStr) {
      if (!dateStr) return '';
      return new Date(dateStr).toLocaleString([], {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
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
    subject: '', body: '', is_reply: false, reply_message_id: null,
    attachments: [], picked_files: [], sending: false, error: '',
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
