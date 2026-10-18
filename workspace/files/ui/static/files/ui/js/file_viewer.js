// The Files viewer, opened anywhere with an `open-file-viewer` event on window
// (detail {uuid, name, type, at?, edit?}) or a `?open=<uuid>` URL. Exactly one
// shell per page listens, and the page picks how it shows by the partial it
// includes: fileViewerModal() over the page (file_viewer_modal.html), or
// fileViewerInline() in place of the listing (file_viewer_inline.html).
//
// Both spread fileViewerShellMixin(), which owns the state, the gallery, ?open
// and the keyboard, and each supplies the presentation hooks:
//   _present()      show the shell; called again while open by gallery steps
//   _dismiss()      hide it, ending in _onClosed()
//   _keysBlocked()  something above the viewer owns the keyboard
//   _onOpened(uuid) optional, after each file starts loading
//
// Each file's actions come from the registry (POST /api/v1/files/actions).
// The shell stars and unstars the file itself, then raises
// `file-favorite-changed` ({uuid, isFavorite}) for the page to update its
// listing. A shell given `properties` also offers the page's properties
// panel for the file on screen (a button, Ctrl+I): it raises
// `open-properties`, which the page hosting propertiesPanelMixin() answers,
// and `viewer-file-shown` on each file, so an open panel follows the
// gallery (followViewer()).
window.fileViewerShellMixin = function fileViewerShellMixin() {
  return {
    ...viewerPanelMixin(),
    open: false,
    fileUuid: null,
    fileName: '',
    fileType: '',
    // Where the panel and the download come from: the Files endpoints,
    // unless the listing row names its own (see _rowUrls).
    downloadUrl: '',
    // Set from the merged panel's marker element (see openFile) rather
    // than the HTTP status: loadViewerPanel goes through $ajax, which
    // does not expose it.
    viewerBlocked: false,
    // An image being rotated and cropped holds the viewer (the image
    // viewer dispatches viewer-editing): an arrow key or a swipe would
    // drop the drawing, and the arrows would cover the crop handles.
    panelEditing: false,
    // Where the panel wants the download to come from instead (an edited
    // image, rendered as shown); null for the raw bytes.
    downloadOverride: null,
    // What the action registry lets the user do on the file on screen; false
    // while it answers. canShowProperties stays false without `properties`.
    canShowProperties: false,
    canFavorite: false,
    isFavorite: false,
    _propertiesEnabled: false,
    _actionsSeq: 0,
    _favoriteBusy: false,

    _onOpened() {},

    _row(uuid) {
      return document.querySelector(`[data-uuid="${uuid}"][data-node-type="file"]`);
    },

    _getViewableFiles() {
      const rows = document.querySelectorAll('[data-uuid][data-viewable="1"][data-node-type="file"]');
      return Array.from(rows)
        .filter(function(r) {
          return r.offsetParent !== null;
        })
        .map(function(r) {
          return {
            uuid: r.dataset.uuid,
            name: r.dataset.displayName,
            fileType: r.dataset.fileType,
            // A row may name the moment a video opens at, in seconds.
            at: r.dataset.at ? Number(r.dataset.at) : null,
          };
        });
    },

    navigateNext() {
      if (this.panelEditing) return;
      const files = this._getViewableFiles();
      const idx = files.findIndex(f => f.uuid === this.fileUuid);
      if (idx >= 0 && idx < files.length - 1) {
        const next = files[idx + 1];
        this.openFile(next.uuid, next.name, next.fileType, true, next.at);
      }
    },

    navigatePrev() {
      if (this.panelEditing) return;
      const files = this._getViewableFiles();
      const idx = files.findIndex(f => f.uuid === this.fileUuid);
      if (idx > 0) {
        const prev = files[idx - 1];
        this.openFile(prev.uuid, prev.name, prev.fileType, true, prev.at);
      }
    },

    _currentIndex() {
      // Read the reactive uuid before querying the DOM so these bindings
      // re-evaluate when the viewer opens, even if the file list was
      // empty (or not yet swapped in) when Alpine initialized.
      const current = this.fileUuid;
      const files = this._getViewableFiles();
      return files.findIndex(f => f.uuid === current);
    },

    _totalViewable() {
      void this.fileUuid;
      return this._getViewableFiles().length;
    },

    // A listing whose rows are not Files' to serve (an album shared
    // with the viewer) names the panel and the download on the row:
    // data-viewer-url, and data-download-url, left empty when the file
    // may not be downloaded.
    _rowUrls(uuid) {
      const row = this._row(uuid);
      const data = row ? row.dataset : {};
      return {
        viewer: data.viewerUrl || `/files/view/${uuid}`,
        download: 'downloadUrl' in data ? data.downloadUrl : `/api/v1/files/${uuid}/content`,
      };
    },

    // `at`, in seconds, starts a video there rather than at its beginning;
    // `edit` opens an image straight into rotate and crop.
    async openFile(uuid, name, fileType, updateUrl = true, at = null, edit = false) {
      const urls = this._rowUrls(uuid);
      this.fileUuid = uuid;
      this.fileName = name || 'File';
      this.fileType = fileType || '';
      this.downloadUrl = urls.download;
      this.viewerBlocked = false;
      this.panelEditing = false;
      this.downloadOverride = null;
      const wasOpen = this.open;
      this.open = true;
      this._present();

      // Opening adds an entry, so back closes the viewer; stepping
      // through the gallery only rewrites it.
      if (updateUrl) {
        const url = new URL(window.location);
        url.searchParams.set('open', uuid);
        if (wasOpen) window.navHistory.replace(url);
        else window.navHistory.push(url);
      }
      this._onOpened(uuid);
      this._loadActions(uuid);

      const viewerUrl = edit ?
        urls.viewer + (urls.viewer.includes('?') ? '&' : '?') + 'edit=1' :
        urls.viewer;
      // Not merged: a later open or a close took the panel over, and what is
      // in it now is not this file's.
      if (!await this.loadViewerPanel(viewerUrl)) return;
      const panel = document.getElementById(this._viewerPanelId());
      this.viewerBlocked = !!(panel && panel.querySelector('[data-viewer-blocked]'));
      const video = at != null && panel ? panel.querySelector('video') : null;
      // Before the metadata has loaded, this sets where playback starts.
      if (video) video.currentTime = at;
    },

    // Every close path funnels here once the shell is hidden. Cancels any
    // in-flight load so a late response cannot mount into the closed
    // viewer, then disposes and empties the panel.
    _onClosed() {
      this.open = false;
      this.teardownViewerPanel();
      this.fileUuid = null;
      this.fileName = '';
      this.fileType = '';
      this.downloadUrl = '';
      this.viewerBlocked = false;
      this.panelEditing = false;
      this.downloadOverride = null;
      this.viewerError = null;
      this.canShowProperties = false;
      this.canFavorite = false;
      this.isFavorite = false;
    },

    async _loadActions(uuid) {
      this.canShowProperties = false;
      this.canFavorite = false;
      this.isFavorite = false;
      const seq = ++this._actionsSeq;
      let actions = null;
      try {
        actions = await window.fileActions.fetchActions([uuid]);
      } catch (_) {}
      if (seq !== this._actionsSeq || !this.open || this.fileUuid !== uuid) return;
      const available = (actions && actions[uuid]) || [];
      const favorite = available.find(a => a.id === 'toggle_favorite');
      this.canFavorite = !!favorite;
      this.isFavorite = !!(favorite && favorite.state && favorite.state.is_favorite);
      if (!this._propertiesEnabled) return;
      this.canShowProperties = available.some(a => a.id === 'properties');
      window.dispatchEvent(new CustomEvent('viewer-file-shown', {
        detail: { uuid, properties: this.canShowProperties },
      }));
    },

    async toggleFavorite() {
      if (!this.canFavorite || this._favoriteBusy || !this.fileUuid) return;
      const uuid = this.fileUuid;
      const isFavorite = !this.isFavorite;
      this._favoriteBusy = true;
      try {
        const response = await fetch(`/api/v1/files/${uuid}/favorite`, {
          method: isFavorite ? 'POST' : 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!response.ok) throw new Error();
        if (this.fileUuid === uuid) this.isFavorite = isFavorite;
        window.dispatchEvent(new CustomEvent('file-favorite-changed', {
          detail: { uuid, isFavorite },
        }));
      } catch (_) {
        window.AppAlert.error('Failed to update favorites');
      } finally {
        this._favoriteBusy = false;
      }
    },

    // Asking again for the file the panel shows closes it.
    toggleProperties() {
      if (!this.canShowProperties || !this.fileUuid) return;
      window.dispatchEvent(new CustomEvent('open-properties', {
        detail: { uuid: this.fileUuid, nodeType: 'file' },
      }));
    },

    close() {
      this._dismiss();

      const url = new URL(window.location);
      if (!url.searchParams.has('open')) return;
      url.searchParams.delete('open');
      window.navHistory.returnTo(url);
    },

    handleKeydown(event) {
      if (!this.open || this._keysBlocked()) return;

      if (event.key === 'Escape') {
        this.close();
      }

      if ((event.ctrlKey || event.metaKey) && event.key === 'i' && this.canShowProperties) {
        event.preventDefault();
        this.toggleProperties();
      }

      // Arrow navigation - skip if focus is in an editable element
      if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && !event.altKey) {
        const tag = document.activeElement && document.activeElement.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (document.activeElement && document.activeElement.isContentEditable) return;

        event.preventDefault();
        if (event.key === 'ArrowLeft') {
          this.navigatePrev();
        } else {
          this.navigateNext();
        }
      }
    },

    async _openFileFromUuid(fileUuid) {
      if (!isValidUuid(fileUuid)) return;
      // A file only shared with the user is out of reach of the metadata
      // endpoint (it answers 404 below edit rights), so the listing row -
      // the "Shared with me" view renders one - is read first.
      const row = this._row(fileUuid);
      if (row) {
        this.openFile(fileUuid, row.dataset.displayName, row.dataset.fileType, false);
        return;
      }
      try {
        const response = await fetch('/api/v1/files/' + fileUuid);
        if (response.ok) {
          const fileData = await response.json();
          this.openFile(fileData.uuid, fileData.name, fileData.type, false);
        }
      } catch (err) {
        console.error('Failed to open file from URL:', err);
      }
    },

    _on(target, type, handler) {
      target.addEventListener(type, handler);
      this._listeners.push(() => target.removeEventListener(type, handler));
    },

    // Called by the shell's init(). `swipeEl` is the element a horizontal
    // swipe steps through the gallery on.
    async _listen(swipeEl) {
      // Prevent multiple initializations
      if (this._initialized) {
        return;
      }
      this._initialized = true;
      this._listeners = [];

      this._on(window, 'keydown', (e) => this.handleKeydown(e));
      this._on(window, 'open-file-viewer', (e) => {
        const {
          uuid,
          name,
          type,
          at,
          edit
        } = e.detail;
        this.openFile(uuid, name, type, true, at, !!edit);
      });

      const reloadCurrent = (e) => {
        if (this.open && this.fileUuid && e.detail && e.detail.file_uuid === this.fileUuid) {
          this.openFile(this.fileUuid, this.fileName, this.fileType, false);
        }
      };
      this._on(window, 'sse:files.file_updated', reloadCurrent);
      this._on(window, 'sse:files.lock_released', reloadCurrent);

      this._listeners.push(window.attachSwipeNavigation(swipeEl, {
        onPrev: () => this.navigatePrev(),
        onNext: () => this.navigateNext(),
      }));

      // Back or forward: the viewer follows ?open.
      this._on(window, 'nav:restore', async () => {
        const urlParams = new URLSearchParams(window.location.search);
        const fileUuid = urlParams.get('open');

        if (fileUuid && fileUuid !== this.fileUuid) {
          await this._openFileFromUuid(fileUuid);
        } else if (!fileUuid && this.open) {
          this._dismiss();
        }
      });

      // Check if there's an open parameter in URL and open it
      const urlParams = new URLSearchParams(window.location.search);
      const fileUuid = urlParams.get('open');
      if (fileUuid) {
        await this._openFileFromUuid(fileUuid);
      }
    },

    destroy() {
      (this._listeners || []).forEach((off) => off && off());
      this._listeners = [];
    },
  };
};

