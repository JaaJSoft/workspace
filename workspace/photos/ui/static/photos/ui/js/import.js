// Importing photos and videos from the Photos page: the Import button, a drop
// anywhere on the listing, and the import folder preference. The bytes go
// through the Files upload queue (files/ui/js/upload_queue.js), into the
// folder GET/POST/PUT /api/v1/photos/import-folder answers for.

const PHOTOS_IMPORT_API = '/api/v1/photos/import-folder';
const PHOTOS_IMPORT_BY_DATE_API = '/api/v1/photos/import-folder/by-date';
// The most uuids the by-date endpoint takes in one report.
const PHOTOS_IMPORT_BY_DATE_BATCH = 500;
// Browsers leave `type` empty for the formats they cannot decode themselves.
const PHOTOS_UNTYPED_MEDIA = /\.(heic|heif|avif|jxl)$/i;
const PHOTOS_ANALYSIS_POLL_MS = 4000;
const PHOTOS_ANALYSIS_MAX_POLLS = 75;

function photosImportable(file) {
  if (file.type === 'image/svg+xml') return false;
  if (/^(image|video)\//.test(file.type)) return true;
  return !file.type && PHOTOS_UNTYPED_MEDIA.test(file.name);
}

function photosImportFolderData() {
  const el = document.getElementById('photos-import-folder-data');
  try {
    return (el && JSON.parse(el.textContent)) || null;
  } catch (_) {
    return null;
  }
}

window.photosImportMixin = function photosImportMixin() {
  return {
    importFolder: photosImportFolderData(),
    importDropActive: false,
    _importDragDepth: 0,
    _analysisTimer: null,
    // Upload queue rows this page imported and has not reported for sorting
    // by date yet (see _sortImportsByDate).
    _importedRows: new Set(),

    initImport() {
      window.addEventListener('uploads-changed', () => {
        this._sortImportsByDate();
        this._showImported();
      });
    },

    importFolderLabel() {
      const folder = this.importFolder;
      if (!folder) return '';
      return folder.group ? `${folder.path} (${folder.group})` : folder.path;
    },

    // Personal folders only: the WebDAV root is the user's own files.
    importDavUrl() {
      const folder = this.importFolder;
      if (!folder || folder.group) return '';
      const path = folder.path.split('/').map(encodeURIComponent).join('/');
      return `${window.location.origin}/dav/${path}/`;
    },

    pickImportFiles() {
      document.getElementById('photos-import-input').click();
    },

    onImportInput(event) {
      const files = Array.from(event.target.files);
      // Cleared at once so picking the same files again still fires change.
      event.target.value = '';
      this.importFiles(files);
    },

    async importFiles(files) {
      const media = files.filter(photosImportable);
      const rejected = files.length - media.length;
      if (rejected) {
        window.AppAlert.warning(
          `${rejected} file${rejected > 1 ? 's' : ''} skipped: only photos and videos can be imported here`
        );
      }
      if (!media.length) return;
      // Resolved per batch: the default folder is only created once something goes into it.
      let folder;
      try {
        const response = await fetch(PHOTOS_IMPORT_API, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!response.ok) throw new Error();
        folder = await response.json();
      } catch (_) {
        window.AppAlert.error('Could not open the import folder');
        return;
      }
      this.importFolder = folder;
      const uploads = Alpine.store('uploads');
      const firstRow = uploads.items.length;
      // Cameras reuse names (IMG_0001.JPG): a clash keeps both, never replaces.
      uploads.add(
        media.map((file) => ({ file, folderId: folder.uuid, onConflict: 'rename' }))
      );
      for (const row of uploads.items.slice(firstRow)) this._importedRows.add(row.id);
    },

    // Reports what this page imported and the queue has finished, for the
    // server to file into year and month folders. Only these rows: the same
    // queue also carries what other pages dropped into other folders.
    _sortImportsByDate() {
      const rows = Alpine.store('uploads').items.filter(
        // A failed row can still be retried: it waits for its next outcome.
        (row) => this._importedRows.has(row.id) && !['queued', 'uploading', 'failed'].includes(row.status)
      );
      for (const row of rows) this._importedRows.delete(row.id);
      if (!this.photoPrefs.import_by_date) return;
      const uuids = rows.filter((row) => row.status === 'done' && row.uuid).map((row) => row.uuid);
      for (let i = 0; i < uuids.length; i += PHOTOS_IMPORT_BY_DATE_BATCH) {
        fetch(PHOTOS_IMPORT_BY_DATE_API, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ files: uuids.slice(i, i + PHOTOS_IMPORT_BY_DATE_BATCH) }),
        }).catch(() => {
          // They stay in the import folder, where they already show up.
        });
      }
    },

    async chooseImportFolder() {
      const picked = await AppDialog.folderPicker({
        title: 'Import folder',
        message: 'Choose where photos and videos imported from Photos are saved.',
        okLabel: 'Select',
        okClass: 'btn-module',
        icon: 'folder-input',
        iconClass: 'bg-module/15 text-module',
      });
      if (!picked) return;
      if (!picked.uuid) {
        window.AppAlert.warning('Choose a folder inside the space, not the space itself');
        return;
      }
      try {
        const response = await fetch(PHOTOS_IMPORT_API, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ folder: picked.uuid }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
          window.AppAlert.error(body.folder || 'Could not save the import folder');
          return;
        }
        this.importFolder = body;
      } catch (_) {
        window.AppAlert.error('Could not save the import folder');
      }
    },

    // ── Drop to import ──────────────────────────────────
    // Only a drag carrying files from outside the page: Chrome lists an
    // <img> dragged within the page under 'Files' too, so an album reorder
    // (this._drag, photos.js) is told apart by its own state.

    _isImportDrag(e) {
      return !this._drag && e.dataTransfer.types.includes('Files');
    },

    onImportDragEnter(e) {
      if (!this._isImportDrag(e)) return;
      e.preventDefault();
      this._importDragDepth++;
      this.importDropActive = true;
    },

    onImportDragOver(e) {
      if (!this._isImportDrag(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    },

    onImportDragLeave(e) {
      if (!this._isImportDrag(e)) return;
      this._importDragDepth--;
      if (this._importDragDepth <= 0) {
        this._importDragDepth = 0;
        this.importDropActive = false;
      }
    },

    onImportDrop(e) {
      if (!this._isImportDrag(e)) return;
      e.preventDefault();
      this._importDragDepth = 0;
      this.importDropActive = false;
      this.importFiles(Array.from(e.dataTransfer.files));
    },

    // ── After an import ─────────────────────────────────
    // A new file joins the timeline once the worker has read its capture
    // date. Until then the header counts it as being analyzed: follow that
    // count, and show the listing again when it drops to zero.

    async _showImported() {
      clearTimeout(this._analysisTimer);
      await this._refresh(['photos-nav', 'photos-content']);
      const href = window.location.href;
      let polls = 0;
      const tick = async () => {
        if (window.location.href !== href) return;
        if (!document.querySelector('[data-photos-pending]')) {
          this._refresh(['photos-nav', 'photos-content']);
          return;
        }
        if (++polls > PHOTOS_ANALYSIS_MAX_POLLS) return;
        await this._refresh(['photos-header']);
        this._analysisTimer = setTimeout(tick, PHOTOS_ANALYSIS_POLL_MS);
      };
      if (document.querySelector('[data-photos-pending]')) {
        this._analysisTimer = setTimeout(tick, PHOTOS_ANALYSIS_POLL_MS);
      }
    },
  };
};
