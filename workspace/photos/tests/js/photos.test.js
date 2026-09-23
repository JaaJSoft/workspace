const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function load({ mobile = false, collapsed = false, observers = [], ...extra } = {}) {
  const saved = [];
  const dispatched = [];
  class FakeObserver {
    constructor(callback, options) {
      this.callback = callback;
      this.options = options;
      this.observed = [];
      this.disconnected = false;
      observers.push(this);
    }
    observe(el) { this.observed.push(el); }
    disconnect() { this.disconnected = true; }
  }
  const ctx = loadScripts(
    [
      // The real mixin: photos.js spreads it, and the menu drives it.
      'workspace/files/ui/static/files/ui/js/properties_panel.js',
      'workspace/photos/ui/static/photos/ui/js/photos.js',
    ],
    {
      sidebarPreference: { initial: () => collapsed, save: (m, v) => saved.push([m, v]) },
      matchMedia: () => ({ matches: mobile }),
      document: { getElementById: () => null, querySelector: () => null },
      IntersectionObserver: FakeObserver,
      CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
      dispatchEvent: (event) => dispatched.push(event),
      tagsMixin: () => ({
        toggleFileTag: async () => {},
        loadTags() { this.tagsLoaded = (this.tagsLoaded || 0) + 1; },
      }),
      innerWidth: 1280,
      innerHeight: 900,
      ...extra,
    },
  );
  return { ctx, saved, dispatched, observers };
}

test('the stored preference collapses the sidebar on desktop only', () => {
  assert.equal(load({ collapsed: true }).ctx.photosApp().sidebarCollapsed(), true);
  assert.equal(load({ collapsed: true, mobile: true }).ctx.photosApp().sidebarCollapsed(), false);
});

test('toggling saves the preference under the photos module', () => {
  const { ctx, saved } = load();
  const app = ctx.photosApp();
  app.toggleCollapse();
  assert.equal(app.collapsed, true);
  assert.deepEqual(saved.map((s) => Array.from(s)), [['photos', true]]);
});

test('toggling is a no-op on a phone', () => {
  const { ctx, saved } = load({ mobile: true });
  const app = ctx.photosApp();
  app.toggleCollapse();
  assert.equal(app.collapsed, false);
  assert.equal(saved.length, 0);
});

