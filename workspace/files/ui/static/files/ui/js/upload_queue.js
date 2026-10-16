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
 * Up to MAX_IN_FLIGHT files go up at once, which hides the round trip and
 * the server's fixed cost per file behind the other transfers. Two of them
 * racing for one free name is safe: the server reserves the name in the
 * database before the bytes move, and the loser gets what its onConflict
 * asks for - the next free name, the winner's content replaced, or the
 * usual refusal, shown here as skipped.
 *
 * Large files go up in chunks through an upload session (sendFileUpload
 * picks), which retries a failed chunk on its own and resumes from what the
 * server holds; the queue only sees a request that takes longer.
 *
 * `uploads-changed` is dispatched on window once the queue drains with new
 * files on the server, and after a discarded duplicate.
 */
// Three, not six: an HTTP/1.1 deployment gets six connections per host, and
// the server-sent events stream already holds one.
const MAX_IN_FLIGHT = 3;

// From this size on a file goes up in chunks. Below it a cut costs little to
// send again, and one request is cheaper than three.
const RESUMABLE_THRESHOLD = 50 * 1024 * 1024;

// Waits between attempts at one step of a chunked upload: about two minutes
// in all, which outlasts a network switch or a laptop waking up.
const RETRY_DELAYS = [1000, 2000, 4000, 8000, 15000, 30000, 30000, 30000];

