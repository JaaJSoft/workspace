// Photos: the module shell (sidebar, viewer hand-off, selection, albums) and
// the sentinel that appends the next page of the timeline as it scrolls into
// view.

const PHOTOS_MOBILE_QUERY = '(max-width: 1023px)';

// The swap targets of this page. A response that lacks one (a login page once
// the session expired) must leave the live element alone: alpine-ajax's
// default is to remove it, which would empty the timeline.
const PHOTOS_SWAP_TARGETS = [
  'photos-nav', 'photos-content', 'photos-header', 'timeline-grid', 'timeline-more',
];

// The file actions a photo's context menu offers, out of everything the files
// registry may return for it. The registry still decides which of these the
// user may run on a given file; this only drops the ones that mean nothing on
// a timeline (cut, paste, pin, extract...).
const PHOTO_ACTIONS = [
  'view', 'download', 'copy_link', 'toggle_favorite', 'share', 'properties', 'rename', 'delete',
];

// The album actions its header menu offers: the ones on the album as a whole.
// The rest (add, remove, cover, reorder) gate controls elsewhere on the page.
const ALBUM_MENU_ACTIONS = ['rename', 'edit_description', 'change_sort', 'delete'];

// Room the context menu needs, to keep it inside the viewport.
const PHOTOS_MENU_WIDTH = 224;
const PHOTOS_MENU_HEIGHT = 340;

// How long a finger rests on a tile before it selects it, in ms.
const PHOTOS_LONG_PRESS = 450;
// A contextmenu event this soon after a touch is the long press itself.
const PHOTOS_TOUCH_GRACE = 1500;

const ALBUMS_API = '/api/v1/photos/albums';

function photosJson(method, url, body) {
  const init = { method, headers: { 'X-CSRFToken': getCSRFToken() } };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  return fetch(url, init).then((response) => {
    if (!response.ok) throw new Error(String(response.status));
    return response.status === 204 ? null : response.json();
  });
}

// The album the page shows, as the server rendered it, or null on the
// timeline. Read from the DOM each time: a navigation swaps it.
function photosCurrentAlbum() {
  const el = document.getElementById('album-data');
  if (!el) return null;
  try {
    return JSON.parse(el.textContent);
  } catch (_) {
    return null;
  }
}

// The uuids of `ordered` from `from` to `to`, both included, whichever
// comes first; empty when either is missing.
function photosRange(ordered, from, to) {
  const start = ordered.indexOf(from);
  const end = ordered.indexOf(to);
  if (start === -1 || end === -1) return [];
  return ordered.slice(Math.min(start, end), Math.max(start, end) + 1);
}

