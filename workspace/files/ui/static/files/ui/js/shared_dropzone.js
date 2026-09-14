window.sharedLinkUrl = function sharedLinkUrl(token, suffix, params) {
  const query = new URLSearchParams(params || {});
  const tail = query.toString();
  return `/api/v1/files/shared/${encodeURIComponent(token)}${suffix}${tail ? '?' + tail : ''}`;
};

window.sharedDrop = function sharedDrop(token, accessToken, maxFileBytes, rootName) {
  return {
    token,
    accessToken: accessToken || '',
    maxFileBytes: Number(maxFileBytes) || 0,
    rootName: rootName || '',
    targetNode: '',
    targetName: '',
    queue: [],
    sending: false,

    init() {
      this.syncTarget();
    },

    // The browsed folder, published by #shared-content on every swap. The
    // zone sits outside that region, so it reads the attributes rather than
    // being re-rendered with them.
    syncTarget() {
      const content = document.getElementById('shared-content');
      this.targetNode = (content && content.dataset.node) || '';
      this.targetName = (content && content.dataset.nodeName) || this.rootName;
    },

    // The target is fixed when the file enters the queue: uploads run one
    // at a time and the zone survives navigation, so a later click into
    // another folder must not redirect what was dropped into this one.
    queued(file) {
      return { file, name: file.name, state: 'pending', percent: 0, node: this.targetNode };
    },

    pick(event) {
      const picked = Array.from(event.target.files || []);
      picked.forEach(file => this.queue.push(this.queued(file)));
      event.target.value = '';
      this.sendAll();
    },

    drop(event) {
      const dropped = Array.from(event.dataTransfer.files || []);
      dropped.forEach(file => this.queue.push(this.queued(file)));
      this.sendAll();
    },

    tooLarge(item) {
      return this.maxFileBytes > 0 && item.file.size > this.maxFileBytes;
    },

    async sendAll() {
      if (this.sending) return;
      this.sending = true;
      const doneBefore = this.doneCount();
      for (const item of this.queue) {
        if (item.state !== 'pending') continue;
        await this.send(item);
      }
      this.sending = false;
      // The listing above lives in the #shared-content swap region, which
      // this zone sits outside of: nothing else re-fetches it.
      if (this.doneCount() > doneBefore) window.folderNav.reload();
    },

    async send(item) {
      if (this.tooLarge(item)) {
        item.state = 'too-large';
        return;
      }
      item.state = 'sending';
      item.percent = 0;
      const body = new FormData();
      body.append('file', item.file);
      if (item.node) body.append('node', item.node);
      try {
        // 204 is the only success, and it carries no body on purpose: the
        // response must not reveal whether the name collided.
        const status = await this.post(body, (percent) => { item.percent = percent; });
        item.state = status === 204 ? 'done' : 'failed';
      } catch (e) {
        item.state = 'failed';
      }
    },

    // XMLHttpRequest rather than fetch: it is the only way to see the bytes
    // leave, which is what the per-file bar shows.
    post(body, onProgress) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onload = () => resolve(xhr.status);
        xhr.onerror = () => reject(new Error('Network error'));
        xhr.open('POST', window.sharedLinkUrl(this.token, '/upload', {}));
        if (this.accessToken) xhr.setRequestHeader('X-Share-Access', this.accessToken);
        xhr.send(body);
      });
    },

    sendingCount() {
      return this.queue.filter(item => item.state === 'pending' || item.state === 'sending').length;
    },

    doneCount() {
      return this.queue.filter(item => item.state === 'done').length;
    },

    stateLabel(state) {
      const labels = {
        pending: 'Waiting',
        sending: 'Sending',
        'too-large': 'Too large',
        failed: 'Failed',
        done: 'Sent',
      };
      return labels[state] || state;
    },
  };
};