window.fileViewerModal = function fileViewerModal() {
  const VIEWER_PREFS_URL = '/api/v1/settings/files/viewer';
  let _prefsSaveTimer = null;

  return {
    ...fileViewerShellMixin(),
    expanded: false,

    // Gallery navigation re-opens over an already-open dialog: the
    // current spec makes that a no-op, but older engines throw.
    _present() {
      if (!this.$refs.frame.open) this.$refs.frame.showModal();
    },

    // Every close path (this one, Escape, the backdrop form) ends in the
    // dialog's close event, bound to _onClosed().
    _dismiss() {
      this.$refs.frame.close();
    },

    _keysBlocked() {
      return false;
    },

    toggleExpanded() {
      this.expanded = !this.expanded;
      this._saveViewerPrefs();
    },

    _saveViewerPrefs() {
      const prefs = {
        expanded: this.expanded
      };
      clearTimeout(_prefsSaveTimer);
      _prefsSaveTimer = setTimeout(function() {
        fetch(VIEWER_PREFS_URL, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken()
          },
          body: JSON.stringify({
            value: prefs
          }),
        }).catch(function() {});
      }, 500);
    },

    init() {
      // Initial viewer prefs are embedded server-side via |json_script
      // (see files/ui/index.html). Reading from the DOM avoids a redundant
      // fetch and the 404 a fresh user would hit on the settings endpoint.
      const prefsEl = document.getElementById('viewer-prefs-data');
      if (prefsEl) {
        try {
          const initial = JSON.parse(prefsEl.textContent) || {};
          if (initial.expanded !== undefined) this.expanded = initial.expanded;
        } catch (_) {}
      }
      return this._listen(this.$refs.frame);
    },
  };
};

