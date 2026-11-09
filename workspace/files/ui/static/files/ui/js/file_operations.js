/**
 * Batch file operations. Every move, copy, trash, restore, purge, extraction
 * and emptying of the trash goes through POST /api/v1/files/operations, and a
 * worker carries it out item by item.
 *
 *   const report = await window.fileOperations.run('trash', uuids);
 *   const report = await window.fileOperations.run('move', [{ uuid, on_conflict }], { target });
 *
 * run() resolves with the finished report - counts per outcome and the errors,
 * worded for the reader - once the operation is over. It rejects with a
 * FileOperationRefused when the server turns the request itself down (a
 * destination out of reach, too many operations in progress).
 *
 * Progress arrives as `sse:files.operation` events. The stream can be down - a
 * reconnect, a laptop waking up - so the store also asks every few seconds: an
 * operation never waits on an event that was lost.
 *
 * The `fileOperations` Alpine store keeps the operations of this page for the
 * transfers panel. One shows there only once it has lasted SHOW_AFTER ms, or
 * when it ended with something to report, so a quick trash never flashes a
 * panel. `file-operation-finished` is dispatched on window when an operation
 * ends, for the pages that list files to refresh.
 */
const OPERATIONS_URL = '/api/v1/files/operations';
const FINISHED_STATUSES = ['completed', 'failed', 'cancelled'];
const SHOW_AFTER = 500;
const POLL_DELAYS = [1000, 2000, 3000, 5000];
// What an SSE event carries; the errors only come with the report itself.
const PROGRESS_FIELDS = ['status', 'total', 'processed', 'bytes_total', 'bytes_done', 'counts'];

// Needs file_actions.js, for the wording of a refusal.
class FileOperationRefused extends Error {
  constructor(status, body) {
    super(window.fileActions.firstErrorMessage(body));
    this.status = status;
    this.body = body;
  }
}
window.FileOperationRefused = FileOperationRefused;

window.isOperationFinished = function isOperationFinished(operation) {
  return FINISHED_STATUSES.includes(operation.status);
};

// A finished operation the user should hear about even when it was quick.
window.operationNeedsAttention = function operationNeedsAttention(operation) {
  return operation.status !== 'completed' || Boolean(operation.counts && operation.counts.failed);
};

// "Move 3 items to Reports": what the operation was asked to do.
window.describeOperation = function describeOperation(operation) {
  const count = operation.total;
  const folder = operation.target_name || gettext('My files');
  const values = { count, folder };
  const texts = {
    move: ngettext('Move %(count)s item to %(folder)s', 'Move %(count)s items to %(folder)s', count),
    copy: ngettext('Copy %(count)s item to %(folder)s', 'Copy %(count)s items to %(folder)s', count),
    trash: ngettext('Move %(count)s item to the trash', 'Move %(count)s items to the trash', count),
    restore: ngettext('Restore %(count)s item', 'Restore %(count)s items', count),
    purge: ngettext('Delete %(count)s item permanently', 'Delete %(count)s items permanently', count),
    empty_trash: gettext('Empty the trash'),
    extract: ngettext('Extract %(count)s archive to %(folder)s', 'Extract %(count)s archives to %(folder)s', count),
  };
  return interpolate(texts[operation.kind] || '', values, true);
};

// "12 done, 1 failed": what became of the items, in the order a reader scans.
window.summarizeOperation = function summarizeOperation(operation) {
  const counts = operation.counts || {};
  // Each literal is spelled out in its ngettext call: extraction only sees those.
  const parts = [
    [counts.done, (n) => ngettext('%(count)s done', '%(count)s done', n)],
    [counts.replaced, (n) => ngettext('%(count)s replaced', '%(count)s replaced', n)],
    [counts.renamed, (n) => ngettext('%(count)s kept under a new name', '%(count)s kept under a new name', n)],
    [counts.skipped, (n) => ngettext('%(count)s skipped', '%(count)s skipped', n)],
    [counts.failed, (n) => ngettext('%(count)s failed', '%(count)s failed', n)],
  ]
    .filter(([count]) => count)
    .map(([count, text]) => interpolate(text(count), { count }, true));
  const text = parts.join(', ');
  return text.charAt(0).toUpperCase() + text.slice(1);
};