window.createUploadQueue = function createUploadQueue({
  send, discard, notify, confirm, release = () => {},
}) {
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
        const uploading = this.items.filter((item) => item.status === 'uploading').length;
        if (uploading > 1) return `Pausing after the ${uploading} files in flight`;
        if (uploading) return 'Pausing after the current file';
        return `Paused - ${plural(pending)} left`;
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
          retrying: false,
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
        case 'uploading':
          if (item.retrying) return `Connection lost, retrying - ${size(item.loaded)} of ${size(item.size)}`;
          // Every byte is sent: the server is still storing the file.
          if (item.size && item.loaded >= item.size) return 'Finishing up';
          return `${size(item.loaded)} of ${size(item.size)}`;
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

    retryFailed() {
      for (const item of this.items) {
        if (item.status !== 'failed') continue;
        item.status = 'queued';
        item.error = '';
      }
      this._pump();
    },

    // The files in flight finish: an aborted request cannot be resumed, and
    // one aborted after the server stored it would come back as a name
    // collision (or a second copy) on resume. Cancelling them is the way to
    // stop them now.
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
      // A failed large file keeps what it sent for a retry; nobody will retry now.
      for (const item of this.items) {
        if (item.status === 'failed' && files.has(item.id)) release(files.get(item.id));
      }
      this.items = [];
      files.clear();
      this.paused = false;
    },

    get duplicateCount() {
      return this._duplicates().length;
    },

    async discard(id) {
      const item = this._find(id);
      if (item && (await this._discard(item))) notify('uploads-changed');
    },

    async discardAllDuplicates() {
      let changed = false;
      for (const item of this._duplicates()) {
        // Two copies uploaded side by side each list the other; once one is
        // gone the other is the only copy left, and stays.
        if (!item.duplicates.length) continue;
        if (await this._discard(item)) changed = true;
      }
      if (changed) notify('uploads-changed');
    },

    keep(id) {
      const item = this._find(id);
      if (item) item.duplicates = [];
    },

    keepAllDuplicates() {
      for (const item of this._duplicates()) item.duplicates = [];
    },

    _duplicates() {
      return this.items.filter((item) => item.status === 'done' && item.duplicates.length > 0);
    },

    async _discard(item) {
      if (item.status !== 'done' || !item.uuid) return false;
      if (!(await discard(item.uuid, item.savedName || item.name))) return false;
      item.status = 'discarded';
      item.duplicates = [];
      for (const other of this.items) {
        other.duplicates = other.duplicates.filter((dup) => dup.uuid !== item.uuid);
      }
      return true;
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
      while (!this.paused && inFlight.size < MAX_IN_FLIGHT) {
        const item = this.items.find((candidate) => candidate.status === 'queued');
        if (!item) break;
        this._send(item);
      }
      if (changedSinceIdle && !this.pendingCount) {
        changedSinceIdle = false;
        notify('uploads-changed');
      }
    },

    _send(item) {
      item.status = 'uploading';
      item.loaded = 0;
      item.retrying = false;
      const request = send(
        files.get(item.id),
        item,
        (loaded) => {
          if (inFlight.get(item.id) === request) item.loaded = loaded;
        },
        (retrying) => {
          if (inFlight.get(item.id) === request) item.retrying = retrying;
        },
      );
      inFlight.set(item.id, request);
      request.promise.then(
        ({ status, body }) => {
          if (inFlight.get(item.id) !== request) return;
          inFlight.delete(item.id);
          files.delete(item.id);
          item.retrying = false;
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
          item.retrying = false;
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

window.sendFileUpload = function sendFileUpload(file, options, onProgress, onRetrying) {
  if (file.size >= RESUMABLE_THRESHOLD) {
    return window.sendResumableUpload(file, options, onProgress, onRetrying);
  }
  const { folderId, onConflict } = options;
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

// The upload session of each large file that went up partly, so a retry
// resumes it. Keyed by the File the user picked: the same file object comes
// back on a retry, and a WeakMap lets it go with the queue row.
const resumableSessions = new WeakMap();

const uploadSessionUrl = (uuid) => `/api/v1/files/uploads/${uuid}`;

function uploadFailure(status, body) {
  const error = new Error(window.fileActions.firstErrorMessage(body));
  error.nameCollision = status === 400 && Array.isArray(body.name);
  return error;
}

// Drops what the server holds of an upload nobody will resume.
window.releaseFileUpload = function releaseFileUpload(file) {
  const uuid = resumableSessions.get(file);
  if (!uuid) return;
  resumableSessions.delete(file);
  fetch(uploadSessionUrl(uuid), {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCSRFToken() },
  }).catch(() => {});
};

/**
 * Sends *file* in chunks through an upload session. Same contract as
 * sendFileUpload: `{ promise, abort }`, the promise answering what
 * `POST /api/v1/files` would.
 *
 * A step that fails on the network or on a server error is tried again after
 * a wait, *onRetrying(true)* saying so meanwhile; a chunk tried again after
 * landing in part is answered with where the server stands, and continues
 * from there. When the waits run out the upload fails, keeping its session
 * for a retry from the queue.
 */
window.sendResumableUpload = function sendResumableUpload(
  file, { folderId, onConflict }, onProgress, onRetrying = () => {},
) {
  let xhr = null;
  let cancelWait = null;
  let aborted = false;

  const cancelled = () => Object.assign(new Error('Cancelled'), { cancelled: true });

  const request = (method, url, { body = null, headers = {}, onSent } = {}) => new Promise((resolve, reject) => {
    if (aborted) {
      reject(cancelled());
      return;
    }
    xhr = new XMLHttpRequest();
    xhr.open(method, url);
    xhr.setRequestHeader('X-CSRFToken', getCSRFToken());
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
    if (onSent) xhr.upload.onprogress = (e) => onSent(e.loaded);
    xhr.onload = () => {
      let data = {};
      try {
        data = JSON.parse(xhr.responseText);
      } catch (_) {}
      resolve({ status: xhr.status, body: data });
    };
    xhr.onerror = () => reject(new Error('Network error'));
    xhr.onabort = () => reject(cancelled());
    xhr.send(body);
  });

  const wait = (ms) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cancelWait = null;
      resolve();
    }, ms);
    cancelWait = () => {
      clearTimeout(timer);
      reject(cancelled());
    };
  });

  // *send* again while the answer says to wait: a lost connection, a server
  // error, or *busy* - the server still writing what an earlier attempt sent.
  const persist = async (send, busy = () => false) => {
    for (let attempt = 0; ; attempt++) {
      let response = null;
      let failure = null;
      try {
        response = await send();
      } catch (error) {
        if (error.cancelled) throw error;
        failure = error;
      }
      const transient = failure || response.status >= 500 || response.status === 408
        || response.status === 429 || busy(response);
      if (!transient) {
        onRetrying(false);
        return response;
      }
      if (attempt >= RETRY_DELAYS.length) {
        if (failure) throw failure;
        return response;
      }
      onRetrying(true);
      await wait(RETRY_DELAYS[attempt]);
    }
  };

  const promise = (async () => {
    let uuid = resumableSessions.get(file);
    let offset = 0;
    let chunkSize = 0;
    if (uuid) {
      const { status, body } = await persist(() => request('GET', uploadSessionUrl(uuid)));
      if (status === 200) {
        offset = body.completed ? file.size : body.offset;
        chunkSize = body.chunk_size;
      } else {
        uuid = null;
        resumableSessions.delete(file);
      }
    }
    if (!uuid) {
      const { status, body } = await persist(() => request('POST', '/api/v1/files/uploads', {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: file.name,
          size: file.size,
          parent: folderId || null,
          on_conflict: onConflict || 'error',
          mime_type: file.type || '',
        }),
      }));
      if (status !== 201) throw uploadFailure(status, body);
      uuid = body.uuid;
      offset = body.offset;
      chunkSize = body.chunk_size;
      resumableSessions.set(file, uuid);
      // Cancelled while the session was being opened: abort() found none to drop.
      if (aborted) {
        window.releaseFileUpload(file);
        throw cancelled();
      }
    }

    const fail = (status, body) => {
      // Refused for good: nothing to resume.
      window.releaseFileUpload(file);
      return uploadFailure(status, body);
    };

    for (;;) {
      while (offset < file.size) {
        const start = offset;
        const end = Math.min(start + chunkSize, file.size);
        const { status, body } = await persist(
          () => request('PATCH', uploadSessionUrl(uuid), {
            headers: {
              'Content-Type': 'application/octet-stream',
              'Content-Range': `bytes ${start}-${end - 1}/${file.size}`,
            },
            body: file.slice(start, end),
            onSent: (loaded) => onProgress(start + loaded),
          }),
          (response) => response.status === 409 && response.body.offset === start,
        );
        // 409: an earlier attempt landed after all; 400: this one was cut.
        // A 409 at the same offset is a server still busy after every wait.
        const moved = typeof body.offset === 'number' && !(status === 409 && body.offset === start);
        if (moved && [200, 400, 409].includes(status)) {
          offset = body.offset;
          onProgress(offset);
          continue;
        }
        if (status >= 500 || status === 409) throw new Error('Network error');
        throw fail(status, body);
      }

      const { status, body } = await persist(
        () => request('POST', `${uploadSessionUrl(uuid)}/finalize`),
        (response) => response.status === 409 && response.body.offset === file.size,
      );
      if (status === 200 || status === 201) {
        resumableSessions.delete(file);
        return { status, body };
      }
      if (status === 409 && typeof body.offset === 'number' && body.offset < file.size) {
        offset = body.offset;
        continue;
      }
      if (status >= 500 || status === 409) throw new Error('Network error');
      throw fail(status, body);
    }
  })();

  return {
    promise,
    abort: () => {
      aborted = true;
      if (cancelWait) cancelWait();
      if (xhr) xhr.abort();
      window.releaseFileUpload(file);
    },
  };
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
    release: window.releaseFileUpload,
  }));

  // Leaving the page aborts every request still in flight.
  window.addEventListener('beforeunload', (e) => {
    if (!Alpine.store('uploads').pendingCount) return;
    e.preventDefault();
    e.returnValue = '';
  });
});