// The viewer in place of the listing. It covers the element `covers` names,
// marked data-viewer-covered while it does, but left mounted: the scroll
// position and the pages loaded so far are still there on close, and the
// gallery, which pages through the rows in the DOM, still sees them.
//
// An element inside it marked [data-viewer-keep] (a breadcrumb) stays visible
// and live: the viewer starts below it. Everything else covered goes inert,
// so focus and the listing's shortcuts stay off the rows behind, and
// transparent, so what the room left under the kept element would show of
// it (a title) does not peek out. Opacity, not display: none, which would
// drop the layout the gallery and scrollIntoView rely on, nor visibility,
// which children inherit and a transition-all card animates back: the
// focus handed to it on close would land while it is still hidden.
//
// Back on the listing, the row's `[data-viewer-focus]` element, if it has
// one, takes the focus: the row's first control may be a checkbox, and an
// input holding the focus mutes the listing's shortcuts.
window.fileViewerInline = function fileViewerInline(options) {
  const coversId = options && options.covers;
  // Kept off the component: Alpine's reactive proxy breaks DOM methods.
  let inerted = [];
  let keptObserver = null;

  return {
    ...fileViewerShellMixin(),
    _propertiesEnabled: !!(options && options.properties),
    // How far down the kept element pushes the viewer, in px.
    keptHeight: 0,

    _covered() {
      return coversId ? document.getElementById(coversId) : null;
    },

    _cover() {
      const covered = this._covered();
      if (!covered) return;
      covered.setAttribute('data-viewer-covered', '');
      const kept = covered.querySelector('[data-viewer-keep]');
      if (kept) {
        for (let node = kept; node !== covered; node = node.parentElement) {
          for (const sibling of node.parentElement.children) {
            if (sibling !== node && !sibling.inert) inerted.push(sibling);
          }
        }
      } else if (!covered.inert) {
        inerted.push(covered);
      }
      inerted.forEach((el) => {
        el.inert = true;
        el.style.opacity = '0';
      });
      this._watchKept(kept, covered);
    },

    _uncover() {
      const covered = this._covered();
      if (covered) covered.removeAttribute('data-viewer-covered');
      inerted.forEach((el) => {
        el.inert = false;
        el.style.removeProperty('opacity');
      });
      inerted = [];
      this._watchKept(null);
    },

    // Measured from the covered element's top: what sits above the kept
    // element (a header's padding) stays uncovered with it, and the same
    // room is left below it, its container's bottom padding. Both are
    // observed, since a breakpoint can change that padding alone.
    _watchKept(kept, covered) {
      if (keptObserver) keptObserver.disconnect();
      keptObserver = null;
      const measure = () => {
        if (!kept) {
          this.keptHeight = 0;
          return;
        }
        const room = parseFloat(getComputedStyle(kept.parentElement).paddingBottom) || 0;
        this.keptHeight = kept.getBoundingClientRect().bottom + room - covered.getBoundingClientRect().top;
      };
      measure();
      if (!kept) return;
      keptObserver = new ResizeObserver(measure);
      keptObserver.observe(kept);
      keptObserver.observe(covered);
    },

    // Focus moves in on opening only, not on each gallery step. It goes to
    // the frame rather than a button: a button focused from script after a
    // click on a row would draw its focus ring.
    _present() {
      const covered = this._covered();
      if (covered && covered.hasAttribute('data-viewer-covered')) return;
      this._cover();
      this.$nextTick(() => this.$refs.frame.focus({ preventScroll: true }));
    },

    _dismiss() {
      this._uncover();
      const row = this.fileUuid ? this._row(this.fileUuid) : null;
      this._onClosed();
      const target = row && row.querySelector('[data-viewer-focus]');
      if (target) target.focus({ preventScroll: true });
    },

    // The listing is live around the viewer, so a dialog it opened (a
    // rename from the properties panel) takes the keys first.
    _keysBlocked() {
      return !!document.querySelector('dialog[open]');
    },

    // Kept in view behind the viewer: closing lands on it, and the
    // listing's infinite scroll loads what the gallery steps into next.
    _onOpened(uuid) {
      const row = this._row(uuid);
      if (row) row.scrollIntoView({ block: 'nearest' });
    },

    init() {
      // The page around stays clickable: a navigation that drops ?open (a
      // sidebar link, the kept breadcrumb) takes the viewer away with the
      // content it covered, writing no history of its own.
      if (this._initialized) return;
      const ready = this._listen(this.$refs.frame);
      this._on(window, 'nav:change', () => {
        if (this.open && !new URLSearchParams(window.location.search).has('open')) {
          this._dismiss();
        }
      });
      // A refresh swapped the covered listing under the open viewer: the new
      // markup comes in live, and its header is a new element to measure.
      this._on(document, 'ajax:merged', (e) => {
        const covered = this._covered();
        if (!this.open || !covered || !covered.contains(e.target)) return;
        this._uncover();
        this._cover();
      });
      return ready;
    },
  };
};