// The status line of a row in the transfers panel.
window.operationStatusText = function operationStatusText(operation) {
  const progress = { done: operation.processed, total: operation.total };
  if (operation.status === 'pending') return gettext('Waiting to start');
  if (operation.status === 'running') {
    if (operation.cancel_requested) return gettext('Stopping after the current item');
    return interpolate(gettext('%(done)s of %(total)s'), progress, true);
  }
  if (operation.status === 'cancelled') {
    return interpolate(gettext('Cancelled after %(done)s of %(total)s'), progress, true);
  }
  if (operation.status === 'failed') {
    return interpolate(gettext('Interrupted after %(done)s of %(total)s'), progress, true);
  }
  return window.summarizeOperation(operation) || gettext('Nothing to do');
};

// The items of *uuids* the operation went through. The report lists at most
// fifty errors, so past that some failed items read as done: the caller's
// next refresh of its listing puts them back.
window.operationDoneUuids = function operationDoneUuids(report, uuids) {
  const failed = new Set((report.errors || []).map((error) => error.uuid));
  return report.status === 'completed' ? uuids.filter((uuid) => !failed.has(uuid)) : [];
};

// The failures the report counts but does not list, past its fifty errors.
window.operationUnlistedErrorsText = function operationUnlistedErrorsText(operation) {
  const unlisted = ((operation.counts && operation.counts.failed) || 0) - (operation.errors || []).length;
  return interpolate(ngettext('And %(count)s more', 'And %(count)s more', unlisted), { count: unlisted }, true);
};

window.operationPercent = function operationPercent(operation) {
  if (window.isOperationFinished(operation)) return 100;
  if (operation.bytes_total) return Math.floor((operation.bytes_done / operation.bytes_total) * 100);
  return operation.total ? Math.floor((operation.processed / operation.total) * 100) : 0;
};

window.createFileOperations = function createFileOperations({
  request,
  notify = () => {},
  schedule = (fn, ms) => setTimeout(fn, ms),
  unschedule = (id) => clearTimeout(id),
}) {
  // Kept out of the reactive store: Alpine would proxy them.
  const waiters = new Map();
  const timers = new Map();

  const clearTimers = (uuid) => {
    for (const id of timers.get(uuid) || []) unschedule(id);
    timers.delete(uuid);
  };
  const addTimer = (uuid, id) => {
    timers.set(uuid, [...(timers.get(uuid) || []), id]);
  };

  return {
    // The operations this page follows, newest first.
    items: [],
    expanded: true,
    // Set by the transfers panel: where there is none, nothing is shown.
    panel: false,

    get rows() {
      return this.items.filter((operation) => operation.shown);
    },

    get activeCount() {
      return this.rows.filter((operation) => !window.isOperationFinished(operation)).length;
    },

    get attentionCount() {
      return this.rows.filter(
        (operation) => window.isOperationFinished(operation) && window.operationNeedsAttention(operation)
      ).length;
    },

    get title() {
      const active = this.activeCount;
      if (active) {
        return interpolate(
          ngettext('%(count)s operation in progress', '%(count)s operations in progress', active),
          { count: active },
          true
        );
      }
      return gettext('Operations finished');
    },

    async run(kind, items = [], { target } = {}) {
      const body = { kind };
      if (kind !== 'empty_trash') {
        body.items = items.map((item) => (typeof item === 'string' ? { uuid: item } : item));
      }
      if (target !== undefined) body.target = target;
      const response = await request(OPERATIONS_URL, { method: 'POST', body });
      if (!response.ok) throw new FileOperationRefused(response.status, response.body);
      return this.follow(response.body);
    },

    // Keep *operation* up to date until it ends; resolves with its report.
    follow(operation) {
      const row = this._upsert(operation);
      if (window.isOperationFinished(row)) return this._settle(row);
      const finished = new Promise((resolve) => {
        waiters.set(row.uuid, [...(waiters.get(row.uuid) || []), resolve]);
      });
      if (!timers.has(row.uuid)) {
        addTimer(row.uuid, schedule(() => {
          const shown = this._find(row.uuid);
          if (shown) shown.shown = true;
        }, SHOW_AFTER));
        this._poll(row.uuid, 0);
      }
      return finished;
    },

    // An `operation` event from the files stream.
    receive(event) {
      const row = this._find(event.operation_uuid);
      if (!row || row.settled) return;
      for (const field of PROGRESS_FIELDS) row[field] = event[field];
      if (window.isOperationFinished(row)) this._settle(row);
    },

    // The operations still running when the page was (re)loaded.
    async restore() {
      this.panel = true;
      const response = await request(OPERATIONS_URL, { method: 'GET' });
      if (!response.ok) return;
      // The server lists the newest first and each row goes on top: oldest first.
      for (const operation of [...response.body].reverse()) {
        if (this._find(operation.uuid)) continue;
        if (window.isOperationFinished(operation) && !window.operationNeedsAttention(operation)) continue;
        this.follow(operation);
        this._find(operation.uuid).shown = true;
      }
    },

    async cancel(uuid) {
      const response = await request(`${OPERATIONS_URL}/${uuid}/cancel`, { method: 'POST' });
      if (!response.ok) return;
      const row = this._upsert(response.body);
      if (window.isOperationFinished(row)) this._settle(row);
    },

    // Forget a finished operation, here and on the server.
    dismiss(uuid) {
      const row = this._find(uuid);
      if (!row || !window.isOperationFinished(row)) return;
      this.items = this.items.filter((operation) => operation.uuid !== uuid);
      request(`${OPERATIONS_URL}/${uuid}`, { method: 'DELETE' }).catch(() => {});
    },

    dismissFinished() {
      for (const row of this.rows.filter(window.isOperationFinished)) this.dismiss(row.uuid);
    },

    _find(uuid) {
      return this.items.find((operation) => operation.uuid === uuid);
    },

    // Take in what the server answered about an operation.
    _upsert(operation) {
      // Only the report itself words the errors, and only once it is over.
      const reported = window.isOperationFinished(operation) && Array.isArray(operation.errors);
      const row = this._find(operation.uuid);
      if (row) {
        Object.assign(row, operation, { reported });
        return row;
      }
      this.items.unshift({ ...operation, reported, shown: false, settled: false });
      // Through the store, so that later writes reach the panel.
      return this.items[0];
    },

    _poll(uuid, attempt) {
      const delay = POLL_DELAYS[Math.min(attempt, POLL_DELAYS.length - 1)];
      addTimer(uuid, schedule(async () => {
        const row = this._find(uuid);
        if (!row || row.settled) return;
        try {
          const response = await request(`${OPERATIONS_URL}/${uuid}`, { method: 'GET' });
          if (response.ok) {
            this._upsert(response.body);
            if (window.isOperationFinished(row)) {
              await this._settle(row);
              return;
            }
          }
        } catch (_) {
          // Offline for a moment: the next attempt will tell.
        }
        this._poll(uuid, attempt + 1);
      }, delay));
    },

    async _settle(row) {
      if (row.settled) return row;
      row.settled = true;
      clearTimers(row.uuid);
      // An event carries no errors: their wording depends on the reader.
      if (!row.reported) {
        try {
          const response = await request(`${OPERATIONS_URL}/${row.uuid}`, { method: 'GET' });
          if (response.ok) this._upsert(response.body);
        } catch (_) {
          // The counts are enough to report on.
        }
      }
      if (!row.shown && window.operationNeedsAttention(row)) row.shown = true;
      if (!row.shown) {
        this.items = this.items.filter((operation) => operation.uuid !== row.uuid);
        request(`${OPERATIONS_URL}/${row.uuid}`, { method: 'DELETE' }).catch(() => {});
      }
      const report = { ...row, errors: [...(row.errors || [])], counts: { ...row.counts } };
      notify('file-operation-finished', report);
      for (const resolve of waiters.get(row.uuid) || []) resolve(report);
      waiters.delete(row.uuid);
      return report;
    },
  };
};

