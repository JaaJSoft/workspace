// userSelector is now provided globally by common/static/ui/js/user_selector.js

window.shareModal = function shareModal() {
  return {
    open: false,
    fileUuid: null,
    fileName: '',
    shares: [],        // existing shares from server
    pendingAdds: [],   // users to add (staged), each has { ...user, permission: 'ro' }
    pendingRemovals: new Set(), // user IDs to remove (staged)
    pendingPermissionChanges: new Map(), // userId → newPermission for existing shares
    loading: false,
    saving: false,
    // Share links state
    shareLinks: [],
    linksLoading: false,
    creatingLink: false,
    showLinkForm: false,
    newLinkExpiry: '',
    newLinkPassword: '',

    get displayList() {
      const permChanges = this.pendingPermissionChanges;
      const existing = this.shares.map(s => ({
        ...s,
        permission: permChanges.has(s.id) ? permChanges.get(s.id) : s.permission,
        _pending: false,
        _removed: this.pendingRemovals.has(s.id),
      }));
      const added = this.pendingAdds.map(u => ({
        id: u.id,
        username: u.username,
        first_name: u.first_name,
        last_name: u.last_name,
        permission: u.permission || 'ro',
        _pending: true,
        _removed: false,
      }));
      return [...existing, ...added];
    },

    get hasChanges() {
      return this.pendingAdds.length > 0 || this.pendingRemovals.size > 0 || this.pendingPermissionChanges.size > 0;
    },

    init() {
      window.addEventListener('open-share-modal', (e) => {
        this.fileUuid = e.detail.uuid;
        this.fileName = e.detail.name;
        this.open = true;
        this.pendingAdds = [];
        this.pendingRemovals = new Set();
        this.pendingPermissionChanges = new Map();
        this.loadShares();
        this.loadShareLinks();
        this.$nextTick(() => {
          const dlg = this.$refs.shareDialog;
          if (dlg && !dlg.open) dlg.showModal();
        });
      });

      window.addEventListener('share-user-selected', (e) => {
        this.stageAdd(e.detail.user);
      });
    },

    async loadShares() {
      if (!this.fileUuid) return;
      this.loading = true;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/shares`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.shares = await resp.json();
        }
      } catch (e) {
        this.shares = [];
      } finally {
        this.loading = false;
      }
    },

    stageAdd(user) {
      if (!user) return;
      // Already in existing shares?
      if (this.shares.some(s => s.id === user.id)) {
        // If it was marked for removal, undo that
        if (this.pendingRemovals.has(user.id)) {
          this.pendingRemovals.delete(user.id);
          this.pendingRemovals = new Set(this.pendingRemovals);
        }
        return;
      }
      // Already in pending adds?
      if (this.pendingAdds.some(u => u.id === user.id)) return;
      this.pendingAdds = [...this.pendingAdds, { ...user, permission: 'ro' }];
    },

    stageRemove(userId) {
      // If it's a pending add, just remove from the list
      const idx = this.pendingAdds.findIndex(u => u.id === userId);
      if (idx !== -1) {
        this.pendingAdds = this.pendingAdds.filter(u => u.id !== userId);
        return;
      }
      // Otherwise mark existing share for removal
      this.pendingRemovals.add(userId);
      this.pendingRemovals = new Set(this.pendingRemovals);
    },

    undoRemove(userId) {
      this.pendingRemovals.delete(userId);
      this.pendingRemovals = new Set(this.pendingRemovals);
    },

    stagePermissionChange(userId, permission, isPending) {
      if (isPending) {
        // Update permission on pending add
        this.pendingAdds = this.pendingAdds.map(u =>
          u.id === userId ? { ...u, permission } : u
        );
        return;
      }
      // For existing shares, check if it differs from original
      const original = this.shares.find(s => s.id === userId);
      if (original && original.permission === permission) {
        this.pendingPermissionChanges.delete(userId);
      } else {
        this.pendingPermissionChanges.set(userId, permission);
      }
      this.pendingPermissionChanges = new Map(this.pendingPermissionChanges);
    },

    async save() {
      if (!this.fileUuid || !this.hasChanges) return;
      this.saving = true;
      const csrfToken = getCSRFToken();
      const headers = {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      };
      let errors = 0;

      // Process additions
      for (const user of this.pendingAdds) {
        try {
          const resp = await fetch(`/api/v1/files/${this.fileUuid}/share`, {
            method: 'POST', headers,
            body: JSON.stringify({ shared_with: user.id, permission: user.permission || 'ro' }),
          });
          if (!resp.ok) errors++;
        } catch (e) { errors++; }
      }

      // Process permission changes for existing shares
      for (const [userId, permission] of this.pendingPermissionChanges) {
        try {
          const resp = await fetch(`/api/v1/files/${this.fileUuid}/share`, {
            method: 'POST', headers,
            body: JSON.stringify({ shared_with: userId, permission }),
          });
          if (!resp.ok) errors++;
        } catch (e) { errors++; }
      }

      // Process removals
      for (const userId of this.pendingRemovals) {
        try {
          const resp = await fetch(`/api/v1/files/${this.fileUuid}/share`, {
            method: 'DELETE', headers,
            body: JSON.stringify({ shared_with: userId }),
          });
          if (!resp.ok) errors++;
        } catch (e) { errors++; }
      }

      this.saving = false;

      if (errors > 0 && window.AppAlert) {
        window.AppAlert.error(`Some changes failed (${errors} error${errors > 1 ? 's' : ''})`);
      } else if (window.AppAlert) {
        window.AppAlert.success('Sharing updated');
      }

      // Reset staged changes, reload, and close
      this.pendingAdds = [];
      this.pendingRemovals = new Set();
      this.pendingPermissionChanges = new Map();
      await this.loadShares();
      window.dispatchEvent(new CustomEvent('shares-changed'));
      // Refresh the folder browser so the shared badge updates
      this.$ajax(window.location.pathname + window.location.search, { target: 'folder-browser' });
      this.closeModal();
    },

    closeModal() {
      this.open = false;
      const dlg = this.$refs.shareDialog;
      if (dlg && dlg.open) dlg.close();
      this.fileUuid = null;
      this.fileName = '';
      this.shares = [];
      this.pendingAdds = [];
      this.pendingRemovals = new Set();
      this.pendingPermissionChanges = new Map();
      this.shareLinks = [];
      this.showLinkForm = false;
      this.newLinkExpiry = '';
      this.newLinkPassword = '';
    },

    // --- Share Links ---

    async loadShareLinks() {
      if (!this.fileUuid) return;
      this.linksLoading = true;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/share-links`, {
          credentials: 'same-origin',
        });
        if (resp.ok) this.shareLinks = await resp.json();
      } catch (e) {
        this.shareLinks = [];
      }
      this.linksLoading = false;
    },

    async createShareLink() {
      if (!this.fileUuid) return;
      this.creatingLink = true;
      const csrfToken = getCSRFToken();
      const body = {};
      if (this.newLinkExpiry) body.expires_at = new Date(this.newLinkExpiry).toISOString();
      if (this.newLinkPassword) body.password = this.newLinkPassword;
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/share-links`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify(body),
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.showLinkForm = false;
          this.newLinkExpiry = '';
          this.newLinkPassword = '';
          await this.loadShareLinks();
        }
      } catch (e) {}
      this.creatingLink = false;
    },

    async deleteShareLink(linkUuid) {
      const csrfToken = getCSRFToken();
      try {
        await fetch(`/api/v1/files/${this.fileUuid}/share-links/${linkUuid}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': csrfToken },
          credentials: 'same-origin',
        });
        this.shareLinks = this.shareLinks.filter(l => l.uuid !== linkUuid);
      } catch (e) {}
    },

    copyShareLink(url) {
      navigator.clipboard.writeText(url).then(() => {
        if (window.AppAlert) window.AppAlert.success('Link copied to clipboard');
      });
    },

    formatLinkExpiry(expiresAt) {
      if (!expiresAt) return 'Permanent';
      const d = new Date(expiresAt);
      return d.toLocaleDateString();
    },
  };
};
