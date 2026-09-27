/**
 * Upload queue of the Files page, published as the `uploads` Alpine store
 * and drawn by files/ui/partials/upload_panel.html.
 *
 *   Alpine.store('uploads').add([{ file, folderId, onConflict }, ...]);
 *
 * Each entry keeps the folder it was dropped into, so navigating while the
 * queue drains never redirects the rest of it. Adding files while others go
 * up appends to the same queue.
 *
 * Files go up one at a time: the server resolves a name collision against
 * what the folder already holds, so two uploads racing for the same free
 * name could both take it.
 *
 * `uploads-changed` is dispatched on window once the queue drains with new
 * files on the server, and after a discarded duplicate.
 */
window.createUploadQueue = function createUploadQueue({ send, discard, notify, confirm }) {
  // Kept out of the reactive store: Alpine would proxy them, and an XHR
  // method called through a proxy throws "Illegal invocation".
  const files = new Map();
  const inFlight = new Map();
  let nextId = 1;
  let changedSinceIdle = false;

  const PENDING = ['queued', 'uploading'];

  return {
    items: [],
    paused: false,
    expanded: true,

    // What the panel lists: rows that need the user first, then the rest in
    // the order they were added.
    get rows() {
      const needsUser = (item) => item.status === 'failed' || item.duplicates.length > 0;
      return [...this.items.filter(needsUser), ...this.items.filter((item) => !needsUser(item))];
    },

    get pendingCount() {
      return this.items.filter((item) => PENDING.includes(item.status)).length;
    },

    get failedCount() {
      return this.items.filter((item) => item.status === 'failed').length;
    },

    // Bytes of everything that is, or will be, on the server.
    get progress() {
      let loaded = 0;
      let total = 0;
      for (const item of this.items) {
        if (!PENDING.includes(item.status) && item.status !== 'done') continue;
        total += item.size;
        loaded += item.status === 'done' ? item.size : item.loaded;
      }
      return { loaded, total, percent: total ? Math.floor((loaded / total) * 100) : 100 };
    },

    get title() {
      const pending = this.pendingCount;
      const plural = (n) => `${n} file${n > 1 ? 's' : ''}`;
      if (pending && this.paused) {
        return this.items.some((item) => item.status === 'uploading')
          ? 'Pausing after the current file'
          : `Paused - ${plural(pending)} left`;
      }
      if (pending) return `Uploading ${plural(pending)}`;
      const count = (status) => this.items.filter((item) => item.status === status).length;
      const parts = [];
      const uploaded = count('done') + count('discarded');
      if (uploaded) parts.push(`${uploaded} uploaded`);
      if (count('failed')) parts.push(`${count('failed')} failed`);
      if (count('skipped')) parts.push(`${count('skipped')} skipped`);
      if (count('cancelled')) parts.push(`${count('cancelled')} cancelled`);
      const text = parts.join(', ');
      return text.charAt(0).toUpperCase() + text.slice(1);
    },

    add(entries) {
      // A pause holds the files it stopped; with none left it has nothing to hold.
      if (!this.pendingCount) this.paused = false;
      for (const { file, folderId, onConflict } of entries) {
        const id = nextId++;
        const skipped = onConflict === 'skip';
        if (!skipped) files.set(id, file);
        this.items.push({
          id,
          name: file.name,
          size: file.size,
          folderId: folderId || null,
          onConflict: skipped ? null : onConflict || null,
          status: skipped ? 'skipped' : 'queued',
          loaded: 0,
          error: skipped ? 'A file with this name already exists' : '',
          outcome: '',
          savedName: '',
          uuid: null,
          duplicates: [],
        });
      }
      if (entries.length) this.expanded = true;
      this._pump();
    },

    statusText(item) {
      const size = window.formatFileSize;
      switch (item.status) {
        case 'queued': return this.paused ? 'Paused' : 'Waiting';
        case 'uploading': return `${size(item.loaded)} of ${size(item.size)}`;
        case 'done':
          if (item.duplicates.length) {
            const [first, ...rest] = item.duplicates;
            return `Same content as ${first.path}${rest.length ? ` and ${rest.length} more` : ''}`;
          }
          if (item.outcome === 'replaced') return 'Replaced the existing file';
          if (item.outcome === 'renamed') return `Saved as ${item.savedName}`;
          return size(item.size);
        case 'discarded': return 'Discarded';
        case 'cancelled': return 'Cancelled';
        default: return item.error;
      }
    },

    statusIcon(item) {
      if (item.status === 'done' && item.duplicates.length) return 'copy';
      return {
        queued: this.paused ? 'pause' : 'clock',
        done: 'circle-check',
        failed: 'circle-alert',
        skipped: 'circle-minus',
        cancelled: 'circle-x',
        discarded: 'trash-2',
      }[item.status] || 'file';
    },

    statusTone(item) {
      if (item.status === 'done') return item.duplicates.length ? 'text-warning' : 'text-success';
      return item.status === 'failed' ? 'text-error' : 'text-base-content/50';
    },

    cancel(id) {
      const item = this._find(id);
      if (!item || !PENDING.includes(item.status)) return;
      this._abort(id);
      item.status = 'cancelled';
      item.loaded = 0;
      this._pump();
    },

    cancelAll() {
      for (const item of this.items) {
        if (!PENDING.includes(item.status)) continue;
        this._abort(item.id);
        item.status = 'cancelled';
        item.loaded = 0;
      }
      this._pump();
    },

    retry(id) {
      const item = this._find(id);
      if (!item || !['failed', 'cancelled'].includes(item.status)) return;
      item.status = 'queued';
      item.error = '';
      this._pump();
    },

    // The file in flight finishes: an aborted request cannot be resumed, and
    // one aborted after the server stored it would come back as a name
    // collision (or a second copy) on resume. Cancelling it is the way to
    // stop it now.
    pause() {
      this.paused = true;
    },

    resume() {
      this.paused = false;
      this._pump();
    },

    // Closing the panel empties it, cancelling what has not gone up yet.
    async close() {
      const pending = this.pendingCount;
      if (pending) {
        const confirmed = await confirm({
          title: 'Cancel uploads?',
          message: `${pending} file${pending > 1 ? 's have' : ' has'} not finished uploading.`,
          okLabel: 'Cancel uploads',
          cancelLabel: 'Keep uploading',
          okClass: 'btn-error',
          icon: 'cloud-upload',
          iconClass: 'bg-error/10 text-error',
        });
        if (!confirmed) return;
        this.cancelAll();
      }
      this.items = [];
      files.clear();
      this.paused = false;
    },

    async discard(id) {
      const item = this._find(id);
      if (!item || item.status !== 'done' || !item.uuid) return;
      if (await discard(item.uuid, item.savedName || item.name)) {
        item.status = 'discarded';
        item.duplicates = [];
        notify('uploads-changed');
      }
    },

    keep(id) {
      const item = this._find(id);
      if (item) item.duplicates = [];
    },

    _find(id) {
      return this.items.find((item) => item.id === id);
    },

    _abort(id) {
      const request = inFlight.get(id);
      if (!request) return;
      inFlight.delete(id);
      request.abort();
    },

    _pump() {
      if (this.paused || inFlight.size) return;
      const item = this.items.find((candidate) => candidate.status === 'queued');
      if (!item) {
        if (changedSinceIdle && !this.pendingCount) {
          changedSinceIdle = false;
          notify('uploads-changed');
        }
        return;
      }
      item.status = 'uploading';
      item.loaded = 0;
      const request = send(files.get(item.id), item, (loaded) => {
        if (inFlight.get(item.id) === request) item.loaded = loaded;
      });
      inFlight.set(item.id, request);
      request.promise.then(
        ({ status, body }) => {
          if (inFlight.get(item.id) !== request) return;
          inFlight.delete(item.id);
          files.delete(item.id);
          changedSinceIdle = true;
          item.status = 'done';
          item.uuid = body.uuid || null;
          item.savedName = body.name || item.name;
          if (status === 200) item.outcome = 'replaced';
          else item.outcome = body.name && body.name !== item.name ? 'renamed' : 'created';
          // Only a row this request created can be discarded again; a
          // replaced file is the user's existing file with new content.
          if (item.outcome !== 'replaced' && Array.isArray(body.duplicates)) {
            item.duplicates = body.duplicates;
          }
          this._pump();
        },
        (error) => {
          if (inFlight.get(item.id) !== request) return;
          inFlight.delete(item.id);
          item.loaded = 0;
          if (error.nameCollision) {
            // The pre-check missed it (or the folder changed under us): the
            // server keeps the existing file, which is what "skip" means.
            files.delete(item.id);
            item.status = 'skipped';
            item.error = 'A file with this name already exists';
          } else {
            item.status = 'failed';
            item.error = error.message || 'Upload failed';
          }
          this._pump();
        },
      );
    },
  };
};

