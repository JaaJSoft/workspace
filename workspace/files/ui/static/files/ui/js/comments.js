// ── File Comments ──────────────────────────────────────────

window.fileComments = function fileComments(fileUuid, currentUserId) {
  return {
    fileUuid,
    currentUserId,
    comments: [],
    loading: true,
    newBody: '',
    sending: false,
    editingId: null,
    editBody: '',


    async init() {
      await this.loadComments();
    },

    async loadComments() {
      this.loading = true;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/comments`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.comments = await resp.json();
        }
      } catch (e) { /* ignore */ }
      this.loading = false;
    },

    async addComment() {
      if (!this.newBody.trim() || this.sending) return;
      this.sending = true;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/comments`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
          body: JSON.stringify({ body: this.newBody.trim() }),
        });
        if (resp.ok) {
          this.newBody = '';
          await this.loadComments();
        }
      } catch (e) { /* ignore */ }
      this.sending = false;
    },

    _refreshIcons() {
    },

    startEdit(comment) {
      this.editingId = comment.uuid;
      this.editBody = comment.body;
      this._refreshIcons();
    },

    cancelEdit() {
      this.editingId = null;
      this.editBody = '';
      this._refreshIcons();
    },

    async saveEdit(commentUuid) {
      if (!this.editBody.trim()) return;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/comments/${commentUuid}`, {
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
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/comments/${commentUuid}`, {
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
      return d.toLocaleDateString();
    },
  };
};
