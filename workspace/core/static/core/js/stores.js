// Global Alpine stores: presence, notifications, push.
//
// Reads runtime configuration:
//   - presence/notification updates: window dispatched events from sse.js
//   - service-worker version: <body data-app-version="...">
document.addEventListener('alpine:init', function () {
  // The stream only carries the users this page asked about. Any status read
  // (statusOf, hence ringClass/dotClass) registers its id, so every surface
  // that renders a presence is covered without declaring anything. Kept out of
  // the store: statusOf runs inside Alpine effects, which must not write
  // reactive state.
  const watchedIds = new Set();
  let pendingIds = [];
  let flushTimer = null;
  const MAX_IDS_PER_REQUEST = 500;
  // The endpoint wakes the stream before it answers, and the two may be served
  // by processes whose status caches differ by a few seconds: a stream change
  // can land first and be newer. An answer never overrides a stream change
  // received after its request went out - the stream would not correct it.
  let streamSeq = 0;
  const streamSeqOf = new Map();

  function postWatch(ids) {
    for (let i = 0; i < ids.length; i += MAX_IDS_PER_REQUEST) {
      const batch = ids.slice(i, i + MAX_IDS_PER_REQUEST);
      const sentAt = streamSeq;
      fetch('/api/v1/users/presence', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ user_ids: batch }),
      })
        .then((r) => {
          if (!r.ok) throw new Error('presence watch failed: ' + r.status);
          return r.json();
        })
        .then((statuses) => {
          const fresh = {};
          for (const [key, status] of Object.entries(statuses || {})) {
            if ((streamSeqOf.get(Number(key)) || 0) <= sentAt) fresh[key] = status;
          }
          Alpine.store('presence').handleChanges(fresh);
        })
        .catch(() => {
          // Forget the batch so the next status read asks again, instead of
          // leaving it unfollowed until the stream reconnects.
          batch.forEach((uid) => watchedIds.delete(uid));
        });
    }
  }

  function watchId(uid) {
    if (!(uid > 0) || watchedIds.has(uid)) return;
    watchedIds.add(uid);
    pendingIds.push(uid);
    if (flushTimer === null) {
      flushTimer = setTimeout(() => {
        flushTimer = null;
        const ids = pendingIds;
        pendingIds = [];
        postWatch(ids);
      }, 50);
    }
  }

  // The server-side watch list may have lapsed while the stream was down
  // (TTL, eviction): send the whole set again so the new stream follows it.
  window.addEventListener('sse:reconnect', function () {
    if (watchedIds.size) postWatch(Array.from(watchedIds));
  });

  Alpine.store('presence', {
    online: new Set(),
    away: new Set(),
    busy: new Set(),
    bot: new Set(),

    handleSnapshot(data) {
      // Only overwrite a bucket when the payload actually carries it. A
      // presence update that omits a category (partial/empty/malformed
      // payload) must NOT wipe the previous membership - otherwise every
      // ring in that bucket flickers to "offline" until the next good
      // snapshot. This matters most for `bot`, which is static identity
      // (bots have no UserPresence row, so their ring depends solely on
      // this list): a missing `bot` array would erase every bot ring for
      // no reason. Keep the previous info when none is received.
      if (!data) return;
      if (Array.isArray(data.online)) this.online = new Set(data.online);
      if (Array.isArray(data.away)) this.away = new Set(data.away);
      if (Array.isArray(data.busy)) this.busy = new Set(data.busy);
      if (Array.isArray(data.bot)) this.bot = new Set(data.bot);
    },

    handleChanges(changes) {
      // { "<user id>": "online" | "away" | "busy" | "bot" | "offline" }, from a
      // presence_changes event or the watch endpoint. Only the listed users
      // move; everyone else keeps their bucket.
      if (!changes || typeof changes !== 'object') return;
      const buckets = {
        online: new Set(this.online),
        away: new Set(this.away),
        busy: new Set(this.busy),
        bot: new Set(this.bot),
      };
      for (const [key, status] of Object.entries(changes)) {
        const uid = Number(key);
        for (const set of Object.values(buckets)) set.delete(uid);
        if (buckets[status]) buckets[status].add(uid);
      }
      this.online = buckets.online;
      this.away = buckets.away;
      this.busy = buckets.busy;
      this.bot = buckets.bot;
    },

    setLocalStatus(userId, status) {
      // Optimistic local update when the current user picks a status from the
      // navbar, applied before the next SSE snapshot confirms it. Reassign the
      // Sets instead of mutating them in place: Alpine reactivity does not
      // track `Set.add`/`Set.delete`, so an in-place mutation would leave the
      // user's own ring stale until the next snapshot.
      const uid = Number(userId);
      const online = new Set(this.online);
      const away = new Set(this.away);
      const busy = new Set(this.busy);
      online.delete(uid);
      away.delete(uid);
      busy.delete(uid);
      if (status === 'online') online.add(uid);
      else if (status === 'away') away.add(uid);
      else if (status === 'busy') busy.add(uid);
      // 'invisible' (or anything else) leaves the user out of every bucket,
      // so they render as offline to themselves too.
      this.online = online;
      this.away = away;
      this.busy = busy;
    },

    statusOf(userId) {
      const uid = Number(userId);
      watchId(uid);
      if (this.bot.has(uid)) return 'bot';
      if (this.busy.has(uid)) return 'busy';
      if (this.online.has(uid)) return 'online';
      if (this.away.has(uid)) return 'away';
      return 'offline';
    },

    ringClass(userId) {
      const s = this.statusOf(userId);
      if (s === 'bot') return 'ring-secondary';
      if (s === 'busy') return 'ring-error';
      if (s === 'online') return 'ring-success';
      if (s === 'away') return 'ring-warning';
      return 'ring-base-300';
    },

    dotClass(userId) {
      const s = this.statusOf(userId);
      if (s === 'bot') return 'bg-secondary';
      if (s === 'busy') return 'bg-error';
      if (s === 'online') return 'bg-success';
      if (s === 'away') return 'bg-warning';
      return 'bg-base-300';
    },
  });

  window.addEventListener('sse:presence.presence_snapshot', function (e) {
    Alpine.store('presence').handleSnapshot(e.detail);
  });
  window.addEventListener('sse:presence.presence_changes', function (e) {
    streamSeq += 1;
    for (const key of Object.keys(e.detail || {})) streamSeqOf.set(Number(key), streamSeq);
    Alpine.store('presence').handleChanges(e.detail);
  });

  // Notifications store
  const NOTIFICATIONS_PAGE_SIZE = 20;

  Alpine.store('notifications', {
    unread: 0,
    items: [],
    loaded: false,
    loading: false,
    hasMore: false,
    loadingMore: false,
    _filter: 'all',
    _search: '',

    setCount(n) {
      this.unread = n;
    },

    _buildUrl(extra) {
      const p = new URLSearchParams({ limit: String(NOTIFICATIONS_PAGE_SIZE) });
      if (this._filter === 'unread') p.set('filter', 'unread');
      else if (this._filter !== 'all') p.set('origin', this._filter);
      if (this._search) p.set('search', this._search);
      if (extra) Object.entries(extra).forEach(([k, v]) => p.set(k, v));
      return '/api/v1/notifications?' + p.toString();
    },

    async fetchList(opts) {
      const filter = opts?.filter ?? this._filter;
      const search = opts?.search ?? this._search;
      this._filter = filter;
      this._search = search;
      await this._fetchHead(NOTIFICATIONS_PAGE_SIZE);
    },

    // Reloads as many rows as are listed, so the pages added by Load more survive.
    async refresh() {
      await this._fetchHead(Math.max(NOTIFICATIONS_PAGE_SIZE, this.items.length));
    },

    async _fetchHead(limit) {
      this.loading = true;
      try {
        const r = await fetch(this._buildUrl({ limit }), { credentials: 'same-origin' });
        if (r.ok) {
          this.items = await r.json();
          this.hasMore = r.headers.get('X-Has-More') === 'true';
          this.loaded = true;
        }
      } finally {
        this.loading = false;
      }
    },

    async loadMore() {
      if (this.loadingMore || !this.hasMore || this.items.length === 0) return;
      this.loadingMore = true;
      try {
        const r = await fetch(this._buildUrl({ offset: this.items.length }), { credentials: 'same-origin' });
        if (r.ok) {
          // A notification that arrived since the first page shifts the offsets by one.
          const known = new Set(this.items.map(n => n.uuid));
          const page = await r.json();
          this.items = this.items.concat(page.filter(n => !known.has(n.uuid)));
          this.hasMore = r.headers.get('X-Has-More') === 'true';
        }
      } finally {
        this.loadingMore = false;
      }
    },

    async markRead(uuid) {
      const r = await fetch('/api/v1/notifications/' + uuid, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (!r.ok) return;
      const item = this.items.find(n => n.uuid === uuid);
      if (item && !item.is_read) {
        item.is_read = true;
        this.unread = Math.max(0, this.unread - 1);
      }
    },

    async markAllRead() {
      const r = await fetch('/api/v1/notifications/read-all', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (!r.ok) return;
      this.items.forEach(n => n.is_read = true);
      this.unread = 0;
    },
  });

  window.addEventListener('sse:notifications.count', function (e) {
    Alpine.store('notifications').setCount(e.detail.unread);
  });

  // Catch up on what the stream missed while it was closed (mobile resume, bfcache, 600 s cut).
  window.addEventListener('sse:reconnect', function () {
    const store = Alpine.store('notifications');
    if (store.loaded) store.refresh();
  });

  // Web Push store
  Alpine.store('push', {
    supported: 'serviceWorker' in navigator && 'PushManager' in window,
    subscription: null,
    loading: false,

    get enabled() { return !!this.subscription; },

    async init() {
      if (!this.supported) return;
      try {
        const params = new URLSearchParams();
        const appVersion = document.body?.dataset?.appVersion || '';
        if (appVersion) params.set('v', appVersion);
        // The worker caches the offline page in this language: a new language
        // is a new URL, which makes the browser install the worker again.
        const language = document.documentElement.lang;
        if (language) params.set('lang', language);
        const query = params.toString();
        const swUrl = query ? '/sw.js?' + query : '/sw.js';
        const reg = await navigator.serviceWorker.register(swUrl);
        this.subscription = await reg.pushManager.getSubscription();
        // The backend prunes subscriptions the push service reports dead
        // (404/410). Re-register the browser's copy so a pruned row does not
        // leave this device silently unreachable while the toggle still
        // shows enabled. The endpoint upserts, so this is idempotent.
        if (this.subscription) {
          const resp = await this._saveSubscription(this.subscription);
          if (!resp.ok) console.warn('Push subscription resync failed:', resp.status);
        }
      } catch (e) {
        console.warn('SW registration failed:', e);
      }
    },

    async toggle() {
      if (this.loading) return;
      this.loading = true;
      try {
        if (this.subscription) {
          await this._unsubscribe();
        } else {
          await this._subscribe();
        }
      } finally {
        this.loading = false;
      }
    },

    async _subscribe() {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') return;
      const resp = await fetch('/api/v1/notifications/push/key', { credentials: 'same-origin' });
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.public_key) return;
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: this._b64toArray(data.public_key),
      });
      const saveResp = await this._saveSubscription(sub);
      // Only commit local state once the backend has the subscription. Otherwise
      // a network error leaves the UI thinking we're enabled while the server
      // has nothing to push to.
      if (!saveResp.ok) {
        await sub.unsubscribe();
        return;
      }
      this.subscription = sub;
    },

    async _saveSubscription(sub) {
      const json = sub.toJSON();
      return fetch('/api/v1/notifications/push/subscribe', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys }),
      });
    },

    async _unsubscribe() {
      if (!this.subscription) return;
      const endpoint = this.subscription.endpoint;
      await this.subscription.unsubscribe();
      this.subscription = null;
      const csrfToken = getCSRFToken();
      await fetch('/api/v1/notifications/push/subscribe', {
        method: 'DELETE', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ endpoint: endpoint }),
      });
    },

    _b64toArray(base64String) {
      const padding = '='.repeat((4 - base64String.length % 4) % 4);
      const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
      const raw = window.atob(base64);
      const arr = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; ++i) arr[i] = raw.charCodeAt(i);
      return arr;
    },
  });
});
