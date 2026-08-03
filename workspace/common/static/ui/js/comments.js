// ── Shared comments component ──────────────────────────────
// Backs the comment thread UI (files properties panel, task panel).
// listUrl is the collection endpoint; item endpoints are `${listUrl}/<uuid>`.
// Pair with the "ui/partials/comments.html" template partial.

window.commentsComponent = function commentsComponent(listUrl, currentUserId, canComment) {
  return {
    listUrl,
    currentUserId,
    canComment,
    comments: [],
    mentionUsers: [],
    loading: true,
    newBody: '',
    sending: false,
    editingId: null,
    editBody: '',
    composerFocused: false,
    mentionActive: false,
    mentionQuery: '',
    mentionResults: [],
    mentionHighlight: -1,
    mentionStartPos: -1,
    mentionField: 'new',
    mentionEl: null,

    async init() {
      await this.loadComments();
    },

    _url(commentUuid) {
      return commentUuid ? `${this.listUrl}/${commentUuid}` : this.listUrl;
    },

    async loadComments() {
      this.loading = true;
      try {
        const resp = await fetch(this._url(), { credentials: 'same-origin' });
        if (resp.ok) {
          const data = await resp.json();
          this.comments = data.comments;
          this.mentionUsers = data.mention_users || [];
        }
      } catch (e) { /* ignore */ }
      this.loading = false;
    },

    async addComment() {
      if (!this.canComment || !this.newBody.trim() || this.sending) return;
      this.sending = true;
      try {
        const resp = await fetch(this._url(), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
          body: JSON.stringify({ body: this.newBody.trim() }),
        });
        if (resp.ok) {
          this.newBody = '';
          if (this.$refs.composer) {
            this.$refs.composer.style.height = '';
            this.$refs.composer.blur();
          }
          await this.loadComments();
        }
      } catch (e) { /* ignore */ }
      this.sending = false;
    },

    autoGrow(el) {
      el.style.height = 'auto';
      // scrollHeight excludes borders (border-box), hence the offset delta.
      el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
    },

    startEdit(comment) {
      this.editingId = comment.uuid;
      this.editBody = comment.body;
    },

    cancelEdit() {
      this.editingId = null;
      this.editBody = '';
    },

    async saveEdit(commentUuid) {
      if (!this.canComment || !this.editBody.trim()) return;
      try {
        const resp = await fetch(this._url(commentUuid), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
          body: JSON.stringify({ body: this.editBody.trim() }),
        });
        if (resp.ok) {
          this.editingId = null;
          this.editBody = '';
          await this.loadComments();
        }
      } catch (e) { /* ignore */ }
    },

    async deleteComment(commentUuid) {
      if (!this.canComment) return;
      try {
        const resp = await fetch(this._url(commentUuid), {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (resp.ok) {
          await this.loadComments();
        }
      } catch (e) { /* ignore */ }
    },

    // ── Mention autocomplete (composer + edit textareas) ─────
    handleMentionInput(el, field) {
      const pos = el.selectionStart;
      const text = el.value.substring(0, pos);
      // A mention starts at the beginning of the text or after whitespace.
      const match = text.match(/(?:^|\s)@(\w*)$/);
      if (match) {
        this.mentionActive = true;
        this.mentionField = field;
        this.mentionEl = el;
        this.mentionQuery = match[1].toLowerCase();
        this.mentionStartPos = pos - match[1].length - 1;
        this.filterMentionResults();
      } else {
        this.closeMentionDropdown();
      }
    },

    filterMentionResults() {
      const q = this.mentionQuery;
      const results = [];
      for (const u of this.mentionUsers) {
        if (u.id === this.currentUserId) continue;
        const searchStr = `${u.username} ${u.first_name || ''} ${u.last_name || ''}`.toLowerCase();
        if (!q || searchStr.includes(q)) results.push(u);
      }
      this.mentionResults = results.slice(0, 8);
      this.mentionHighlight = this.mentionResults.length > 0 ? 0 : -1;
    },

    handleMentionKeydown(e) {
      if (!this.mentionActive || this.mentionResults.length === 0) return;
      // Ctrl/meta+enter keeps its submit meaning even with the dropdown open.
      if (e.ctrlKey || e.metaKey) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.mentionHighlight = (this.mentionHighlight + 1) % this.mentionResults.length;
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.mentionHighlight = this.mentionHighlight <= 0
          ? this.mentionResults.length - 1
          : this.mentionHighlight - 1;
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        if (this.mentionHighlight >= 0) {
          this.insertMention(this.mentionResults[this.mentionHighlight]);
        }
      }
    },

    insertMention(user) {
      const el = this.mentionEl;
      const field = this.mentionField;
      const value = field === 'edit' ? this.editBody : this.newBody;
      const caret = el ? el.selectionStart : value.length;
      const before = value.substring(0, this.mentionStartPos);
      const after = value.substring(caret);
      const mention = `@${user.username} `;
      const next = before + mention + after;
      if (field === 'edit') {
        this.editBody = next;
      } else {
        this.newBody = next;
      }
      this.closeMentionDropdown();
      if (el && typeof this.$nextTick === 'function') {
        this.$nextTick(() => {
          const newPos = before.length + mention.length;
          el.setSelectionRange(newPos, newPos);
          el.focus();
        });
      }
    },

    closeMentionDropdown() {
      this.mentionActive = false;
      this.mentionQuery = '';
      this.mentionResults = [];
      this.mentionHighlight = -1;
      this.mentionStartPos = -1;
      this.mentionEl = null;
    },

    formatDate(iso) {
      const d = new Date(iso);
      const now = new Date();
      const diff = now - d;
      if (diff < 60000) return 'just now';
      if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
      if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
      if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
      const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
      return d.toLocaleDateString(undefined, { timeZone: tz });
    },
  };
};
