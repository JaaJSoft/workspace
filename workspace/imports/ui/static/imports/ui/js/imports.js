// Imports page: connections, jobs with live progress, and the import wizard.
//
// Talks to /api/v1/imports (JSON). Progress arrives over the global SSE stream
// as "imports.job" events; each event triggers a refetch of that job so the
// list always reflects the server, and a slow poll covers a missed event.

const IMPORTS_API = '/api/v1/imports';
const ACTIVE_STATUSES = ['pending', 'running'];
const JOBS_POLL_MS = 10000;

// What each data kind means to the user; providers declare which ones they serve.
const KIND_LABELS = {
  files: { name: 'Files', description: 'Folders and files, with their dates.' },
  contacts: { name: 'Contacts', description: 'Address books, with their contact groups as lists.' },
};

// What a kind has finished, out of what total.
const KIND_PROGRESS = {
  files: (s) => ({
    done: (s.files || 0) + (s.unchanged || 0) + (s.skipped || 0) + (s.failed || 0),
    total: s.total_files || 0,
  }),
  contacts: (s) => ({
    done: (s.cards || 0) + (s.unchanged || 0) + (s.failed || 0),
    total: s.total_cards || 0,
  }),
};

// The non-zero counters of a kind, in reading order.
const KIND_SUMMARY = {
  files: (s, formatSize) => {
    const parts = [];
    if (s.files) parts.push(`${s.files} imported`);
    if (s.unchanged) parts.push(`${s.unchanged} unchanged`);
    if (s.skipped) parts.push(`${s.skipped} skipped`);
    if (s.failed) parts.push(`${s.failed} failed`);
    if (s.bytes) parts.push(formatSize(s.bytes));
    return parts;
  },
  contacts: (s) => {
    const parts = [];
    if (s.created) parts.push(`${s.created} added`);
    if (s.updated) parts.push(`${s.updated} updated`);
    if (s.unchanged) parts.push(`${s.unchanged} unchanged`);
    if (s.lists) parts.push(`${s.lists} lists`);
    if (s.failed) parts.push(`${s.failed} failed`);
    return parts;
  },
};

// A partial job payload without kinds is a files job.
function jobKinds(job) {
  return job.kinds && job.kinds.length ? job.kinds : ['files'];
}

// Copy shown next to the connection form, per provider.
const PROVIDER_HINTS = {
  nextcloud: {
    url: 'Instance URL, as in your browser (https://cloud.example.org). The WebDAV path is derived from it.',
    secret: 'Use an app password (Settings → Security → Devices & sessions), not your login password.',
  },
  webdav: {
    url: 'Full WebDAV URL of the folder to import from.',
    secret: 'Password or app token for that account.',
  },
};

function readJson(id, fallback) {
  const el = document.getElementById(id);
  if (!el) return fallback;
  try { return JSON.parse(el.textContent); } catch (e) { return fallback; }
}

