// Hiding photos, videos and folders from the library: the Hide and Unhide
// rows of the menus and the selection bar, and the hidden folders of the
// Preferences panel. POST /api/v1/photos/hidden takes files and folders
// alike; the Hidden view (?hidden=1) is the only listing showing them.

const PHOTOS_HIDDEN_API = '/api/v1/photos/hidden';

function photosHiddenFoldersData() {
  const el = document.getElementById('photos-hidden-folders-data');
  try {
    const data = el ? JSON.parse(el.textContent) : null;
    return Array.isArray(data) ? data : [];
  } catch (_) {
    return [];
  }
}

// Whether the listing on screen is the Hidden view. Read from the page each
// time: a navigation swaps it.
function photosInHiddenView() {
  return !!document.getElementById('photos-hidden-view-data');
}

function photosHiddenFolderLabel(folder) {
  return folder.group ? `${folder.path} (${folder.group})` : folder.path;
}

window.photosHiddenMixin = function photosHiddenMixin() {
  return {
    hiddenFolders: photosHiddenFoldersData(),
    hiddenBusy: false,
    // Whether the listing on screen is the Hidden view, kept in step with
    // the page by syncHiddenView so the menus can bind on it.
    hiddenView: false,

    syncHiddenView() {
      this.hiddenView = photosInHiddenView();
    },

    hiddenFolderLabel(folder) {
      return photosHiddenFolderLabel(folder);
    },

    // Hides the photos, or on the Hidden view brings them back: either way
    // they leave the listing on screen.
    async setPhotosHidden(uuids, hide) {
      if (!uuids || !uuids.length || this.hiddenBusy) return;
      const files = uuids.slice();
      this.closeSelectionMenu();
      this.hiddenBusy = true;
      try {
        await photosJson('POST', hide ? PHOTOS_HIDDEN_API : `${PHOTOS_HIDDEN_API}/remove`, { files });
      } catch (_) {
        window.AppAlert.error(hide ? 'Failed to hide' : 'Failed to unhide');
        return;
      } finally {
        this.hiddenBusy = false;
      }
      if (files.includes(this.propertiesUuid)) this.closePropertiesPanel();
      this._removeTiles(files);
      this.clearSelection();
      const count = photosCountLabel(files.length);
      window.AppAlert.success(
        hide ? `Hid ${count} from your library` : `${count} back in your library`,
        { duration: 2500 },
      );
      // Emptied: the whole listing, for its empty state.
      const emptied = !document.querySelector('#timeline-grid [data-uuid]');
      this._refresh(['photos-nav', emptied ? 'photos-content' : 'photos-header']);
    },

    async hideFolder() {
      const picked = await AppDialog.folderPicker({
        title: 'Hide a folder',
        message: 'Its photos and videos, subfolders included, leave your library. They stay in Files.',
        okLabel: 'Hide',
        okClass: 'btn-module',
        icon: 'eye-off',
        iconClass: 'bg-module/15 text-module',
      });
      if (!picked) return;
      if (!picked.uuid) {
        window.AppAlert.warning('Choose a folder inside My Files, not My Files itself');
        return;
      }
      try {
        await photosJson('POST', PHOTOS_HIDDEN_API, { files: [picked.uuid] });
      } catch (_) {
        window.AppAlert.error('Could not hide the folder');
        return;
      }
      await this._reloadHiddenFolders();
      this._refresh(['photos-nav', 'photos-content']);
    },

    async unhideFolder(folder) {
      try {
        await photosJson('POST', `${PHOTOS_HIDDEN_API}/remove`, { files: [folder.uuid] });
      } catch (_) {
        window.AppAlert.error('Could not show the folder again');
        return;
      }
      this.hiddenFolders = this.hiddenFolders.filter((f) => f.uuid !== folder.uuid);
      this._refresh(['photos-nav', 'photos-content']);
    },

    async _reloadHiddenFolders() {
      try {
        this.hiddenFolders = await photosJson('GET', `${PHOTOS_HIDDEN_API}/folders`);
      } catch (_) {
        // The list keeps what it showed; the next page load corrects it.
      }
    },
  };
};
