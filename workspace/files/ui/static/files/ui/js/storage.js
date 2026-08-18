window.storageAnalysis = function storageAnalysis() {
  return {
    scopeName: '',
    currentUrl: null,
    loading: false,
    busy: false,

    init() {
      window.addEventListener('open-storage-analysis', (e) => {
        const detail = e.detail || {};
        this.open(detail.uuid || null, detail.name || 'All files');
      });
    },

    open(uuid, name) {
      this.scopeName = name;
      this.currentUrl = uuid ? `/files/storage/${uuid}` : '/files/storage';
      const dlg = this.$refs.dialog;
      if (dlg && !dlg.open) dlg.showModal();
      this.load(this.currentUrl);
    },

    load(url) {
      this.currentUrl = url;
      this.$ajax(url, { target: 'storage-analysis' });
    },

    syncScope() {
      const swapped = document.getElementById('storage-analysis');
      if (swapped && swapped.dataset.name) this.scopeName = swapped.dataset.name;
    },

    reload() {
      const swapped = document.getElementById('storage-analysis');
      // Refreshing after a mutation keeps the scope the user is looking at,
      // which may be a drilled-into sub-folder rather than the opening one.
      const active = swapped && swapped.dataset.url;
      this.load(active || this.currentUrl);
    },

    close() {
      const dlg = this.$refs.dialog;
      if (dlg && dlg.open) dlg.close();
    },

    onClose() {
      const target = document.getElementById('storage-analysis');
      if (target) target.innerHTML = '';
    },

    async trashFile(uuid, name) {
      const confirmed = await AppDialog.confirm({
        title: 'Move to trash?',
        message: `"${name}" will be moved to the trash.`,
        okLabel: 'Move to trash',
        okClass: 'btn-error',
      });
      if (!confirmed) return;
      this.busy = true;
      try {
        const resp = await fetch(`/api/v1/files/${uuid}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!resp.ok) throw new Error('delete failed');
        this.afterMutation();
      } catch (err) {
        AppAlert.error('Failed to move the file to trash');
      } finally {
        this.busy = false;
      }
    },

    async emptyTrash() {
      const confirmed = await AppDialog.confirm({
        title: 'Empty trash?',
        message: 'This will permanently delete all items in trash and cannot be undone.',
        okLabel: 'Empty trash',
        okClass: 'btn-error',
      });
      if (!confirmed) return;
      this.busy = true;
      try {
        const resp = await fetch('/api/v1/files/trash/clean?force=1', {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!resp.ok) throw new Error('clean failed');
        this.afterMutation();
      } catch (err) {
        AppAlert.error('Failed to empty trash');
      } finally {
        this.busy = false;
      }
    },

    afterMutation() {
      this.reload();
      window.dispatchEvent(new CustomEvent('pinned-folders-changed'));
      const browser = document.getElementById('folder-browser');
      if (browser) {
        this.$ajax(window.location.pathname + window.location.search, { target: 'folder-browser' });
      }
    },
  };
};