async function api(path, { method = 'GET', body } = {}) {
  const headers = { 'X-CSRFToken': getCSRFToken() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(IMPORTS_API + path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  if (res.status !== 204) {
    try { data = await res.json(); } catch (e) { data = null; }
  }
  return { ok: res.ok, status: res.status, data };
}

function errorMessage(data, fallback) {
  if (!data) return fallback;
  if (typeof data.detail === 'string') return data.detail;
  // DRF field errors: {field: [msg]} or nested per kind.
  const parts = [];
  const walk = (obj, prefix) => {
    for (const [key, value] of Object.entries(obj)) {
      const label = prefix ? `${prefix}.${key}` : key;
      if (Array.isArray(value)) parts.push(`${label}: ${value.join(' ')}`);
      else if (value && typeof value === 'object') walk(value, label);
      else parts.push(`${label}: ${value}`);
    }
  };
  walk(data, '');
  return parts.join(' · ') || fallback;
}

function emptyConnectionForm(provider) {
  return { provider: provider || '', label: '', base_url: '', username: '', secret: '' };
}

// Only what the user changed goes in the PATCH: the server re-checks the
// credentials against the remote whenever the URL or username is sent, and a
// plain rename should not need the old cloud to be up.
function connectionChanges(original, form) {
  const body = {};
  for (const field of ['label', 'base_url', 'username']) {
    if (!original || form[field] !== original[field]) body[field] = form[field];
  }
  if (form.secret) body.secret = form.secret;
  return body;
}

function emptyWizard() {
  return {
    step: 1,
    connection: null,
    kinds: ['files'],
    options: {
      files: {
        source_path: '/',
        destination: null,
        destination_name: 'Root of my files',
        on_conflict: 'rename',
        create_root_folder: true,
      },
      contacts: { books: [] },
    },
    error: '',
    launching: false,
  };
}

window.importsApp = function importsApp() {
  return {
    providers: [],
    connections: [],
    jobs: [],
    highlightJob: '',

    connForm: emptyConnectionForm(),
    connEditing: null,
    connOriginal: null,
    connError: '',
    connSaving: false,
    connBusy: {},

    wizard: emptyWizard(),
    browse: { path: '/', entries: [], loading: false, error: '' },
    groups: [],
    addressBooks: { loading: false, error: '', loadedFor: '' },

    errorsDialog: { job: null, items: [], count: 0, loading: false, error: '' },

    _pollTimer: null,
    _onJobEvent: null,
    _onReconnect: null,

    init() {
      this.providers = readJson('providers-data', []);
      this.connections = readJson('connections-data', []);
      this.jobs = readJson('jobs-data', []);
      this.highlightJob = readJson('highlight-job-data', '');
      this.groups = readJson('groups-data', []);
      this._onJobEvent = (e) => this.onJobEvent(e.detail);
      this._onReconnect = () => this.refreshJobs();
      window.addEventListener('sse:imports.job', this._onJobEvent);
      window.addEventListener('sse:reconnect', this._onReconnect);
      this._schedulePoll();
      if (readJson('open-wizard-data', false)) {
        // ?new=1 from a command or a link: without a connection the wizard
        // has nothing to offer, so start with the connection dialog.
        this.$nextTick(() => (this.connections.length ? this.openWizard() : this.openConnectionDialog()));
      }
      if (this.highlightJob) {
        this.$nextTick(() => {
          const el = document.getElementById('job-' + this.highlightJob);
          if (el) el.scrollIntoView({ block: 'center' });
        });
      }
    },

    destroy() {
      if (this._pollTimer) clearTimeout(this._pollTimer);
      window.removeEventListener('sse:imports.job', this._onJobEvent);
      window.removeEventListener('sse:reconnect', this._onReconnect);
    },

    // -- helpers ----------------------------------------------------------

    credentialProviders() {
      return this.providers.filter((p) => p.auth === 'credentials');
    },

    providerName(slug) {
      const p = this.providers.find((x) => x.slug === slug);
      return p ? p.name : slug;
    },

    hint(kind) {
      const h = PROVIDER_HINTS[this.connForm.provider];
      return h ? h[kind] : '';
    },

    formatSize(bytes) {
      return typeof formatFileSize === 'function' ? formatFileSize(bytes) : `${bytes} B`;
    },

    formatDate(value) {
      if (!value) return '';
      return new Date(value).toLocaleString();
    },

    hasActiveJobs() {
      return this.jobs.some((j) => ACTIVE_STATUSES.includes(j.status));
    },

    isActive(job) {
      return ACTIVE_STATUSES.includes(job.status);
    },

    quotaLabel(conn) {
      const c = conn.capabilities || {};
      if (c.quota_used == null) return '';
      const used = this.formatSize(c.quota_used);
      return c.quota_available != null
        ? `${used} used · ${this.formatSize(c.quota_available)} free`
        : `${used} used`;
    },

    // The kind the job is working on: the first not done yet, else the last.
    currentKind(job) {
      const kinds = jobKinds(job);
      const stats = job.stats || {};
      return kinds.find((kind) => (stats[kind] || {}).phase !== 'done') || kinds[kinds.length - 1];
    },

    progress(job) {
      const kind = this.currentKind(job);
      const s = (job.stats && job.stats[kind]) || {};
      const { done, total } = (KIND_PROGRESS[kind] || KIND_PROGRESS.files)(s);
      const phase = s.phase || (job.status === 'pending' ? 'pending' : '');
      let pct = 0;
      if (job.status === 'completed') pct = 100;
      else if (total > 0) pct = Math.min(100, Math.round((done / total) * 100));
      // While listing, done/total both grow (unchanged entries are counted as
      // they are seen), so a ratio would mislead - a retry would look almost
      // finished before copying anything.
      const determinate = total > 0 && phase !== 'listing';
      return { kind, done, total, pct, phase, determinate, stats: s };
    },

    isStopping(job) {
      return this.isActive(job) && !!job.cancel_requested_at;
    },

    phaseLabel(job) {
      const p = this.progress(job);
      if (this.isStopping(job)) return 'Stopping… what is already imported stays.';
      if (job.status === 'pending') return 'Waiting to start…';
      if (job.status !== 'running') return '';
      if (p.kind === 'contacts') {
        return p.phase === 'listing'
          ? `Listing the address books… ${p.total} contacts found`
          : `Importing contacts… ${p.done} / ${p.total}`;
      }
      if (p.phase === 'listing') return `Listing the remote folders… ${p.total} files found`;
      return `Copying… ${p.done} / ${p.total}`;
    },

    summary(job) {
      const stats = job.stats || {};
      const kinds = jobKinds(job).filter((kind) => stats[kind]);
      const labelled = kinds.length > 1 || (kinds.length === 1 && kinds[0] !== 'files');
      return kinds
        .map((kind) => {
          const parts = (KIND_SUMMARY[kind] || (() => []))(stats[kind], (bytes) => this.formatSize(bytes));
          if (!parts.length) return '';
          const text = parts.join(' · ');
          return labelled ? `${(KIND_LABELS[kind] || { name: kind }).name}: ${text}` : text;
        })
        .filter(Boolean)
        .join(' / ');
    },

    failedCount(job) {
      return Object.values(job.stats || {}).reduce((sum, s) => sum + ((s && s.failed) || 0), 0);
    },

    bookSummary(job) {
      const count = ((job.options && job.options.contacts && job.options.contacts.books) || []).length;
      return count === 1 ? '1 address book' : `${count} address books`;
    },

    targetLabel(target) {
      if (target === 'mine') return 'My contacts';
      const group = this.groups.find((g) => `group:${g.id}` === target);
      return group ? group.name : target;
    },

    statusClass(status) {
      return {
        pending: 'badge-ghost',
        running: 'badge-info',
        completed: 'badge-success',
        failed: 'badge-error',
        cancelled: 'badge-warning',
      }[status] || 'badge-ghost';
    },

    // -- jobs: live updates ------------------------------------------------

    async onJobEvent(detail) {
      if (!detail || !detail.job) return;
      await this.refreshJob(detail.job);
    },

    async refreshJob(uuid) {
      const { ok, data } = await api(`/jobs/${uuid}`);
      if (ok) this.upsertJob(data);
    },

    // One card per job, whichever arrives first: a create/retry response or
    // the refetch an SSE event triggered while that request was in flight.
    upsertJob(job) {
      const idx = this.jobs.findIndex((j) => j.uuid === job.uuid);
      if (idx === -1) this.jobs.unshift(job);
      else this.jobs.splice(idx, 1, job);
    },

    async refreshJobs() {
      const { ok, data } = await api('/jobs?limit=50');
      if (ok && data) this.jobs = data.results;
    },

    _schedulePoll() {
      if (this._pollTimer) clearTimeout(this._pollTimer);
      this._pollTimer = setTimeout(async () => {
        if (this.hasActiveJobs()) await this.refreshJobs();
        this._schedulePoll();
      }, JOBS_POLL_MS);
    },

    // -- jobs: actions -----------------------------------------------------

    async cancelJob(job) {
      const ok = await AppDialog.confirm({
        title: 'Stop this import?',
        message: 'What has already been imported stays.',
        okLabel: 'Stop',
        okClass: 'btn-warning',
        icon: 'octagon-x',
        iconClass: 'bg-warning/10 text-warning',
      });
      if (!ok) return;
      const res = await api(`/jobs/${job.uuid}/cancel`, { method: 'POST' });
      if (!res.ok) return AppAlert.error(errorMessage(res.data, 'Could not stop the import.'));
      await this.refreshJob(job.uuid);
    },

    async retryJob(job) {
      const res = await api(`/jobs/${job.uuid}/retry`, { method: 'POST' });
      if (!res.ok) return AppAlert.error(errorMessage(res.data, 'Could not retry the import.'));
      this.upsertJob(res.data);
      AppAlert.success('Import restarted - only what failed or was never reached runs again.');
      // In eager mode (development) the retry call returns once the import
      // is over, with the job as it was created; refetch its real state.
      await this.refreshJob(res.data.uuid);
    },

    async showErrors(job) {
      this.errorsDialog = { job, items: [], count: 0, loading: true, error: '' };
      this.$refs.errorsDialog.showModal();
      this.$nextTick(() => initLucideIcons());
      const res = await api(`/jobs/${job.uuid}/items?status=failed&limit=200`);
      this.errorsDialog.loading = false;
      if (res.ok) {
        this.errorsDialog.items = res.data.results;
        this.errorsDialog.count = res.data.count;
      } else {
        this.errorsDialog.error = errorMessage(res.data, 'Could not load the report.');
      }
    },

    // -- connections -------------------------------------------------------

    openConnectionDialog(conn) {
      this.connError = '';
      if (conn) {
        this.connEditing = conn.uuid;
        this.connOriginal = conn;
        this.connForm = { provider: conn.provider, label: conn.label, base_url: conn.base_url, username: conn.username, secret: '' };
      } else {
        this.connEditing = null;
        this.connOriginal = null;
        const first = this.credentialProviders()[0];
        this.connForm = emptyConnectionForm(first ? first.slug : '');
      }
      this.$refs.connectionDialog.showModal();
      this.$nextTick(() => initLucideIcons());
    },

    closeConnectionDialog() {
      this.$refs.connectionDialog.close();
    },

    async saveConnection() {
      this.connError = '';
      this.connSaving = true;
      try {
        let res;
        if (this.connEditing) {
          const body = connectionChanges(this.connOriginal, this.connForm);
          res = await api(`/connections/${this.connEditing}`, { method: 'PATCH', body });
        } else {
          res = await api('/connections', { method: 'POST', body: this.connForm });
        }
        if (!res.ok) {
          this.connError = errorMessage(res.data, 'The connection could not be verified.');
          return;
        }
        const idx = this.connections.findIndex((c) => c.uuid === res.data.uuid);
        if (idx === -1) this.connections.unshift(res.data);
        else this.connections.splice(idx, 1, res.data);
        this.closeConnectionDialog();
        AppAlert.success(this.connEditing ? 'Connection updated.' : 'Connected - the server answered with your credentials.');
        if (!this.connEditing && this.wizard.step === 1 && this.$refs.wizardDialog.open) {
          this.wizard.connection = res.data;
        }
      } finally {
        this.connSaving = false;
      }
    },

    async testConnection(conn) {
      this.connBusy[conn.uuid] = true;
      try {
        const res = await api(`/connections/${conn.uuid}/test`, { method: 'POST' });
        if (res.ok) {
          this._replaceConnection(res.data);
          AppAlert.success(`${conn.label} is reachable.`);
        } else {
          // The row keeps last_error: refetch to show it.
          const fresh = await api(`/connections/${conn.uuid}`);
          if (fresh.ok) this._replaceConnection(fresh.data);
          AppAlert.error(errorMessage(res.data, 'The connection failed.'));
        }
      } finally {
        this.connBusy[conn.uuid] = false;
      }
    },

    async deleteConnection(conn) {
      const ok = await AppDialog.confirm({
        title: `Remove ${conn.label}?`,
        message: 'The import history of this connection goes with it. What was imported stays.',
        okLabel: 'Remove',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      const res = await api(`/connections/${conn.uuid}`, { method: 'DELETE' });
      if (!res.ok) return AppAlert.error(errorMessage(res.data, 'Could not remove the connection.'));
      this.connections = this.connections.filter((c) => c.uuid !== conn.uuid);
      this.jobs = this.jobs.filter((j) => j.connection !== conn.uuid);
    },

    _replaceConnection(data) {
      const idx = this.connections.findIndex((c) => c.uuid === data.uuid);
      if (idx !== -1) this.connections.splice(idx, 1, data);
    },

    // -- wizard ------------------------------------------------------------

    openWizard(conn) {
      this.wizard = emptyWizard();
      this.wizard.connection = conn || (this.connections.length === 1 ? this.connections[0] : null);
      this.browse = { path: '/', entries: [], loading: false, error: '' };
      this.addressBooks = { loading: false, error: '', loadedFor: '' };
      this.$refs.wizardDialog.showModal();
      this.$nextTick(() => initLucideIcons());
    },

    closeWizard() {
      this.$refs.wizardDialog.close();
    },

    wizardKinds() {
      const conn = this.wizard.connection;
      if (!conn) return [];
      const provider = this.providers.find((p) => p.slug === conn.provider);
      const kinds = provider ? provider.kinds : [];
      // Known kinds show in the step 3 / run order (KIND_LABELS); anything
      // else keeps the order the provider declared it in.
      const known = Object.keys(KIND_LABELS);
      const ordered = [
        ...known.filter((kind) => kinds.includes(kind)),
        ...kinds.filter((kind) => !known.includes(kind)),
      ];
      return ordered.map((kind) => ({
        kind,
        ...(KIND_LABELS[kind] || { name: kind, description: '' }),
      }));
    },

    toggleKind(kind) {
      const idx = this.wizard.kinds.indexOf(kind);
      if (idx === -1) this.wizard.kinds.push(kind);
      else this.wizard.kinds.splice(idx, 1);
    },

    selectedBooks() {
      return this.wizard.options.contacts.books.filter((book) => book.selected);
    },

    canContinue() {
      const w = this.wizard;
      if (w.step === 1) return !!w.connection;
      if (w.step === 2) return w.kinds.length > 0;
      if (w.step === 3 && w.kinds.includes('contacts')) return this.selectedBooks().length > 0;
      return true;
    },

    nextStep() {
      if (!this.canContinue()) return;
      this.wizard.error = '';
      this.wizard.step += 1;
      if (this.wizard.step === 3) {
        if (this.wizard.kinds.includes('files')) this.loadBrowse('/');
        if (this.wizard.kinds.includes('contacts')) this.loadAddressBooks();
      }
      this.$nextTick(() => initLucideIcons());
    },

    // Listed once per connection: going back and forth keeps the choices.
    async loadAddressBooks() {
      const conn = this.wizard.connection;
      if (!conn || this.addressBooks.loadedFor === conn.uuid) return;
      this.addressBooks = { loading: true, error: '', loadedFor: '' };
      const res = await api(`/connections/${conn.uuid}/browse?kind=contacts`);
      this.addressBooks.loading = false;
      if (!res.ok) {
        this.addressBooks.error = errorMessage(res.data, 'Could not list the address books.');
        return;
      }
      this.addressBooks.loadedFor = conn.uuid;
      this.wizard.options.contacts.books = res.data.entries.map((book) => ({
        id: book.id,
        name: book.name,
        description: book.description,
        selected: true,
        target: 'mine',
      }));
    },

    launchBody() {
      const w = this.wizard;
      const options = {};
      if (w.kinds.includes('files')) {
        const files = w.options.files;
        options.files = {
          source_path: files.source_path,
          destination: files.destination,
          on_conflict: files.on_conflict,
          create_root_folder: files.create_root_folder,
        };
      }
      if (w.kinds.includes('contacts')) {
        options.contacts = { books: this.selectedBooks().map((book) => ({ id: book.id, target: book.target })) };
      }
      return { connection: w.connection.uuid, kinds: w.kinds, options };
    },

    prevStep() {
      this.wizard.error = '';
      this.wizard.step = Math.max(1, this.wizard.step - 1);
      this.$nextTick(() => initLucideIcons());
    },

    async loadBrowse(path) {
      const conn = this.wizard.connection;
      if (!conn) return;
      this.browse.loading = true;
      this.browse.error = '';
      const res = await api(`/connections/${conn.uuid}/browse?path=${encodeURIComponent(path)}`);
      this.browse.loading = false;
      if (!res.ok) {
        this.browse.error = errorMessage(res.data, 'Could not list this folder.');
        return;
      }
      this.browse.path = res.data.path;
      this.browse.entries = res.data.entries;
      this.$nextTick(() => initLucideIcons());
    },

    browseInto(entry) {
      if (entry.is_dir) this.loadBrowse(entry.id);
    },

    browseUp() {
      const p = this.browse.path.replace(/\/+$/, '');
      const parent = p.substring(0, p.lastIndexOf('/')) || '/';
      this.loadBrowse(parent);
    },

    browseCrumbs() {
      const parts = this.browse.path.split('/').filter(Boolean);
      const crumbs = [{ label: 'Root', path: '/' }];
      let acc = '';
      for (const part of parts) {
        acc += '/' + part;
        crumbs.push({ label: part, path: acc });
      }
      return crumbs;
    },

    useCurrentFolderAsSource() {
      this.wizard.options.files.source_path = this.browse.path;
    },

    async pickDestination() {
      const picked = await AppDialog.folderPicker({
        title: 'Import into',
        message: 'The imported folders and files land here.',
        okLabel: 'Choose',
      });
      if (!picked) return;
      this.wizard.options.files.destination = picked.uuid;
      this.wizard.options.files.destination_name = picked.uuid ? picked.name : 'Root of my files';
    },

    rootFolderName() {
      const conn = this.wizard.connection;
      return conn ? `${conn.label} import` : '';
    },

    async launch() {
      const w = this.wizard;
      w.error = '';
      w.launching = true;
      try {
        const res = await api('/jobs', { method: 'POST', body: this.launchBody() });
        if (!res.ok) {
          w.error = errorMessage(res.data, 'The import could not be started.');
          return;
        }
        this.upsertJob(res.data);
        this.closeWizard();
        AppAlert.success('Import started. You can leave this page - you will be notified when it ends.');
        // In eager mode (development) the create call returns once the import
        // is over; refetch so the card does not show "pending" forever.
        this.refreshJob(res.data.uuid);
      } finally {
        w.launching = false;
      }
    },
  };
};
