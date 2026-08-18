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
      // ?storage=root|<uuid>[&storage_category=x] survives a reload: reopen
      // the dialog on the same scope the user was looking at.
      const params = new URLSearchParams(window.location.search);
      const scope = params.get('storage');
      if (scope) {
        const uuid = scope === 'root' ? null : scope;
        const category = params.get('storage_category');
        this.$nextTick(() => {
          this.scopeName = '';
          this.currentUrl = uuid ? `/files/storage/${uuid}` : '/files/storage';
          const dlg = this.$refs.dialog;
          if (dlg && !dlg.open) dlg.showModal();
          this.load(category ? `${this.currentUrl}?category=${encodeURIComponent(category)}` : this.currentUrl);
        });
      }
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
      if (!swapped) return;
      if (swapped.dataset.name) this.scopeName = swapped.dataset.name;
      if (swapped.dataset.url) this.syncUrl(swapped.dataset.url);
    },

    // Mirror the analysed scope into the page URL (replaceState, so the
    // folder browser's own history stack is left alone).
    syncUrl(analysisUrl) {
      const source = new URL(analysisUrl, window.location.origin);
      const match = source.pathname.match(/^\/files\/storage(?:\/([0-9a-f-]{36}))?$/);
      if (!match) return;
      const url = new URL(window.location.href);
      url.searchParams.set('storage', match[1] || 'root');
      const category = source.searchParams.get('category');
      if (category) url.searchParams.set('storage_category', category);
      else url.searchParams.delete('storage_category');
      window.history.replaceState(window.history.state, '', url.toString());
    },

    clearUrl() {
      const url = new URL(window.location.href);
      if (!url.searchParams.has('storage')) return;
      url.searchParams.delete('storage');
      url.searchParams.delete('storage_category');
      window.history.replaceState(window.history.state, '', url.toString());
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
      this.clearUrl();
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
