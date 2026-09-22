// A share entry is addressed to a user or to a group; the two share one
// list, one permission toggle and one save loop, and are told apart by
// `type` ('user' | 'group'). Ids overlap across the two tables, so every
// map and set below is keyed by shareKey(), never by the bare id.
function shareKey(type, id) {
  return type + ':' + id;
}

window.shareModal = function shareModal() {
  return {
    open: false,
    fileUuid: null,
    fileName: '',
    nodeType: 'file',
    shares: [],        // existing shares from server, each { type, id, ... }
    groups: [],        // every group, for the group picker
    pendingAdds: [],   // targets to add (staged), each { type, id, ..., permission }
    pendingRemovals: new Set(), // entry keys to remove (staged)
    pendingPermissionChanges: new Map(), // entry key -> newPermission for existing shares
    loading: false,
    saving: false,
    // Share links state
    shareLinks: [],
    linksLoading: false,
    creatingLink: false,
    showLinkForm: false,
    newLinkExpiry: '',
    newLinkPassword: '',
    newLinkMode: 'read',
    newLinkMaxBytes: '',
    newLinkMaxCount: '',
    // The generator is mounted under x-if, so closing it tears the panel down
    // and its destroy() drops the value it drew.
    linkGeneratorOpen: false,
    // Whether the password field still holds the panel's value. A redraw after
    // Use rewrites the field only while this holds, so the link is never
    // created with one password while the panel shows, and copies, another.
    linkPasswordFollowsGenerator: false,
    // The field is masked until the sender asks otherwise or applies a
    // generated value: they have to pass that one on with the link.
    linkPasswordRevealed: false,
    // Reported beside the panel, not through AppAlert: the toasts render
    // under the dialog's backdrop, out of sight of whoever pressed Copy.
    linkGeneratorError: '',

    // A share is addressed to a user or a group, and the two id spaces
    // overlap, so every entry is keyed by both.
    entryKey(type, id) {
      return type + ':' + id;
    },

    get displayList() {
      const permChanges = this.pendingPermissionChanges;
      const existing = this.shares.map(s => {
        const key = this.entryKey(s.type, s.id);
        return {
          ...s,
          key,
          permission: permChanges.has(key) ? permChanges.get(key) : s.permission,
          _pending: false,
          _removed: this.pendingRemovals.has(key),
        };
      });
      const added = this.pendingAdds.map(t => ({
        ...t,
        key: this.entryKey(t.type, t.id),
        permission: t.permission || 'ro',
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
        this.nodeType = e.detail.nodeType || 'file';
        this.open = true;
        this.pendingAdds = [];
        this.pendingRemovals = new Set();
        this.pendingPermissionChanges = new Map();
        this.loadShares();
        this.loadGroups();
        this.loadShareLinks();
        this.$nextTick(() => {
          const dlg = this.$refs.shareDialog;
          if (dlg && !dlg.open) dlg.showModal();
        });
      });

      window.addEventListener('share-user-selected', (e) => {
        this.stageAdd({ type: 'user', ...e.detail.user });
      });
      window.addEventListener('share-group-selected', (e) => {
        this.stageAdd({ type: 'group', ...e.detail.group });
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

    async loadGroups() {
      try {
        const resp = await fetch('/api/v1/groups', { credentials: 'same-origin' });
        this.groups = resp.ok ? await resp.json() : [];
      } catch (e) {
        this.groups = [];
      }
    },

    // Groups not already shared with or staged, for the group picker.
    selectableGroups() {
      const taken = new Set(
        this.displayList
          .filter(entry => entry.type === 'group' && !entry._removed)
          .map(entry => String(entry.id))
      );
      return this.groups.filter(g => !taken.has(String(g.id)));
    },

    stageAdd(target) {
      if (!target || !target.type) return;
      const key = this.entryKey(target.type, target.id);
      // Already in existing shares?
      if (this.shares.some(s => this.entryKey(s.type, s.id) === key)) {
        // If it was marked for removal, undo that
        if (this.pendingRemovals.has(key)) {
          this.pendingRemovals.delete(key);
          this.pendingRemovals = new Set(this.pendingRemovals);
        }
        return;
      }
      // Already in pending adds?
      if (this.pendingAdds.some(t => this.entryKey(t.type, t.id) === key)) return;
      this.pendingAdds = [...this.pendingAdds, { ...target, permission: 'ro' }];
    },

    stageRemove(key) {
      // If it's a pending add, just remove from the list
      const idx = this.pendingAdds.findIndex(t => this.entryKey(t.type, t.id) === key);
      if (idx !== -1) {
        this.pendingAdds = this.pendingAdds.filter(t => this.entryKey(t.type, t.id) !== key);
        return;
      }
      // Otherwise mark existing share for removal
      this.pendingRemovals.add(key);
      this.pendingRemovals = new Set(this.pendingRemovals);
    },

    undoRemove(key) {
      this.pendingRemovals.delete(key);
      this.pendingRemovals = new Set(this.pendingRemovals);
    },

    stagePermissionChange(key, permission, isPending) {
      if (isPending) {
        // Update permission on pending add
        this.pendingAdds = this.pendingAdds.map(t =>
          this.entryKey(t.type, t.id) === key ? { ...t, permission } : t
        );
        return;
      }
      // For existing shares, check if it differs from original
      const original = this.shares.find(s => this.entryKey(s.type, s.id) === key);
      if (original && original.permission === permission) {
        this.pendingPermissionChanges.delete(key);
      } else {
        this.pendingPermissionChanges.set(key, permission);
      }
      this.pendingPermissionChanges = new Map(this.pendingPermissionChanges);
    },

    // The request body naming a share target: { shared_with }, { group } or
    // { project }.
    targetBody(type, id) {
      if (type === 'group') return { group: id };
      if (type === 'project') return { project: id };
      return { shared_with: id };
    },

    // Display name and kind label of an entry, whatever its target type.
    entryName(entry) {
      return entry.type === 'user' ? entry.username : entry.name;
    },

    entrySubtitle(entry) {
      if (entry.type === 'project') return 'Project';
      if (entry.type === 'group') return 'Group';
      return ((entry.first_name || '') + ' ' + (entry.last_name || '')).trim();
    },

    splitKey(key) {
      const sep = key.indexOf(':');
      return { type: key.slice(0, sep), id: key.slice(sep + 1) };
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
      for (const target of this.pendingAdds) {
        try {
          const resp = await fetch(`/api/v1/files/${this.fileUuid}/share`, {
            method: 'POST', headers,
            body: JSON.stringify({ ...this.targetBody(target.type, target.id), permission: target.permission || 'ro' }),
          });
          if (!resp.ok) errors++;
        } catch (e) { errors++; }
      }

      // Process permission changes for existing shares
      for (const [key, permission] of this.pendingPermissionChanges) {
        const { type, id } = this.splitKey(key);
        try {
          const resp = await fetch(`/api/v1/files/${this.fileUuid}/share`, {
            method: 'POST', headers,
            body: JSON.stringify({ ...this.targetBody(type, id), permission }),
          });
          if (!resp.ok) errors++;
        } catch (e) { errors++; }
      }

      // Process removals
      for (const key of this.pendingRemovals) {
        const { type, id } = this.splitKey(key);
        try {
          const resp = await fetch(`/api/v1/files/${this.fileUuid}/share`, {
            method: 'DELETE', headers,
            body: JSON.stringify(this.targetBody(type, id)),
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
      this.closeLinkForm();
    },

    closeLinkForm() {
      this.showLinkForm = false;
      this.newLinkExpiry = '';
      this.newLinkPassword = '';
      this.newLinkMode = 'read';
      this.newLinkMaxBytes = '';
      this.newLinkMaxCount = '';
      this.linkGeneratorOpen = false;
      this.linkPasswordFollowsGenerator = false;
      this.linkPasswordRevealed = false;
      this.linkGeneratorError = '';
    },

    // --- Link password generator ---

    toggleLinkGenerator() {
      this.linkGeneratorOpen = !this.linkGeneratorOpen;
      this.linkGeneratorError = '';
    },

    applyGeneratedLinkPassword(value) {
      if (!value) return;
      this.newLinkPassword = value;
      this.linkPasswordFollowsGenerator = true;
      this.linkPasswordRevealed = true;
      this.linkGeneratorOpen = false;
      this.linkGeneratorError = '';
    },

    // A failed draw announces an empty value; blanking a password the sender
    // already applied would silently drop the protection from the link.
    trackGeneratedLinkPassword(value) {
      if (!this.linkPasswordFollowsGenerator || !value) return;
      this.newLinkPassword = value;
    },

    noteLinkPasswordEdited() {
      this.linkPasswordFollowsGenerator = false;
    },

    // The plain clipboard, with no clearing timer: the sender is about to
    // paste this next to the link, and taking it back would lose it.
    async copyGeneratedLinkPassword(value) {
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        this.linkGeneratorError = '';
      } catch (err) {
        this.linkGeneratorError = 'That password could not be copied.';
      }
    },

    // --- Share Links ---

    canShareWithPeople() {
      return this.nodeType === 'file';
    },

    canChooseMode() {
      return this.nodeType === 'folder';
    },

    availableModes() {
      return [
        { value: 'read', label: 'Read only' },
        { value: 'drop', label: 'Upload only' },
        { value: 'both', label: 'Read and upload' },
      ];
    },

    showsCaps() {
      return this.newLinkMode === 'drop' || this.newLinkMode === 'both';
    },

    modeLabel(mode) {
      const found = this.availableModes().find(m => m.value === mode);
      return found ? found.label : mode;
    },

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
      // The picked day means "valid through that day" in the user's zone.
      if (this.newLinkExpiry) {
        const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
        body.expires_at = window.wallClockToIso(this.newLinkExpiry + 'T23:59:59', tz);
      }
      if (this.newLinkPassword) body.password = this.newLinkPassword;
      if (this.canChooseMode()) body.mode = this.newLinkMode;
      if (this.showsCaps()) {
        if (this.newLinkMaxBytes) body.max_file_bytes = Number(this.newLinkMaxBytes);
        if (this.newLinkMaxCount) body.max_file_count = Number(this.newLinkMaxCount);
      }
      try {
        const resp = await fetch(`/api/v1/files/${this.fileUuid}/share-links`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify(body),
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.closeLinkForm();
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
      // Expiry is stored as end-of-day in the user's zone: formatting in
      // that zone shows the day the user picked.
      const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
      return new Date(expiresAt).toLocaleDateString(undefined, { timeZone: tz });
    },
  };
};
