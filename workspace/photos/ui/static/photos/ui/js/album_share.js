// The album share modal (album_share_modal.html): who the album is shared
// with and in which role, whether viewers may download, and its public
// links. Member changes are staged and saved together, as in the Files share
// modal; a link is created or revoked at once.

const ALBUM_ROLES = [
  { value: 'viewer', label: 'Viewer', hint: 'Browses the album' },
  { value: 'contributor', label: 'Contributor', hint: 'Also adds their own photos, and removes them' },
  { value: 'manager', label: 'Manager', hint: 'Also edits the album, its members and its links' },
];

// A member is a user, a group or a project, and their ids overlap: every map
// and set below is keyed by both.
function albumShareKey(type, id) {
  return `${type}:${id}`;
}

// The request body naming a share target.
function albumShareTarget(type, id) {
  if (type === 'group') return { group: Number(id) };
  if (type === 'project') return { project: id };
  return { shared_with: Number(id) };
}

window.albumShareModal = function albumShareModal() {
  return {
    roles: ALBUM_ROLES,
    albumUuid: null,
    albumTitle: '',
    owner: null,
    shares: [],
    groups: [],
    // The download setting as the server holds it, and the staged one
    // (null while unchanged).
    savedAllowDownload: true,
    pendingAllowDownload: null,
    pendingAdds: [],
    pendingRemovals: new Set(),
    pendingRoles: new Map(),
    loading: false,
    saving: false,
    links: [],
    linksLoading: false,
    showLinkForm: false,
    creatingLink: false,
    newLinkExpiry: '',
    newLinkPassword: '',
    newLinkAllowDownload: false,
    linkPasswordRevealed: false,

    init() {
      window.addEventListener('open-album-share', (e) => this.openModal(e.detail));
      window.addEventListener('album-share-user-selected', (e) => {
        this.stageAdd({ type: 'user', ...e.detail.user });
      });
      window.addEventListener('album-share-group-selected', (e) => {
        this.stageAdd({ type: 'group', ...e.detail.group });
      });
    },

    _api(suffix = '') {
      return `/api/v1/photos/albums/${this.albumUuid}${suffix}`;
    },

    openModal(detail) {
      this.albumUuid = detail.uuid;
      this.albumTitle = detail.title || '';
      this._resetStaged();
      this.owner = null;
      this.shares = [];
      this.links = [];
      this.loadMembers();
      this.loadGroups();
      this.loadLinks();
      const dialog = this.$refs.dialog;
      if (dialog && !dialog.open) dialog.showModal();
    },

    _resetStaged() {
      this.pendingAdds = [];
      this.pendingRemovals = new Set();
      this.pendingRoles = new Map();
      this.pendingAllowDownload = null;
    },

    async loadMembers() {
      this.loading = true;
      try {
        const response = await fetch(this._api('/shares'));
        if (!response.ok) throw new Error(String(response.status));
        const data = await response.json();
        this.owner = data.owner;
        this.shares = data.shares;
        this.savedAllowDownload = data.allow_download;
      } catch (_) {
        this.shares = [];
      }
      this.loading = false;
    },

    async loadGroups() {
      try {
        const response = await fetch('/api/v1/groups');
        this.groups = response.ok ? await response.json() : [];
      } catch (_) {
        this.groups = [];
      }
    },

    // Every member as the modal shows them: the saved ones with their
    // staged role or removal, then the staged additions.
    displayList() {
      const saved = this.shares.map((share) => {
        const key = albumShareKey(share.type, share.id);
        return {
          ...share,
          key,
          role: this.pendingRoles.has(key) ? this.pendingRoles.get(key) : share.role,
          _pending: false,
          _removed: this.pendingRemovals.has(key),
        };
      });
      const added = this.pendingAdds.map((target) => ({
        ...target,
        key: albumShareKey(target.type, target.id),
        _pending: true,
        _removed: false,
      }));
      return [...saved, ...added];
    },

    allowDownload() {
      return this.pendingAllowDownload === null ? this.savedAllowDownload : this.pendingAllowDownload;
    },

    hasChanges() {
      return this.pendingAdds.length > 0
        || this.pendingRemovals.size > 0
        || this.pendingRoles.size > 0
        || this.pendingAllowDownload !== null;
    },

    // Groups neither in the album nor staged, nor the one owning it.
    selectableGroups() {
      const taken = new Set(
        this.displayList()
          .filter((entry) => entry.type === 'group' && !entry._removed)
          .map((entry) => String(entry.id)),
      );
      if (this.owner && this.owner.type === 'group') taken.add(String(this.owner.id));
      return this.groups.filter((group) => !taken.has(String(group.id)));
    },

    stageAdd(target) {
      if (!target || !target.type) return;
      if (this.owner && this.owner.type === target.type && String(this.owner.id) === String(target.id)) return;
      const key = albumShareKey(target.type, target.id);
      if (this.shares.some((share) => albumShareKey(share.type, share.id) === key)) {
        // Adding back a member staged for removal keeps them.
        this.undoRemove(key);
        return;
      }
      if (this.pendingAdds.some((t) => albumShareKey(t.type, t.id) === key)) return;
      this.pendingAdds = [...this.pendingAdds, { ...target, role: 'viewer' }];
    },

    stageRemove(key) {
      if (this.pendingAdds.some((t) => albumShareKey(t.type, t.id) === key)) {
        this.pendingAdds = this.pendingAdds.filter((t) => albumShareKey(t.type, t.id) !== key);
        return;
      }
      this.pendingRemovals = new Set([...this.pendingRemovals, key]);
    },

    undoRemove(key) {
      const removals = new Set(this.pendingRemovals);
      removals.delete(key);
      this.pendingRemovals = removals;
    },

    stageRoleChange(key, role, pending) {
      if (pending) {
        this.pendingAdds = this.pendingAdds.map((t) => (
          albumShareKey(t.type, t.id) === key ? { ...t, role } : t
        ));
        return;
      }
      const roles = new Map(this.pendingRoles);
      const saved = this.shares.find((share) => albumShareKey(share.type, share.id) === key);
      if (saved && saved.role === role) {
        roles.delete(key);
      } else {
        roles.set(key, role);
      }
      this.pendingRoles = roles;
    },

    stageAllowDownload(value) {
      this.pendingAllowDownload = value === this.savedAllowDownload ? null : value;
    },

    roleHint(role) {
      const found = ALBUM_ROLES.find((r) => r.value === role);
      return found ? found.hint : '';
    },

    entryName(entry) {
      return entry.type === 'user' ? entry.username : entry.name;
    },

    entrySubtitle(entry) {
      if (entry.type === 'project') return 'Project';
      if (entry.type === 'group') return 'Group';
      return `${entry.first_name || ''} ${entry.last_name || ''}`.trim();
    },

    // One request per change; a failure leaves the others in place and is
    // counted in the message.
    async save() {
      if (!this.albumUuid || !this.hasChanges()) return;
      this.saving = true;
      const send = (method, url, body) => fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify(body),
      }).then((response) => response.ok, () => false);
      const requests = [];
      for (const target of this.pendingAdds) {
        requests.push(() => send('POST', this._api('/shares'), {
          ...albumShareTarget(target.type, target.id), role: target.role,
        }));
      }
      for (const [key, role] of this.pendingRoles) {
        const sep = key.indexOf(':');
        requests.push(() => send('POST', this._api('/shares'), {
          ...albumShareTarget(key.slice(0, sep), key.slice(sep + 1)), role,
        }));
      }
      for (const key of this.pendingRemovals) {
        const sep = key.indexOf(':');
        requests.push(() => send('DELETE', this._api('/shares'), albumShareTarget(key.slice(0, sep), key.slice(sep + 1))));
      }
      if (this.pendingAllowDownload !== null) {
        requests.push(() => send('PATCH', this._api(), { allow_download: this.pendingAllowDownload }));
      }
      let failed = 0;
      for (const request of requests) {
        if (!(await request())) failed += 1;
      }
      this.saving = false;
      if (failed) {
        window.AppAlert.error(`Some changes failed (${failed} error${failed > 1 ? 's' : ''})`);
      } else {
        window.AppAlert.success('Sharing updated');
      }
      window.dispatchEvent(new CustomEvent('album-shares-changed', { detail: { uuid: this.albumUuid } }));
      this.closeModal();
    },

    closeModal() {
      const dialog = this.$refs.dialog;
      if (dialog && dialog.open) dialog.close();
      this._resetStaged();
      this.closeLinkForm();
    },

    // ── Public links ────────────────────────────────────

    async loadLinks() {
      this.linksLoading = true;
      try {
        const response = await fetch(this._api('/links'));
        this.links = response.ok ? await response.json() : [];
      } catch (_) {
        this.links = [];
      }
      this.linksLoading = false;
    },

    closeLinkForm() {
      this.showLinkForm = false;
      this.newLinkExpiry = '';
      this.newLinkPassword = '';
      this.newLinkAllowDownload = false;
      this.linkPasswordRevealed = false;
    },

    async createLink() {
      if (!this.albumUuid || this.creatingLink) return;
      this.creatingLink = true;
      const body = { allow_download: !!this.newLinkAllowDownload };
      // The picked day means "valid through that day" in the user's zone.
      if (this.newLinkExpiry) {
        const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
        body.expires_at = window.wallClockToIso(`${this.newLinkExpiry}T23:59:59`, tz);
      }
      if (this.newLinkPassword) body.password = this.newLinkPassword;
      try {
        const response = await fetch(this._api('/links'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error(String(response.status));
        const link = await response.json();
        this.links = [link, ...this.links];
        this.closeLinkForm();
        this.copyLink(link.url);
      } catch (_) {
        window.AppAlert.error('Failed to create the link');
      }
      this.creatingLink = false;
    },

    async revokeLink(linkUuid) {
      try {
        const response = await fetch(this._api(`/links/${linkUuid}`), {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!response.ok && response.status !== 404) throw new Error(String(response.status));
        this.links = this.links.filter((link) => link.uuid !== linkUuid);
      } catch (_) {
        window.AppAlert.error('Failed to revoke the link');
      }
    },

    copyLink(url) {
      navigator.clipboard.writeText(url)
        .then(() => window.AppAlert.success('Link copied to clipboard', { duration: 2000 }))
        .catch(() => {});
    },

    formatExpiry(expiresAt) {
      if (!expiresAt) return 'Permanent';
      const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
      return `Until ${new Date(expiresAt).toLocaleDateString(undefined, { timeZone: tz })}`;
    },

    viewsLabel(count) {
      return count === 1 ? '1 view' : `${count || 0} views`;
    },
  };
};
