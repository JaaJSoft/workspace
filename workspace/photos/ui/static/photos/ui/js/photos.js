// Photos: the module shell (sidebar, viewer hand-off) and the sentinel that
// appends the next page of the timeline as it scrolls into view.

const PHOTOS_MOBILE_QUERY = '(max-width: 1023px)';

// The swap targets of this page. A response that lacks one (a login page once
// the session expired) must leave the live element alone: alpine-ajax's
// default is to remove it, which would empty the timeline.
const PHOTOS_SWAP_TARGETS = ['photos-nav', 'photos-content', 'timeline-grid', 'timeline-more'];

// The file actions a photo's context menu offers, out of everything the files
// registry may return for it. The registry still decides which of these the
// user may run on a given file; this only drops the ones that mean nothing on
// a timeline (cut, paste, pin, extract...).
const PHOTO_ACTIONS = [
  'view', 'download', 'copy_link', 'toggle_favorite', 'share', 'properties', 'rename', 'delete',
];

// Room the context menu needs, to keep it inside the viewport.
const PHOTOS_MENU_WIDTH = 224;
const PHOTOS_MENU_HEIGHT = 340;

// The size slider's step and the tile width in px of each step, as the page
// was rendered with them.
function photosTilePrefs() {
  const el = document.getElementById('photo-tile-data');
  try {
    const data = el ? JSON.parse(el.textContent) : null;
    if (data && Array.isArray(data.widths)) return data;
  } catch (_) {
    // Unreadable: the root keeps the width the server rendered.
  }
  return { size: 1, widths: [] };
}