window.sendFileUpload = function sendFileUpload(file, { folderId, onConflict }, onProgress) {
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('name', file.name);
    form.append('node_type', 'file');
    form.append('content', file);
    if (folderId) form.append('parent', folderId);
    if (onConflict) form.append('on_conflict', onConflict);

    // The request total counts the multipart envelope; scale to the file.
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total) onProgress(Math.floor((e.loaded / e.total) * file.size));
    };
    xhr.onload = () => {
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ status: xhr.status, body });
        return;
      }
      const error = new Error(window.fileActions.firstErrorMessage(body));
      error.nameCollision = xhr.status === 400 && Array.isArray(body.name);
      reject(error);
    };
    xhr.onerror = () => reject(new Error('Network error'));
    xhr.onabort = () => reject(new Error('Cancelled'));

    xhr.open('POST', '/api/v1/files');
    xhr.setRequestHeader('X-CSRFToken', getCSRFToken());
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
};

// Trash then purge: the discarded copy should not linger in the trash.
window.discardFileUpload = async function discardFileUpload(uuid, name) {
  const headers = { 'X-CSRFToken': getCSRFToken() };
  try {
    const trashed = await fetch(`/api/v1/files/${uuid}`, { method: 'DELETE', headers });
    if (!trashed.ok) throw new Error();
    const purged = await fetch(`/api/v1/files/${uuid}/purge`, { method: 'DELETE', headers });
    if (!purged.ok) {
      window.AppAlert.warning(`${name} was moved to trash but could not be permanently discarded`);
    }
    return true;
  } catch (_) {
    window.AppAlert.error(`Failed to discard ${name}`);
    return false;
  }
};

document.addEventListener('alpine:init', () => {
  Alpine.store('uploads', window.createUploadQueue({
    send: window.sendFileUpload,
    discard: window.discardFileUpload,
    notify: (type) => window.dispatchEvent(new CustomEvent(type)),
    confirm: (options) => window.AppDialog.confirm(options),
  }));

  // Leaving the page aborts every request still in flight.
  window.addEventListener('beforeunload', (e) => {
    if (!Alpine.store('uploads').pendingCount) return;
    e.preventDefault();
    e.returnValue = '';
  });
});
