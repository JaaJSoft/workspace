/* Service Worker — PWA caching + Web Push Notifications */

var CACHE_VERSION = new URL(self.location).searchParams.get('v') || 'dev';
var CACHE_NAME = 'workspace-' + CACHE_VERSION;
var OFFLINE_URL = '/static/offline.html';

/* ──────────────────────────────────────────
   Lifecycle events
   ────────────────────────────────────────── */

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.add(OFFLINE_URL);
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
  var request = event.request;

  // Only handle GET requests
  if (request.method !== 'GET') return;

  var url = new URL(request.url);

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
        var clone = response.clone();
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
    var fetchPromise = fetch(request).then(function (response) {
      // For cross-origin, accept opaque responses (status 0)
      if (response.ok || response.type === 'opaque') {
        var clone = response.clone();
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
      if (response.ok) {
        var clone = response.clone();
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
   Push Notifications (existing functionality)
   ────────────────────────────────────────── */

self.addEventListener('push', function (event) {
  if (!event.data) return;

  var payload;
  try {
    payload = event.data.json();
  } catch (e) {
    payload = { title: 'New notification', body: event.data.text() };
  }

  var options = {
    body: payload.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/badge-72.png',
    tag: payload.origin || 'workspace',
    renotify: true,
    data: { url: payload.url || '/' },
  };

  event.waitUntil(
    self.registration.showNotification(payload.title || 'Workspace', options)
  );
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = event.notification.data?.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
      for (var client of clientList) {
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