// The store and run() share one client, so the panel sees what run() starts.
window.fileOperations = {
  run(kind, items, options) {
    return window.Alpine.store('fileOperations').run(kind, items, options);
  },

  // Tell the user how *report* ended, unless the transfers panel already does.
  // *summary* words a success worth announcing; a failure always is.
  announce(report, { summary } = {}) {
    const store = window.Alpine.store('fileOperations');
    if (store.panel && store.items.some((operation) => operation.uuid === report.uuid)) return;
    const failed = (report.counts && report.counts.failed) || 0;
    if (report.status === 'failed') {
      window.AppAlert.error(gettext('The operation was interrupted'));
    } else if (failed) {
      const first = report.errors && report.errors[0];
      const text = window.summarizeOperation(report);
      window.AppAlert.warning(first ? `${text} - ${first.message}` : text);
    } else if (summary) {
      const text = summary(report);
      if (text) window.AppAlert.success(text);
    }
  },
};

async function requestJson(url, { method, body }) {
  const headers = { 'X-CSRFToken': getCSRFToken() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    // 204, or an error page.
  }
  return { ok: response.ok, status: response.status, body: data };
}

document.addEventListener('alpine:init', () => {
  Alpine.store('fileOperations', window.createFileOperations({
    request: requestJson,
    notify: (type, detail) => window.dispatchEvent(new CustomEvent(type, { detail })),
  }));
  window.addEventListener('sse:files.operation', (e) => Alpine.store('fileOperations').receive(e.detail));
});
