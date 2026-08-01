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
    loading: true,
    newBody: '',
    sending: false,
    editingId: null,
    editBody: '',
    composerFocused: false,

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
          this.comments = await resp.json();
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
