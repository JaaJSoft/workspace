window.sharedLinkUrl = function sharedLinkUrl(token, suffix, params) {
  const query = new URLSearchParams(params || {});
  const tail = query.toString();
  return `/api/v1/files/shared/${encodeURIComponent(token)}${suffix}${tail ? '?' + tail : ''}`;
};

window.sharedFolderBrowser = function sharedFolderBrowser(token, accessToken) {
  return {
    token,
    accessToken: accessToken || '',
    entries: [],
    breadcrumbs: [],
    folderUuid: '',
    loading: false,
    error: '',

    init() {
      this.load('');
    },

    params(extra) {
      const base = extra ? { ...extra } : {};
      if (this.accessToken) base.access_token = this.accessToken;
      return base;
    },

    async load(folderUuid) {
      this.loading = true;
      this.error = '';
      try {
        const url = window.sharedLinkUrl(
          this.token, '/entries', this.params(folderUuid ? { folder: folderUuid } : {}),
        );
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('unavailable');
        const data = await resp.json();
        this.entries = data.entries;
        this.breadcrumbs = data.breadcrumbs;
        this.folderUuid = folderUuid;
      } catch (e) {
        this.error = 'This folder could not be loaded.';
      }
      this.loading = false;
    },

    downloadUrl(entry) {
      return window.sharedLinkUrl(this.token, '/download', this.params({ file: entry.uuid }));
    },

    thumbnailUrl(entry) {
      return window.sharedLinkUrl(this.token, '/thumbnail', this.params({ file: entry.uuid }));
    },

    isFolder(entry) {
      return entry.node_type === 'folder';
    },
  };
};

window.sharedDrop = function sharedDrop(token, accessToken, maxFileBytes) {
  return {
    token,
    accessToken: accessToken || '',
    maxFileBytes: Number(maxFileBytes) || 0,
    queue: [],
    sending: false,

    pick(event) {
      const picked = Array.from(event.target.files || []);
      picked.forEach(file => this.queue.push({ file, name: file.name, state: 'pending' }));
      event.target.value = '';
      this.sendAll();
    },

    drop(event) {
      const dropped = Array.from(event.dataTransfer.files || []);
      dropped.forEach(file => this.queue.push({ file, name: file.name, state: 'pending' }));
      this.sendAll();
    },

    tooLarge(item) {
      return this.maxFileBytes > 0 && item.file.size > this.maxFileBytes;
    },

    async sendAll() {
      if (this.sending) return;
      this.sending = true;
      for (const item of this.queue) {
        if (item.state !== 'pending') continue;
        await this.send(item);
      }
      this.sending = false;
    },

    async send(item) {
      if (this.tooLarge(item)) {
        item.state = 'too-large';
        return;
      }
      item.state = 'sending';
      const body = new FormData();
      body.append('file', item.file);
      const headers = {};
      if (this.accessToken) headers['X-Share-Access'] = this.accessToken;
      try {
        const resp = await fetch(window.sharedLinkUrl(this.token, '/upload', {}), {
          method: 'POST',
          headers,
          body,
        });
        // 204 is the only success, and it carries no body on purpose: the
        // response must not reveal whether the name collided.
        item.state = resp.status === 204 ? 'done' : 'failed';
      } catch (e) {
        item.state = 'failed';
      }
    },

    doneCount() {
      return this.queue.filter(item => item.state === 'done').length;
    },
  };
};