window.photosApp = function photosApp() {
  const tags = window.tagsMixin();
  const tile = photosTilePrefs();

  return {
    // The properties panel is the Files one, tag dropdown included: the
    // tags mixin works on `selectedFile`, which the panel seeds.
    ...tags,
    ...window.propertiesPanelMixin(),

    collapsed: window.sidebarPreference.initial(),
    tileSize: tile.size,
    _tileWidths: tile.widths,
    ctxMenu: { open: false, x: 0, y: 0, photo: null, actions: null },
    _ctxGeneration: 0,
    _tagsLoaded: false,

    init() {
      window.addEventListener('open-properties', (e) => {
        this.showProperties(e.detail.uuid, e.detail.nodeType);
      });
      window.addEventListener('shares-changed', () => this.reloadPropertiesPanel());
      window.addEventListener('rename-item', (e) => this.renamePhoto(e.detail.uuid, e.detail.name));
      // Tag counts in the sidebar follow assignments made from the panel.
      window.addEventListener('tags-changed', () => {
        this.$ajax(window.location.href, { target: 'photos-nav', focus: false });
      });
    },

    // The stored preference is a desktop one: below `lg` the drawer is
    // off-canvas and opens as the full sidebar.
    sidebarCollapsed() {
      if (window.matchMedia(PHOTOS_MOBILE_QUERY).matches) return false;
      return this.collapsed;
    },

    toggleCollapse() {
      if (window.matchMedia(PHOTOS_MOBILE_QUERY).matches) return;
      this.collapsed = !this.collapsed;
      window.sidebarPreference.save('photos', this.collapsed);
    },

    closeDrawer() {
      const toggle = document.getElementById('photos-drawer');
      if (toggle) toggle.checked = false;
    },

    // ── Tile size ───────────────────────────────────────

    tileStyle() {
      const width = this._tileWidths[this.tileSize - 1];
      return width ? { '--photo-tile': width + 'px' } : {};
    },

    // Never awaited: the grid has already resized, and a refused write only
    // costs the size on the next load.
    saveTileSize() {
      return fetch('/api/v1/settings/photos/tile_size', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ value: this.tileSize }),
      }).catch(() => {});
    },

    keepMissingTarget(event) {
      const target = event.detail && event.detail.target;
      if (target && PHOTOS_SWAP_TARGETS.includes(target.id)) {
        event.preventDefault();
      }
    },

    openPhoto(tile) {
      window.dispatchEvent(new CustomEvent('open-file-viewer', {
        detail: {
          uuid: tile.dataset.uuid,
          name: tile.dataset.displayName,
          type: tile.dataset.fileType,
        },
      }));
    },

    _tile(uuid) {
      return document.querySelector(`#timeline-grid [data-uuid="${uuid}"]`);
    },

    // ── Properties ──────────────────────────────────────

    showProperties(uuid, nodeType) {
      // The tag dropdown in the panel lists every tag; nothing else on the
      // page needs them, so they load the first time the panel opens.
      if (!this._tagsLoaded) {
        this._tagsLoaded = true;
        this.loadTags();
      }
      this.openPropertiesPanel(uuid, nodeType);
    },

    // An assignment changes the sidebar's tag counts, like the mixin's own
    // create/edit/delete announcements do.
    async toggleFileTag(tag) {
      await tags.toggleFileTag.call(this, tag);
      window.dispatchEvent(new CustomEvent('tags-changed'));
    },

    // Navigation contract of the shared tag partials (see tags.js).
    tagViewHref(tag) {
      return '/photos?tag=' + encodeURIComponent(tag.uuid);
    },

    openTagView(tag) {
      window.location.href = this.tagViewHref(tag);
    },

    isTagViewActive(tag) {
      return new URLSearchParams(window.location.search).get('tag') === tag.uuid;
    },

    // ── Context menu ────────────────────────────────────

    // Opened at the cursor on a right-click, or under the "..." button that
    // was pressed - a keyboard press has no cursor position to go by.
    openCtxMenu(event, tile, anchor) {
      let x = event.clientX;
      let y = event.clientY;
      if (anchor) {
        const rect = anchor.getBoundingClientRect();
        x = rect.right - PHOTOS_MENU_WIDTH;
        y = rect.bottom + 4;
      }
      x = Math.max(4, Math.min(x, window.innerWidth - PHOTOS_MENU_WIDTH - 4));
      y = Math.max(4, Math.min(y, window.innerHeight - PHOTOS_MENU_HEIGHT - 4));

      const photo = {
        uuid: tile.dataset.uuid,
        name: tile.dataset.displayName,
        type: tile.dataset.fileType,
        filesUrl: tile.dataset.filesUrl,
      };
      const generation = ++this._ctxGeneration;
      this.ctxMenu = { open: true, x, y, photo, actions: null };

      window.fileActions.fetchActions([photo.uuid]).then((data) => {
        if (generation !== this._ctxGeneration) return;
        const available = (data && data[photo.uuid]) || [];
        this.ctxMenu.actions = available.filter((a) => PHOTO_ACTIONS.includes(a.id));
      }).catch(() => {
        if (generation === this._ctxGeneration) this.ctxMenu.actions = [];
      });
    },

    closeCtxMenu() {
      this.ctxMenu.open = false;
    },

    runCtxAction(action) {
      const photo = this.ctxMenu.photo;
      this.closeCtxMenu();
      if (!photo) return;

      switch (action.id) {
        case 'view':
          window.dispatchEvent(new CustomEvent('open-file-viewer', {
            detail: { uuid: photo.uuid, name: photo.name, type: photo.type },
          }));
          break;
        case 'copy_link':
          this.copyLink(photo.uuid);
          break;
        case 'toggle_favorite':
          this.toggleFavorite(photo.uuid, !!(action.state && action.state.is_favorite));
          break;
        case 'share':
          window.dispatchEvent(new CustomEvent('open-share-modal', {
            detail: { uuid: photo.uuid, name: photo.name, nodeType: 'file' },
          }));
          break;
        case 'properties':
          this.showProperties(photo.uuid, 'file');
          break;
        case 'rename':
          window.fileActions.showRenameDialog(photo.uuid, photo.name);
          break;
        case 'delete':
          this.trashPhoto(photo.uuid, photo.name);
          break;
      }
    },

    // ── Actions ─────────────────────────────────────────

    copyLink(uuid) {
      const url = new URL(window.location.origin + window.location.pathname);
      url.searchParams.set('open', uuid);
      navigator.clipboard.writeText(url.toString())
        .then(() => window.AppAlert.success('Link copied to clipboard', { duration: 2000 }))
        .catch(() => window.AppAlert.error('Failed to copy link'));
    },

    // The star on a tile. The server only renders it where the registry
    // offers the toggle; the check here keeps a stale page from sending a
    // request the endpoint would refuse anyway.
    async toggleTileFavorite(el) {
      const tile = el.closest('[data-uuid]');
      if (!tile || tile.dataset.canFavorite !== '1' || tile.dataset.busy === '1') return;
      tile.dataset.busy = '1';
      try {
        await this.toggleFavorite(tile.dataset.uuid, tile.dataset.favorite === '1');
      } finally {
        delete tile.dataset.busy;
      }
    },

    async toggleFavorite(uuid, isFavorite) {
      try {
        const response = await fetch(`/api/v1/files/${uuid}/favorite`, {
          method: isFavorite ? 'DELETE' : 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!response.ok) throw new Error();
        const tile = this._tile(uuid);
        if (tile) tile.dataset.favorite = isFavorite ? '0' : '1';
      } catch (_) {
        window.AppAlert.error('Failed to update favorites');
      }
    },

    async renamePhoto(uuid, name) {
      try {
        const file = await window.fileActions.renameItem(uuid, name);
        const tile = this._tile(uuid);
        if (tile) {
          const newName = (file && file.name) || name;
          tile.dataset.displayName = newName;
          tile.querySelectorAll('[title]').forEach((el) => {
            if (el.title !== 'More actions') el.title = newName;
          });
        }
        this.reloadPropertiesPanel();
      } catch (err) {
        window.AppAlert.error(err.message || 'Failed to rename');
      }
    },

    async trashPhoto(uuid, name) {
      const ok = await AppDialog.confirm({
        title: 'Move to trash',
        message: `Move "${name}" to the trash? It can be restored from the Files trash.`,
        okLabel: 'Move to trash',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        const response = await fetch(`/api/v1/files/${uuid}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!response.ok) throw new Error();
      } catch (_) {
        window.AppAlert.error('Failed to move the photo to the trash');
        return;
      }
      if (this.propertiesUuid === uuid) this.closePropertiesPanel();
      const tile = this._tile(uuid);
      if (!tile) return;
      const day = tile.closest('[data-day]');
      tile.remove();
      // A day group left without photos would keep its date over nothing.
      if (day && !day.querySelector('[data-uuid]')) day.remove();
    },
  };
};

// Each page ends with its own sentinel, which the next page replaces: the
// observer lives and dies with the element it watches, so there is never
// more than one, and never one watching a page that is already loaded.
window.timelineSentinel = function timelineSentinel(url) {
  return {
    loading: false,
    _observer: null,

    init() {
      if (!('IntersectionObserver' in window)) return;
      // Observed against the scroll container, not the viewport: the margin
      // only reaches ahead of the fold relative to the root, and the
      // viewport never scrolls on this page.
      this._observer = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting)) this.load();
        },
        { root: this.$el.closest('#photos-content'), rootMargin: '0px 0px 800px 0px' },
      );
      this._observer.observe(this.$el);
    },

    destroy() {
      if (this._observer) this._observer.disconnect();
    },

    async load() {
      if (this.loading) return;
      this.loading = true;
      try {
        await this.$ajax(url, { targets: ['timeline-grid', 'timeline-more'], focus: false });
      } catch (_) {
        // The button stays for a retry; the observer will not fire again
        // while the sentinel sits still in view.
      }
      this.loading = false;
    },
  };
};
