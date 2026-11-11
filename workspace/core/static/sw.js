/* Service Worker — PWA caching + Web Push Notifications */

const SW_PARAMS = new URL(self.location).searchParams;
const CACHE_VERSION = SW_PARAMS.get('v') || 'dev';
// The offline page is cached in the language active when the worker
// installed. The language is part of the worker's URL, so changing it
// installs a new worker under a new cache, which fetches the page again.
const LANGUAGE = SW_PARAMS.get('lang');
const CACHE_NAME = 'workspace-' + CACHE_VERSION + (LANGUAGE ? '-' + LANGUAGE : '');
const OFFLINE_URL = '/offline';

// The manifest's share_target posts here; see receiveIncoming().
const INCOMING_ACTION = '/incoming';

importScripts('/static/files/ui/js/incoming_files.js?v=' + encodeURIComponent(CACHE_VERSION));

/* ──────────────────────────────────────────
   Lifecycle events
   ────────────────────────────────────────── */

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      // The offline page and everything it links: it must render on the
      // very first outage, before cacheFirst() has ever seen these assets.
      return cache.addAll([
        OFFLINE_URL,
        '/static/css/app.css',
        '/static/icons/favicon.svg',
        '/static/ui/js/vendor/lucide/lucide.js',
      ]);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys
          .filter(function (key) { return key.startsWith('workspace-') && key !== CACHE_NAME; })
          .map(function (key) { return caches.delete(key); })
      );
    })
  );
  self.clients.claim();
});

/* ──────────────────────────────────────────
   Fetch — routing by request type
   ────────────────────────────────────────── */

self.addEventListener('fetch', function (event) {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method === 'POST' && url.origin === self.location.origin
      && url.pathname === INCOMING_ACTION) {
    event.respondWith(receiveIncoming(request));
    return;
  }

  // Only handle GET requests
  if (request.method !== 'GET') return;

  // API requests — network only
  if (url.pathname.startsWith('/api/')) return;

  // CDN assets — stale-while-revalidate
  if (url.origin !== self.location.origin) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  // Local static assets — dev: skip SW (always fresh), prod: cache first (invalidated by version bump)
  if (url.pathname.startsWith('/static/')) {
    if (CACHE_VERSION === 'dev') return;
    event.respondWith(cacheFirst(request));
    return;
  }

  // HTML navigation — network first with offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(networkFirstWithOffline(request));
    return;
  }
});

/* ──────────────────────────────────────────
   Caching strategies
   ────────────────────────────────────────── */

function cacheFirst(request) {
  return caches.match(request).then(function (cached) {
    if (cached) return cached;
    return fetch(request).then(function (response) {
      if (response.ok) {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(function (cache) { cache.put(request, clone); });
      }
      return response;
    });
  }).catch(function () {
    return new Response('', { status: 503, statusText: 'Service Unavailable' });
  });
}

function staleWhileRevalidate(request) {
  return caches.match(request).then(function (cached) {
    const fetchPromise = fetch(request).then(function (response) {
      // For cross-origin, accept opaque responses (status 0)
      if (response.ok || response.type === 'opaque') {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(function (cache) { cache.put(request, clone); });
      }
      return response;
    }).catch(function () {
      return cached || new Response('', { status: 503, statusText: 'Service Unavailable' });
    });
    return cached || fetchPromise;
  }).catch(function () {
    return fetch(request);
  });
}

function networkFirstWithOffline(request) {
  return fetch(request)
    .then(function (response) {
      // `private, no-cache` (the app default) allows a browser-side copy
      // that is revalidated; only `no-store` forbids keeping one at all.
      const cacheControl = response.headers.get('Cache-Control') || '';
      if (response.ok && !/\bno-store\b/i.test(cacheControl)) {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(function (cache) { cache.put(request, clone); });
      }
      return response;
    })
    .catch(function () {
      return caches.match(request).then(function (cached) {
        return cached || caches.match(OFFLINE_URL);
      });
    });
}

/* ──────────────────────────────────────────
   Incoming — what another app shares with the installed app
   ────────────────────────────────────────── */

// The manifest has a single share_target, so everything another app shares
// arrives here, and goes on to the page of the module that receives it with
// the id of what was kept: /<module>?incoming=<id>. Files go to Files.
//
// The files stay in the browser until the Files page has asked where they
// go and handed them to its upload queue: posting them to the server here
// would need a CSRF token the share does not carry, and a folder the user
// has not chosen yet. A share the page cannot find (nothing attached, or
// storing it failed) lands there with an empty id, which it reports.
async function receiveIncoming(request) {
  let id = '';
  try {
    const form = await request.formData();
    const files = form.getAll('files').filter(function (value) { return typeof value !== 'string'; });
    if (files.length) id = await self.incomingFiles.put(files);
  } catch (e) {
    console.warn('Could not keep the incoming files:', e);
  }
  const target = new URL('/files', self.location.origin);
  target.searchParams.set('incoming', id);
  return Response.redirect(target.href, 303);
}

/* ──────────────────────────────────────────
   Push Notifications (existing functionality)
   ────────────────────────────────────────── */

function buildNotificationOptions(payload) {
  return {
    body: payload.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/badge-72.png',
    // Per-source, not per-module: a shared tag makes each notification
    // replace the previous one on screen.
    tag: payload.tag || payload.origin || 'workspace',
    renotify: true,
    data: { url: payload.url || '/' },
  };
}

self.addEventListener('push', function (event) {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch (e) {
    payload = { body: event.data.text() };
  }

  event.waitUntil(
    self.registration.showNotification(
      payload.title || 'Workspace',
      buildNotificationOptions(payload)
    )
  );
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.focus();
          client.navigate(url);
          return;
        }
      }
      return clients.openWindow(url);
    })
  );
});