test('a response missing one of the page targets keeps the live element', () => {
  const { ctx } = load();
  const app = ctx.photosApp();
  for (const id of ['photos-nav', 'photos-content', 'timeline-grid', 'timeline-more']) {
    let prevented = false;
    app.keepMissingTarget({ detail: { target: { id } }, preventDefault() { prevented = true; } });
    assert.equal(prevented, true, id);
  }
  let prevented = false;
  app.keepMissingTarget({ detail: { target: { id: 'viewer-panel' } }, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
});

test('a tile opens the Files viewer with its uuid, name and type', () => {
  const { ctx, dispatched } = load();
  ctx.photosApp().openPhoto({ dataset: { uuid: 'u1', displayName: 'beach.jpg', fileType: 'jpeg' } });
  assert.equal(dispatched.length, 1);
  assert.equal(dispatched[0].type, 'open-file-viewer');
  assert.deepEqual({ ...dispatched[0].detail }, { uuid: 'u1', name: 'beach.jpg', type: 'jpeg' });
});

function sentinel(ctx, ajax) {
  const el = { closest: () => 'scroll-root' };
  const s = ctx.timelineSentinel('/photos/timeline?cursor=c');
  s.$el = el;
  s.$ajax = ajax;
  return s;
}

test('the sentinel watches itself against the scroll container', () => {
  const { ctx, observers } = load();
  const s = sentinel(ctx, () => Promise.resolve());
  s.init();
  assert.equal(observers.length, 1);
  assert.equal(observers[0].options.root, 'scroll-root');
  assert.equal(observers[0].observed[0], s.$el);
  s.destroy();
  assert.equal(observers[0].disconnected, true);
});

test('coming into view loads the next page into both targets, once', async () => {
  const { ctx, observers } = load();
  const calls = [];
  let finish;
  const s = sentinel(ctx, (url, opts) => {
    calls.push([url, opts]);
    return new Promise((resolve) => { finish = resolve; });
  });
  s.init();
  observers[0].callback([{ isIntersecting: true }]);
  observers[0].callback([{ isIntersecting: true }]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/photos/timeline?cursor=c');
  assert.deepEqual(Array.from(calls[0][1].targets), ['timeline-grid', 'timeline-more']);
  assert.equal(s.loading, true);
  finish();
  await new Promise((r) => setImmediate(r));
  assert.equal(s.loading, false);
});

test('a failed load leaves the button usable for a retry', async () => {
  const { ctx } = load();
  const s = sentinel(ctx, () => Promise.reject(new Error('offline')));
  await s.load();
  assert.equal(s.loading, false);
});

test('leaving the view does not load', () => {
  const { ctx, observers } = load();
  let called = false;
  const s = sentinel(ctx, () => { called = true; return Promise.resolve(); });
  s.init();
  observers[0].callback([{ isIntersecting: false }]);
  assert.equal(called, false);
});

// ── Context menu and actions ─────────────────────────────

function tile(dataset = {}) {
  return {
    dataset: {
      uuid: 'u1',
      displayName: 'beach.jpg',
      fileType: 'jpeg',
      filesUrl: '/files/p1?open=u1',
      favorite: '0',
      ...dataset,
    },
  };
}

function fetchActionsReturning(payload) {
  const calls = [];
  return {
    calls,
    fileActions: {
      fetchActions(uuids) {
        calls.push(Array.from(uuids));
        return Promise.resolve(payload);
      },
    },
  };
}

const flush = () => new Promise((r) => setImmediate(r));

test('the menu keeps only the actions that mean something on a photo', async () => {
  const { fileActions, calls } = fetchActionsReturning({
    u1: [
      { id: 'view' }, { id: 'open_new_tab' }, { id: 'download' }, { id: 'toggle_favorite' },
      { id: 'toggle_pin' }, { id: 'cut' }, { id: 'share' }, { id: 'properties' }, { id: 'delete' },
    ],
  });
  const app = load({ fileActions }).ctx.photosApp();

  app.openCtxMenu({ clientX: 100, clientY: 100 }, tile());
  assert.equal(app.ctxMenu.actions, null);
  await flush();

  assert.deepEqual(calls, [['u1']]);
  assert.deepEqual(
    Array.from(app.ctxMenu.actions, (a) => a.id),
    ['view', 'download', 'toggle_favorite', 'share', 'properties', 'delete'],
  );
  assert.equal(app.ctxMenu.photo.filesUrl, '/files/p1?open=u1');
});

test('a slow answer for a previous photo does not fill the current menu', async () => {
  const pending = {};
  const fileActions = {
    fetchActions: (uuids) => new Promise((resolve) => { pending[uuids[0]] = resolve; }),
  };
  const app = load({ fileActions }).ctx.photosApp();

  app.openCtxMenu({ clientX: 1, clientY: 1 }, tile({ uuid: 'old' }));
  app.openCtxMenu({ clientX: 1, clientY: 1 }, tile({ uuid: 'new' }));
  pending.old({ old: [{ id: 'delete' }] });
  await flush();

  assert.equal(app.ctxMenu.actions, null);
  pending.new({ new: [{ id: 'view' }] });
  await flush();
  assert.deepEqual(Array.from(app.ctxMenu.actions, (a) => a.id), ['view']);
});

test('the "..." button anchors the menu under itself, inside the viewport', () => {
  const { fileActions } = fetchActionsReturning({});
  const app = load({ fileActions }).ctx.photosApp();
  const button = { getBoundingClientRect: () => ({ right: 1270, bottom: 880 }) };

  // A keyboard press reports no cursor position.
  app.openCtxMenu({ clientX: 0, clientY: 0 }, tile(), button);

  assert.equal(app.ctxMenu.x, 1270 - 224);
  assert.equal(app.ctxMenu.y, 900 - 340 - 4);
});

test('properties opens the Files panel and loads the tags once', () => {
  const requests = [];
  const app = load().ctx.photosApp();
  app.$el = { addEventListener() {} };
  app.$ajax = (url, opts) => requests.push([url, { ...opts }]);
  app.ctxMenu = { open: true, photo: { uuid: 'u1', name: 'beach.jpg' } };

  app.runCtxAction({ id: 'properties' });
  app.closePropertiesPanel();
  app.showProperties('u2', 'file');

  assert.equal(app.ctxMenu.open, false);
  assert.equal(app.showPropertiesPanel, true);
  assert.equal(app.propertiesUuid, 'u2');
  assert.equal(app.tagsLoaded, 1);
  assert.deepEqual(requests.map((r) => r[0]), ['/files/properties/u1', '/files/properties/u2']);
});

test('share and view hand the photo to the Files components', () => {
  const { ctx, dispatched } = load();
  const app = ctx.photosApp();
  const photo = { uuid: 'u1', name: 'beach.jpg', type: 'jpeg' };

  app.ctxMenu = { open: true, photo };
  app.runCtxAction({ id: 'share' });
  app.ctxMenu = { open: true, photo };
  app.runCtxAction({ id: 'view' });

  assert.deepEqual(
    dispatched.map((e) => [e.type, { ...e.detail }]),
    [
      ['open-share-modal', { uuid: 'u1', name: 'beach.jpg', nodeType: 'file' }],
      ['open-file-viewer', { uuid: 'u1', name: 'beach.jpg', type: 'jpeg' }],
    ],
  );
});

test('favoriting flips the tile star once the server agrees', async () => {
  const requests = [];
  const shown = tile();
  const app = load({
    fetch: (url, opts) => { requests.push([url, opts.method]); return Promise.resolve({ ok: true }); },
    getCSRFToken: () => 't',
    document: { getElementById: () => null, querySelector: () => shown },
  }).ctx.photosApp();

  await app.toggleFavorite('u1', false);
  assert.equal(shown.dataset.favorite, '1');
  await app.toggleFavorite('u1', true);
  assert.equal(shown.dataset.favorite, '0');
  assert.deepEqual(requests, [
    ['/api/v1/files/u1/favorite', 'POST'],
    ['/api/v1/files/u1/favorite', 'DELETE'],
  ]);
});

test('a refused favorite leaves the star alone', async () => {
  const errors = [];
  const shown = tile();
  const app = load({
    fetch: () => Promise.resolve({ ok: false }),
    getCSRFToken: () => 't',
    AppAlert: { error: (m) => errors.push(m) },
    document: { getElementById: () => null, querySelector: () => shown },
  }).ctx.photosApp();

  await app.toggleFavorite('u1', false);

  assert.equal(shown.dataset.favorite, '0');
  assert.equal(errors.length, 1);
});

function removableTile(day) {
  const el = tile();
  el.removed = false;
  el.closest = () => day;
  el.remove = () => { el.removed = true; day.tiles -= 1; };
  return el;
}

test('trashing removes the tile, and the day once it is empty', async () => {
  const day = { tiles: 1, removed: false, querySelector() { return this.tiles ? {} : null; } };
  day.remove = () => { day.removed = true; };
  const shown = removableTile(day);
  const requests = [];
  const app = load({
    AppDialog: { confirm: () => Promise.resolve(true) },
    fetch: (url, opts) => { requests.push([url, opts.method]); return Promise.resolve({ ok: true }); },
    getCSRFToken: () => 't',
    document: { getElementById: () => null, querySelector: () => shown },
  }).ctx.photosApp();
  app.showPropertiesPanel = true;
  app.propertiesUuid = 'u1';

  await app.trashPhoto('u1', 'beach.jpg');

  assert.deepEqual(requests, [['/api/v1/files/u1', 'DELETE']]);
  assert.equal(shown.removed, true);
  assert.equal(day.removed, true);
  assert.equal(app.showPropertiesPanel, false);
});

test('trashing asks first, and a cancel sends nothing', async () => {
  let called = false;
  const app = load({
    AppDialog: { confirm: () => Promise.resolve(false) },
    fetch: () => { called = true; return Promise.resolve({ ok: true }); },
  }).ctx.photosApp();

  await app.trashPhoto('u1', 'beach.jpg');

  assert.equal(called, false);
});

test('tag views of the shared partials point at the photos timeline', () => {
  const app = load({ location: { search: '?tag=t1' }, URLSearchParams }).ctx.photosApp();

  assert.equal(app.tagViewHref({ uuid: 't1' }), '/photos?tag=t1');
  assert.equal(app.isTagViewActive({ uuid: 't1' }), true);
  assert.equal(app.isTagViewActive({ uuid: 't2' }), false);
});

function starButton(dataset) {
  const shown = tile(dataset);
  return { shown, button: { closest: () => shown } };
}

test('the tile star toggles the favorite and flips itself', async () => {
  const requests = [];
  const { shown, button } = starButton({ canFavorite: '1', favorite: '1' });
  const app = load({
    fetch: (url, opts) => { requests.push([url, opts.method]); return Promise.resolve({ ok: true }); },
    getCSRFToken: () => 't',
    document: { getElementById: () => null, querySelector: () => shown },
  }).ctx.photosApp();

  await app.toggleTileFavorite(button);

  assert.deepEqual(requests, [['/api/v1/files/u1/favorite', 'DELETE']]);
  assert.equal(shown.dataset.favorite, '0');
  assert.equal(shown.dataset.busy, undefined);
});

test('the tile star does nothing where the registry did not offer it', async () => {
  let called = false;
  const { button } = starButton({ canFavorite: '0' });
  const app = load({
    fetch: () => { called = true; return Promise.resolve({ ok: true }); },
  }).ctx.photosApp();

  await app.toggleTileFavorite(button);

  assert.equal(called, false);
});

test('a second click while the first is on its way is ignored', async () => {
  let calls = 0;
  let finish;
  const { shown, button } = starButton({ canFavorite: '1' });
  const app = load({
    fetch: () => { calls += 1; return new Promise((r) => { finish = r; }); },
    getCSRFToken: () => 't',
    document: { getElementById: () => null, querySelector: () => shown },
  }).ctx.photosApp();

  const first = app.toggleTileFavorite(button);
  await app.toggleTileFavorite(button);
  finish({ ok: true });
  await first;

  assert.equal(calls, 1);
  assert.equal(shown.dataset.favorite, '1');
});

function withTileData(textContent) {
  return { getElementById: (id) => (id === 'photo-tile-data' ? { textContent } : null), querySelector: () => null };
}

test('the grid opens at the step and widths the page was rendered with', () => {
  const app = load({
    document: withTileData('{"size": 2, "widths": [96, 120, 144, 192, 256]}'),
  }).ctx.photosApp();

  assert.equal(app.tileSize, 2);
  assert.deepEqual({ ...app.tileStyle() }, { '--photo-tile': '120px' });
});

test('moving the slider resizes the tiles', () => {
  const app = load({
    document: withTileData('{"size": 3, "widths": [96, 120, 144, 192, 256]}'),
  }).ctx.photosApp();

  app.tileSize = 5;

  assert.deepEqual({ ...app.tileStyle() }, { '--photo-tile': '256px' });
});

test('without readable widths the root keeps the width the server rendered', () => {
  for (const document of [withTileData('not json'), { getElementById: () => null, querySelector: () => null }]) {
    assert.deepEqual({ ...load({ document }).ctx.photosApp().tileStyle() }, {});
  }
});

test('releasing the slider saves the step under the photos module', async () => {
  const requests = [];
  const app = load({
    document: withTileData('{"size": 3, "widths": [96, 120, 144, 192, 256]}'),
    fetch: (url, opts) => { requests.push([url, opts.method, opts.body]); return Promise.resolve({ ok: true }); },
    getCSRFToken: () => 't',
  }).ctx.photosApp();

  app.tileSize = 4;
  await app.saveTileSize();

  assert.deepEqual(requests, [['/api/v1/settings/photos/tile_size', 'PUT', '{"value":4}']]);
});

test('a refused save is swallowed', async () => {
  const app = load({
    fetch: () => Promise.reject(new Error('offline')),
    getCSRFToken: () => 't',
  }).ctx.photosApp();

  await app.saveTileSize();
});