function photosCountLabel(count) {
  return count === 1 ? '1 photo' : `${count} photos`;
}

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

    // Selected tile uuids, in the order they were picked. Each tile mirrors
    // its own state in data-selected.
    selection: [],
    _selectionAnchor: null,
    _longPressTimer: null,
    _longPressed: false,
    _touchAt: 0,

    // The album on screen and what its registry lets the viewer do there.
    albumUuid: null,
    albumActions: [],
    _albumGeneration: 0,
    albumMenu: { open: false, x: 0, y: 0, actions: null },
    picker: { open: false, loading: false, busy: false, albums: [], files: [] },
    _pickerGeneration: 0,
    _drag: null,

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
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.selection.length && !document.querySelector('dialog[open]')) {
          this.clearSelection();
        }
      });
      this.syncAlbum();
    },

    // A navigation replaced the listing: the tiles the selection named are
    // gone, and the album on screen may have changed.
    onMerged(event) {
      const id = event.target && event.target.id;
      if (id === 'photos-content') this.clearSelection();
      if (id === 'photos-content' || id === 'photos-header') this.syncAlbum();
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

    tileClicked(event, tile) {
      if (this.selection.length) {
        this.toggleTileSelection(tile, event);
        return;
      }
      this.openPhoto(tile);
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
      // On a touch screen the long press that fires contextmenu selects the
      // tile instead (see startLongPress).
      if (!anchor && Date.now() - this._touchAt < PHOTOS_TOUCH_GRACE) return;
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
          tile.querySelectorAll('[data-name-title]').forEach((el) => { el.title = newName; });
          tile.querySelectorAll('img[alt]').forEach((el) => { el.alt = newName; });
          const menuButton = tile.querySelector('[aria-haspopup="menu"]');
          if (menuButton) menuButton.setAttribute('aria-label', `More actions for ${newName}`);
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
      this._removeTiles([uuid]);
    },

    _removeTiles(uuids) {
      const gone = new Set(uuids);
      this.selection = this.selection.filter((uuid) => !gone.has(uuid));
      for (const uuid of uuids) {
        const tile = this._tile(uuid);
        if (!tile) continue;
        const day = tile.closest('[data-day]');
        tile.remove();
        // A day group left without photos would keep its date over nothing.
        if (day && !day.querySelector('[data-uuid]')) day.remove();
      }
    },

    _refresh(targets) {
      return this.$ajax(window.location.href, { targets, focus: false }).catch(() => {});
    },

    // ── Selection ───────────────────────────────────────

    _tileUuids() {
      return Array.from(document.querySelectorAll('#timeline-grid [data-uuid]'))
        .map((tile) => tile.dataset.uuid);
    },

    _markSelected(uuid, selected) {
      const tile = this._tile(uuid);
      if (tile) tile.dataset.selected = selected ? '1' : '0';
    },

    isSelected(uuid) {
      return this.selection.includes(uuid);
    },

    // A click on the check mark, or on the tile once something is selected.
    // Shift extends from the last tile picked, as in Files: every tile
    // between the two, in the order the page shows them, gets selected.
    toggleTileSelection(tile, event) {
      const uuid = tile.dataset.uuid;
      const anchor = this._selectionAnchor;
      if (event && event.shiftKey && anchor && anchor !== uuid && this.isSelected(anchor)) {
        const range = photosRange(this._tileUuids(), anchor, uuid);
        if (range.length) {
          const added = range.filter((u) => !this.isSelected(u));
          added.forEach((u) => this._markSelected(u, true));
          this.selection = this.selection.concat(added);
          this._selectionAnchor = uuid;
          return;
        }
      }
      if (this.isSelected(uuid)) {
        this.selection = this.selection.filter((u) => u !== uuid);
        this._markSelected(uuid, false);
        if (anchor === uuid) this._selectionAnchor = null;
      } else {
        this.selection = this.selection.concat([uuid]);
        this._markSelected(uuid, true);
        this._selectionAnchor = uuid;
      }
    },

    clearSelection() {
      this.selection.forEach((uuid) => this._markSelected(uuid, false));
      this.selection = [];
      this._selectionAnchor = null;
    },

    startLongPress(tile) {
      this._touchAt = Date.now();
      this.cancelLongPress();
      this._longPressTimer = setTimeout(() => {
        this._longPressTimer = null;
        this._longPressed = true;
        this.toggleTileSelection(tile, null);
        if (navigator.vibrate) navigator.vibrate(10);
      }, PHOTOS_LONG_PRESS);
    },

    cancelLongPress() {
      if (this._longPressTimer) clearTimeout(this._longPressTimer);
      this._longPressTimer = null;
    },

    // The finger lifts. After a long press the browser would follow with a
    // click, which would toggle the tile straight back: cancelling the
    // touchend is what stops that click from being sent. Waiting for the
    // click to swallow it instead would not do: not every browser sends it,
    // and the next real tap would be the one swallowed.
    endLongPress(event) {
      this.cancelLongPress();
      if (this._longPressed) {
        this._longPressed = false;
        if (event.cancelable) event.preventDefault();
      }
    },

    // ── Albums ──────────────────────────────────────────

    // Reads the album the page now shows and what the viewer may do to it.
    // The generation drops an answer about an album the page has left.
    syncAlbum() {
      const album = photosCurrentAlbum();
      const generation = ++this._albumGeneration;
      this.albumUuid = album ? album.uuid : null;
      this.albumActions = [];
      if (!album) return Promise.resolve();
      return this._fetchAlbumActions(album.uuid).then((actions) => {
        if (generation === this._albumGeneration) {
          this.albumActions = actions.map((a) => a.id);
        }
      }).catch(() => {});
    },

    _fetchAlbumActions(uuid) {
      return photosJson('POST', `${ALBUMS_API}/actions`, { uuids: [uuid] })
        .then((data) => (data && data[uuid]) || []);
    },

    countLabel(count) {
      return photosCountLabel(count);
    },

    albumAllows(actionId) {
      return this.albumUuid !== null && this.albumActions.includes(actionId);
    },

    async createAlbum() {
      const title = await this._askAlbumTitle();
      if (!title) return;
      try {
        const album = await photosJson('POST', ALBUMS_API, { title });
        window.location.href = album.url;
      } catch (_) {
        window.AppAlert.error('Failed to create the album');
      }
    },

    _askAlbumTitle(value = '') {
      return AppDialog.prompt({
        title: value ? 'Rename album' : 'New album',
        message: value ? '' : 'Albums gather photos without moving them in Files.',
        value,
        placeholder: 'Summer 2024',
        okLabel: value ? 'Rename' : 'Create',
        okClass: 'btn-module',
        icon: 'book-image',
        iconClass: 'bg-module/15 text-module',
      }).then((title) => (title || '').trim());
    },

    // The albums the photos can go to: every album whose registry offers
    // add_items to the viewer.
    async openAlbumPicker(uuids) {
      if (!uuids || !uuids.length) return;
      const generation = ++this._pickerGeneration;
      this.picker = { open: true, loading: true, busy: false, albums: [], files: uuids.slice() };
      const dialog = document.getElementById('album-picker');
      if (dialog && !dialog.open) dialog.showModal();
      try {
        const albums = await photosJson('GET', ALBUMS_API);
        let allowed = [];
        if (albums.length) {
          const actions = await photosJson('POST', `${ALBUMS_API}/actions`, {
            uuids: albums.map((a) => a.uuid),
          });
          allowed = albums.filter((a) => (actions[a.uuid] || []).some((x) => x.id === 'add_items'));
        }
        if (generation !== this._pickerGeneration) return;
        this.picker.albums = allowed;
      } catch (_) {
        if (generation === this._pickerGeneration) window.AppAlert.error('Failed to load your albums');
      }
      if (generation === this._pickerGeneration) this.picker.loading = false;
    },

    _closePicker() {
      const dialog = document.getElementById('album-picker');
      if (dialog && dialog.open) dialog.close();
      this.picker.open = false;
    },

    async addToAlbum(album) {
      const files = this.picker.files;
      this.picker.busy = true;
      try {
        const result = await photosJson('POST', `${ALBUMS_API}/${album.uuid}/items`, { files });
        this._closePicker();
        this.clearSelection();
        const added = result ? result.added : 0;
        window.AppAlert.success(
          added ? `Added ${photosCountLabel(added)} to "${album.title}"` : `Already in "${album.title}"`,
          { duration: 3000 },
        );
        this._refresh(['photos-nav']);
      } catch (_) {
        window.AppAlert.error('Failed to add to the album');
      }
      this.picker.busy = false;
    },

    async createAlbumFromPicker() {
      const files = this.picker.files;
      this._closePicker();
      const title = await this._askAlbumTitle();
      if (!title) return;
      try {
        const album = await photosJson('POST', ALBUMS_API, { title, files });
        this.clearSelection();
        window.AppAlert.success(`Created "${album.title}" with ${photosCountLabel(album.count)}`, { duration: 3000 });
        this._refresh(['photos-nav']);
      } catch (_) {
        window.AppAlert.error('Failed to create the album');
      }
    },

    async removeFromAlbum(uuids) {
      const uuid = this.albumUuid;
      if (!uuid || !this.albumAllows('remove_items') || !uuids.length) return;
      const files = uuids.slice();
      try {
        await photosJson('POST', `${ALBUMS_API}/${uuid}/items/remove`, { files });
      } catch (_) {
        window.AppAlert.error('Failed to remove from the album');
        return;
      }
      this._removeTiles(files);
      this.clearSelection();
      window.AppAlert.success(`Removed ${photosCountLabel(files.length)} from the album`, { duration: 2000 });
      // Emptied: the whole listing, for its empty state.
      const emptied = !document.querySelector('#timeline-grid [data-uuid]');
      this._refresh(['photos-nav', emptied ? 'photos-content' : 'photos-header']);
    },

    async setAlbumCover(fileUuid) {
      const uuid = this.albumUuid;
      if (!uuid || !this.albumAllows('set_cover')) return;
      try {
        await photosJson('PATCH', `${ALBUMS_API}/${uuid}`, { cover: fileUuid });
      } catch (_) {
        window.AppAlert.error('Failed to set the cover');
        return;
      }
      this._refresh(['photos-nav', 'photos-header']);
    },

    // The album header's menu, under its button.
    openAlbumMenu(anchor) {
      const album = photosCurrentAlbum();
      if (!album) return;
      const rect = anchor.getBoundingClientRect();
      const x = Math.max(4, Math.min(rect.left, window.innerWidth - PHOTOS_MENU_WIDTH - 4));
      const generation = ++this._ctxGeneration;
      this.albumMenu = { open: true, x, y: rect.bottom + 4, actions: null };
      this._fetchAlbumActions(album.uuid).then((actions) => {
        if (generation !== this._ctxGeneration) return;
        this.albumMenu.actions = actions.filter((a) => ALBUM_MENU_ACTIONS.includes(a.id));
      }).catch(() => {
        if (generation === this._ctxGeneration) this.albumMenu.actions = [];
      });
    },

    async runAlbumAction(action) {
      this.albumMenu.open = false;
      const album = photosCurrentAlbum();
      if (!album) return;
      const url = `${ALBUMS_API}/${album.uuid}`;
      try {
        switch (action.id) {
          case 'rename': {
            const title = await this._askAlbumTitle(album.title);
            if (!title || title === album.title) return;
            await photosJson('PATCH', url, { title });
            this._refresh(['photos-nav', 'photos-header']);
            break;
          }
          case 'edit_description': {
            const description = await AppDialog.prompt({
              title: 'Album description',
              value: album.description || '',
              placeholder: 'What these photos are about',
              okLabel: 'Save',
              okClass: 'btn-module',
              icon: 'align-left',
              iconClass: 'bg-module/15 text-module',
              inputSize: 'textarea',
            });
            if (description === null) return;
            await photosJson('PATCH', url, { description: description.trim() });
            this._refresh(['photos-header']);
            break;
          }
          case 'change_sort': {
            const sortMode = album.sort_mode === 'manual' ? 'capture_date' : 'manual';
            await photosJson('PATCH', url, { sort_mode: sortMode });
            this._refresh(['photos-nav', 'photos-content']);
            break;
          }
          case 'delete': {
            const ok = await AppDialog.confirm({
              title: 'Delete album',
              message: `Delete "${album.title}"? Its photos stay where they are in Files.`,
              okLabel: 'Delete album',
              okClass: 'btn-error',
              icon: 'trash-2',
              iconClass: 'bg-error/10 text-error',
            });
            if (!ok) return;
            await photosJson('DELETE', url);
            window.location.href = '/photos';
            break;
          }
        }
      } catch (_) {
        window.AppAlert.error('Failed to update the album');
      }
    },

    // ── Manual order ────────────────────────────────────
    // Tiles of an album sorted by hand drag onto one another. A drag that
    // starts on a selected tile carries the whole selection, in page order.

    startTileDrag(event, tile) {
      if (!this.albumAllows('reorder')) {
        event.preventDefault();
        return;
      }
      const uuid = tile.dataset.uuid;
      const uuids = this.isSelected(uuid)
        ? this._tileUuids().filter((u) => this.isSelected(u))
        : [uuid];
      this._drag = { uuids };
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('application/x-photos-album-items', uuids.join(','));
    },

    _dropSide(event, tile) {
      const rect = tile.getBoundingClientRect();
      return event.clientX < rect.left + rect.width / 2 ? 'before' : 'after';
    },

    overTileDrag(event, tile) {
      if (!this._drag || this._drag.uuids.includes(tile.dataset.uuid)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      tile.dataset.dropSide = this._dropSide(event, tile);
    },

    leaveTileDrag(tile) {
      delete tile.dataset.dropSide;
    },

    endTileDrag() {
      this._drag = null;
      document.querySelectorAll('#timeline-grid [data-drop-side]').forEach((el) => {
        delete el.dataset.dropSide;
      });
    },

    async dropTileDrag(event, tile) {
      const drag = this._drag;
      const target = tile.dataset.uuid;
      if (!drag || drag.uuids.includes(target) || !this.albumUuid) return;
      event.preventDefault();
      const side = this._dropSide(event, tile);
      this.endTileDrag();
      try {
        await photosJson('POST', `${ALBUMS_API}/${this.albumUuid}/reorder`, {
          files: drag.uuids,
          [side]: target,
        });
      } catch (_) {
        window.AppAlert.error('Failed to reorder the album');
        return;
      }
      const moved = drag.uuids.map((u) => this._tile(u)).filter(Boolean);
      if (side === 'before') {
        moved.forEach((el) => tile.before(el));
      } else {
        tile.after(...moved);
      }
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
