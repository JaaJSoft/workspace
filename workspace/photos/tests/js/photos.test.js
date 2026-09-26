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
      'workspace/photos/ui/static/photos/ui/js/faces.js',
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

// ── Selection ────────────────────────────────────────────

// A page of tiles in display order, and the album the page shows (or none).
function grid(uuids, album = null) {
  const tiles = uuids.map((uuid) => tile({ uuid, selected: '0' }));
  const byUuid = Object.fromEntries(tiles.map((t) => [t.dataset.uuid, t]));
  return {
    tiles: byUuid,
    document: {
      getElementById: (id) => (id === 'album-data' && album ? { textContent: JSON.stringify(album) } : null),
      querySelector: (selector) => {
        const match = /data-uuid="([^"]+)"/.exec(selector);
        return match ? byUuid[match[1]] || null : null;
      },
      querySelectorAll: () => tiles,
    },
  };
}

test('photosRange spans both ends whichever comes first', () => {
  const { ctx } = load();
  const order = ['a', 'b', 'c', 'd'];
  assert.deepEqual(Array.from(ctx.photosRange(order, 'c', 'a')), ['a', 'b', 'c']);
  assert.deepEqual(Array.from(ctx.photosRange(order, 'b', 'd')), ['b', 'c', 'd']);
  assert.deepEqual(Array.from(ctx.photosRange(order, 'b', 'x')), []);
});

test('the check mark toggles a tile in and out of the selection', () => {
  const page = grid(['a', 'b', 'c']);
  const app = load({ document: page.document }).ctx.photosApp();

  app.toggleTileSelection(page.tiles.b, { shiftKey: false });
  assert.deepEqual(Array.from(app.selection), ['b']);
  assert.equal(page.tiles.b.dataset.selected, '1');

  app.toggleTileSelection(page.tiles.b, { shiftKey: false });
  assert.deepEqual(Array.from(app.selection), []);
  assert.equal(page.tiles.b.dataset.selected, '0');
});

test('shift selects every tile between the last one picked and this one', () => {
  const page = grid(['a', 'b', 'c', 'd', 'e']);
  const app = load({ document: page.document }).ctx.photosApp();

  app.toggleTileSelection(page.tiles.d, { shiftKey: false });
  app.toggleTileSelection(page.tiles.b, { shiftKey: true });

  assert.deepEqual(Array.from(app.selection).sort(), ['b', 'c', 'd']);
  assert.deepEqual(
    Object.values(page.tiles).map((t) => t.dataset.selected),
    ['0', '1', '1', '1', '0'],
  );
});

test('a shift-click with nothing picked before selects that tile alone', () => {
  const page = grid(['a', 'b', 'c']);
  const app = load({ document: page.document }).ctx.photosApp();

  app.toggleTileSelection(page.tiles.c, { shiftKey: true });

  assert.deepEqual(Array.from(app.selection), ['c']);
});

test('a click opens the viewer until something is selected, then selects', () => {
  const page = grid(['a', 'b']);
  const { ctx, dispatched } = load({ document: page.document });
  const app = ctx.photosApp();

  app.tileClicked({ shiftKey: false }, page.tiles.a);
  assert.equal(dispatched.length, 1);

  app.toggleTileSelection(page.tiles.a, { shiftKey: false });
  app.tileClicked({ shiftKey: false }, page.tiles.b);
  assert.equal(dispatched.length, 1);
  assert.deepEqual(Array.from(app.selection), ['a', 'b']);
});

test('clearing the selection unmarks every tile', () => {
  const page = grid(['a', 'b']);
  const app = load({ document: page.document }).ctx.photosApp();
  app.toggleTileSelection(page.tiles.a, { shiftKey: false });
  app.toggleTileSelection(page.tiles.b, { shiftKey: false });

  app.clearSelection();

  assert.deepEqual(Array.from(app.selection), []);
  assert.deepEqual(Object.values(page.tiles).map((t) => t.dataset.selected), ['0', '0']);
});

function longPress() {
  const timers = [];
  return {
    timers,
    globals: {
      setTimeout: (fn) => { timers.push(fn); return timers.length; },
      clearTimeout: () => {},
      navigator: {},
    },
  };
}

function touchEnd() {
  return { cancelable: true, prevented: false, preventDefault() { this.prevented = true; } };
}

test('a long press selects the tile and cancels the click its release would send', () => {
  const page = grid(['a']);
  const press = longPress();
  const app = load({ document: page.document, ...press.globals }).ctx.photosApp();

  app.startLongPress(page.tiles.a);
  press.timers[0]();
  const end = touchEnd();
  app.endLongPress(end);

  assert.deepEqual(Array.from(app.selection), ['a']);
  assert.equal(end.prevented, true);
});

test('a short touch lets its click through', () => {
  const page = grid(['a']);
  const press = longPress();
  const app = load({ document: page.document, ...press.globals }).ctx.photosApp();

  app.startLongPress(page.tiles.a);
  const end = touchEnd();
  app.endLongPress(end);

  assert.deepEqual(Array.from(app.selection), []);
  assert.equal(end.prevented, false);
});

test('the tap after a long press selects the next tile', () => {
  const page = grid(['a', 'b']);
  const press = longPress();
  const { ctx, dispatched } = load({ document: page.document, ...press.globals });
  const app = ctx.photosApp();

  app.startLongPress(page.tiles.a);
  press.timers[0]();
  app.endLongPress(touchEnd());
  app.tileClicked({ shiftKey: false }, page.tiles.b);

  assert.deepEqual(Array.from(app.selection), ['a', 'b']);
  assert.equal(dispatched.length, 0);
});

test('the contextmenu of a long press does not open the menu', () => {
  const { fileActions, calls } = fetchActionsReturning({ u1: [] });
  const app = load({ fileActions, setTimeout: () => 1, clearTimeout: () => {} }).ctx.photosApp();

  app.startLongPress(tile());
  app.openCtxMenu({ clientX: 1, clientY: 1 }, tile());

  assert.equal(app.ctxMenu.open, false);
  assert.equal(calls.length, 0);
});

test('a navigation that replaces the listing drops the selection', () => {
  const page = grid(['a']);
  const app = load({ document: page.document }).ctx.photosApp();
  app.toggleTileSelection(page.tiles.a, { shiftKey: false });

  app.onMerged({ target: { id: 'timeline-grid' } });
  assert.equal(app.selection.length, 1);
  app.onMerged({ target: { id: 'photos-content' } });
  assert.equal(app.selection.length, 0);
});

// ── Albums ───────────────────────────────────────────────

function jsonFetch(routes, requests = []) {
  return (url, opts) => {
    requests.push([opts.method, url, opts.body ? JSON.parse(opts.body) : undefined]);
    const body = typeof routes === 'function' ? routes(url, opts) : routes[`${opts.method} ${url}`];
    return Promise.resolve({ ok: true, status: body === null ? 204 : 200, json: () => Promise.resolve(body) });
  };
}

test('the album on screen and what its registry allows there', async () => {
  const page = grid(['a'], { uuid: 'al1', title: 'Summer', sort_mode: 'manual' });
  const app = load({
    document: page.document,
    getCSRFToken: () => 't',
    fetch: jsonFetch({ 'POST /api/v1/photos/albums/actions': { al1: [{ id: 'remove_items' }, { id: 'reorder' }] } }),
  }).ctx.photosApp();

  await app.syncAlbum();

  assert.equal(app.albumUuid, 'al1');
  assert.equal(app.albumAllows('remove_items'), true);
  assert.equal(app.albumAllows('set_cover'), false);
});

test('outside an album nothing is allowed and nothing is asked', async () => {
  const requests = [];
  const app = load({ fetch: jsonFetch({}, requests) }).ctx.photosApp();

  await app.syncAlbum();

  assert.equal(app.albumUuid, null);
  assert.equal(app.albumAllows('remove_items'), false);
  assert.equal(requests.length, 0);
});

test('the picker lists only the albums the viewer may add to', async () => {
  const dialog = { open: false, showModal() { this.open = true; }, close() { this.open = false; } };
  const app = load({
    document: { getElementById: (id) => (id === 'album-picker' ? dialog : null), querySelector: () => null },
    getCSRFToken: () => 't',
    fetch: jsonFetch({
      'GET /api/v1/photos/albums': [{ uuid: 'x', title: 'Mine' }, { uuid: 'y', title: 'Read only' }],
      'POST /api/v1/photos/albums/actions': { x: [{ id: 'add_items' }], y: [{ id: 'rename' }] },
    }),
  }).ctx.photosApp();

  await app.openAlbumPicker(['a', 'b']);

  assert.equal(dialog.open, true);
  assert.deepEqual(Array.from(app.picker.files), ['a', 'b']);
  assert.deepEqual(Array.from(app.picker.albums, (a) => a.uuid), ['x']);
  assert.equal(app.picker.loading, false);
});

test('adding posts the photos, closes the picker and clears the selection', async () => {
  const page = grid(['a', 'b']);
  const dialog = { open: true, close() { this.open = false; } };
  const requests = [];
  const alerts = [];
  const app = load({
    document: { ...page.document, getElementById: (id) => (id === 'album-picker' ? dialog : null) },
    getCSRFToken: () => 't',
    fetch: jsonFetch({ 'POST /api/v1/photos/albums/x/items': { added: 2 } }, requests),
    AppAlert: { success: (m) => alerts.push(m), error: (m) => alerts.push(m) },
    location: { href: '/photos' },
  }).ctx.photosApp();
  app.$ajax = () => Promise.resolve();
  app.toggleTileSelection(page.tiles.a, { shiftKey: false });
  app.toggleTileSelection(page.tiles.b, { shiftKey: false });
  app.picker.files = app.selection.slice();

  await app.addToAlbum({ uuid: 'x', title: 'Summer' });

  assert.deepEqual(requests.map((r) => [r[0], r[1], r[2]]), [
    ['POST', '/api/v1/photos/albums/x/items', { files: ['a', 'b'] }],
  ]);
  assert.equal(dialog.open, false);
  assert.equal(app.selection.length, 0);
  assert.deepEqual(alerts, ['Added 2 photos to "Summer"']);
});

test('removing from the album takes the tiles off the page', async () => {
  const day = { querySelector: () => null, remove() { this.removed = true; } };
  const page = grid(['a', 'b'], { uuid: 'al1' });
  for (const t of Object.values(page.tiles)) {
    t.closest = () => day;
    t.remove = () => { t.removed = true; };
  }
  const requests = [];
  const app = load({
    document: page.document,
    getCSRFToken: () => 't',
    fetch: jsonFetch((url) => (url.endsWith('/actions') ? { al1: [{ id: 'remove_items' }] } : { removed: 1 }), requests),
    AppAlert: { success() {}, error() {} },
    location: { href: '/photos/albums/al1' },
  }).ctx.photosApp();
  app.$ajax = () => Promise.resolve();
  await app.syncAlbum();

  await app.removeFromAlbum(['a']);

  assert.deepEqual(requests[1].slice(1), ['/api/v1/photos/albums/al1/items/remove', { files: ['a'] }]);
  assert.equal(page.tiles.a.removed, true);
  assert.equal(page.tiles.b.removed, undefined);
});

test('removing does nothing where the registry did not offer it', async () => {
  const requests = [];
  const page = grid(['a'], { uuid: 'al1' });
  const app = load({
    document: page.document,
    getCSRFToken: () => 't',
    fetch: jsonFetch({ 'POST /api/v1/photos/albums/actions': { al1: [] } }, requests),
  }).ctx.photosApp();
  await app.syncAlbum();

  await app.removeFromAlbum(['a']);

  assert.equal(requests.length, 1);
});

// ── Manual order ─────────────────────────────────────────

function dataTransfer() {
  return { effectAllowed: 'uninitialized', dropEffect: 'none', setData() {} };
}

test('a drag declares move, and the tile it passes over accepts it as move', async () => {
  const page = grid(['a', 'b', 'c'], { uuid: 'al1' });
  const app = load({
    document: page.document,
    getCSRFToken: () => 't',
    fetch: jsonFetch({ 'POST /api/v1/photos/albums/actions': { al1: [{ id: 'reorder' }] } }),
  }).ctx.photosApp();
  await app.syncAlbum();
  const start = { dataTransfer: dataTransfer(), preventDefault() { this.prevented = true; } };
  const over = { dataTransfer: dataTransfer(), clientX: 90, preventDefault() { this.prevented = true; } };
  page.tiles.c.getBoundingClientRect = () => ({ left: 0, width: 100 });

  app.startTileDrag(start, page.tiles.a);
  app.overTileDrag(over, page.tiles.c);

  assert.equal(start.dataTransfer.effectAllowed, 'move');
  assert.equal(over.dataTransfer.dropEffect, 'move');
  assert.equal(over.prevented, true);
  assert.equal(page.tiles.c.dataset.dropSide, 'after');
});

test('a drag from a selected tile carries the selection, in page order', async () => {
  const page = grid(['a', 'b', 'c', 'd'], { uuid: 'al1' });
  const requests = [];
  const app = load({
    document: page.document,
    getCSRFToken: () => 't',
    fetch: jsonFetch((url) => (url.endsWith('/actions') ? { al1: [{ id: 'reorder' }] } : null), requests),
  }).ctx.photosApp();
  await app.syncAlbum();
  app.toggleTileSelection(page.tiles.c, { shiftKey: false });
  app.toggleTileSelection(page.tiles.a, { shiftKey: false });
  const target = page.tiles.d;
  target.getBoundingClientRect = () => ({ left: 0, width: 100 });
  target.after = (...moved) => { target.inserted = moved.map((t) => t.dataset.uuid); };

  app.startTileDrag({ dataTransfer: dataTransfer() }, page.tiles.a);
  await app.dropTileDrag({ clientX: 80, preventDefault() {} }, target);

  assert.deepEqual(requests[1].slice(1), [
    '/api/v1/photos/albums/al1/reorder', { files: ['a', 'c'], after: 'd' },
  ]);
  assert.deepEqual(target.inserted, ['a', 'c']);
});

test('no drag starts where the registry does not offer reorder', async () => {
  const page = grid(['a'], { uuid: 'al1' });
  const app = load({
    document: page.document,
    getCSRFToken: () => 't',
    fetch: jsonFetch({ 'POST /api/v1/photos/albums/actions': { al1: [] } }),
  }).ctx.photosApp();
  await app.syncAlbum();
  const start = { dataTransfer: dataTransfer(), preventDefault() { this.prevented = true; } };

  app.startTileDrag(start, page.tiles.a);

  assert.equal(start.prevented, true);
  assert.equal(app._drag, null);
});

test('a long press the browser cancels does not swallow the next tap', () => {
  const page = grid(['a', 'b']);
  const press = longPress();
  const app = load({ document: page.document, ...press.globals }).ctx.photosApp();

  app.startLongPress(page.tiles.a);
  press.timers[0]();
  // The press ends without a touchend: no endLongPress to clear it.
  app.cancelLongPress();
  app.startLongPress(page.tiles.b);
  const end = touchEnd();
  app.endLongPress(end);

  assert.equal(end.prevented, false);
});

test('a refresh waits for the one before it, so it never reuses a response rendered before its write', async () => {
  const app = load({ location: { href: '/photos/albums/al1' } }).ctx.photosApp();
  const sent = [];
  const answers = [];
  app.$ajax = (url, opts) => {
    sent.push(Array.from(opts.targets));
    return new Promise((resolve) => answers.push(resolve));
  };

  const first = app._refresh(['photos-nav', 'photos-header']);
  const second = app._refresh(['photos-header']);
  await flush();

  // The second one is not on the wire while the first is: the page's ajax
  // layer would hand it the first one's in-flight response.
  assert.equal(sent.length, 1);
  answers[0]();
  await first;
  await flush();
  assert.deepEqual(sent, [['photos-nav', 'photos-header'], ['photos-header']]);
  answers[1]();
  await second;
});

test('a failed refresh does not block the next one', async () => {
  const app = load({ location: { href: '/photos' } }).ctx.photosApp();
  let calls = 0;
  app.$ajax = () => {
    calls += 1;
    return calls === 1 ? Promise.reject(new Error('offline')) : Promise.resolve();
  };

  await app._refresh(['photos-nav']);
  await app._refresh(['photos-nav']);

  assert.equal(calls, 2);
});

test('the tile checkbox follows the selection, ranges and clearing included', () => {
  const page = grid(['a', 'b', 'c']);
  const boxes = {};
  for (const [uuid, t] of Object.entries(page.tiles)) {
    boxes[uuid] = { checked: false };
    t.querySelector = (sel) => (sel === '[data-select]' ? boxes[uuid] : null);
  }
  const app = load({ document: page.document }).ctx.photosApp();

  app.toggleTileSelection(page.tiles.a, { shiftKey: false });
  app.toggleTileSelection(page.tiles.c, { shiftKey: true });
  assert.deepEqual(Object.values(boxes).map((b) => b.checked), [true, true, true]);

  app.toggleTileSelection(page.tiles.b, { shiftKey: false });
  assert.deepEqual(Object.values(boxes).map((b) => b.checked), [true, false, true]);

  app.clearSelection();
  assert.deepEqual(Object.values(boxes).map((b) => b.checked), [false, false, false]);
});

// ── Selection actions ────────────────────────────────────

const FAVORITE = (isFavorite) => ({ id: 'toggle_favorite', bulk: true, state: { is_favorite: isFavorite } });
const BULK = (id) => ({ id, bulk: true });

test('the selection offers the bulk actions every selected photo offers', () => {
  const { ctx } = load();
  const actions = ctx.photosSelectionActions([
    [FAVORITE(false), BULK('download'), BULK('delete'), { id: 'rename', bulk: false }, BULK('cut')],
    [FAVORITE(true), BULK('download')],
  ]);

  assert.deepEqual(Array.from(actions, (a) => a.id), ['toggle_favorite', 'download']);
  assert.equal(actions[0].add, true);
});

test('the favorite toggle removes once every selected photo is a favorite', () => {
  const { ctx } = load();
  const [toggle] = ctx.photosSelectionActions([[FAVORITE(true)], [FAVORITE(true)]]);

  assert.equal(toggle.add, false);
  assert.equal(ctx.photosSelectionActions([]).length, 0);
});

test('the registry is asked in slices of 200, once per photo', async () => {
  const uuids = Array.from({ length: 450 }, (_, i) => `u${i}`);
  const calls = [];
  const fileActions = {
    fetchActions(batch) {
      calls.push(batch.length);
      return Promise.resolve(Object.fromEntries(batch.map((u) => [u, [BULK('download')]])));
    },
  };
  const app = load({ fileActions }).ctx.photosApp();

  app.selection = uuids;
  const loading = app._loadSelectionActions();
  assert.equal(app.selectionActions, null);
  await loading;
  assert.deepEqual(calls, [200, 200, 50]);
  assert.equal(app.selectionAllows('download'), true);

  app.selection = uuids.slice(0, 10);
  await app._loadSelectionActions();
  assert.deepEqual(calls, [200, 200, 50]);
});

test('an answer about a selection that has changed since is dropped', async () => {
  const pending = [];
  const fileActions = {
    fetchActions: (batch) => new Promise((resolve) => pending.push(() => resolve(
      Object.fromEntries(batch.map((u) => [u, u === 'a' ? [BULK('delete')] : [BULK('download')]])),
    ))),
  };
  const app = load({ fileActions }).ctx.photosApp();

  app.selection = ['a'];
  const first = app._loadSelectionActions();
  app.selection = ['b'];
  const second = app._loadSelectionActions();
  pending[1]();
  await second;
  pending[0]();
  await first;

  assert.deepEqual(Array.from(app.selectionActions, (a) => a.id), ['download']);
});

test('a refused answer offers nothing', async () => {
  const app = load({ fileActions: { fetchActions: () => Promise.resolve(null) } }).ctx.photosApp();
  app.selection = ['a'];
  await app._loadSelectionActions();
  assert.deepEqual(Array.from(app.selectionActions), []);
});

test('select all picks every loaded tile, in page order', () => {
  const page = grid(['a', 'b', 'c']);
  const app = load({ document: page.document }).ctx.photosApp();
  app.toggleTileSelection(page.tiles.b, null);

  app.selectAll();

  assert.deepEqual(Array.from(app.selection), ['a', 'b', 'c']);
  assert.ok(Object.values(page.tiles).every((t) => t.dataset.selected === '1'));
});

test('a right-click on a tile of a multiple selection opens the selection menu', () => {
  const page = grid(['a', 'b', 'c']);
  const { fileActions, calls } = fetchActionsReturning({});
  const app = load({ document: page.document, fileActions }).ctx.photosApp();
  app.toggleTileSelection(page.tiles.a, null);
  app.toggleTileSelection(page.tiles.b, null);

  app.openCtxMenu({ clientX: 50, clientY: 60 }, page.tiles.b);
  assert.equal(app.selectionMenu.open, true);
  assert.equal(app.ctxMenu.open, false);
  assert.deepEqual(calls, []);

  // A tile outside the selection keeps its own menu.
  app.openCtxMenu({ clientX: 50, clientY: 60 }, page.tiles.c);
  assert.equal(app.selectionMenu.open, false);
  assert.equal(app.ctxMenu.open, true);
  assert.equal(app.ctxMenu.photo.uuid, 'c');
});

test('a single selected tile keeps its own menu', () => {
  const page = grid(['a']);
  const { fileActions } = fetchActionsReturning({});
  const app = load({ document: page.document, fileActions }).ctx.photosApp();
  app.toggleTileSelection(page.tiles.a, null);

  app.openCtxMenu({ clientX: 50, clientY: 60 }, page.tiles.a);

  assert.equal(app.selectionMenu.open, false);
  assert.equal(app.ctxMenu.open, true);
});

test('favoriting the selection flips the stars it could and reports the rest', async () => {
  const page = grid(['a', 'b', 'c']);
  const requests = [];
  const alerts = [];
  const app = load({
    document: page.document,
    getCSRFToken: () => 't',
    fetch: (url, opts) => {
      requests.push([opts.method, url]);
      return Promise.resolve({ ok: !url.includes('/b/') });
    },
    AppAlert: { success: (m) => alerts.push(['success', m]), warning: (m) => alerts.push(['warning', m]) },
  }).ctx.photosApp();
  app.selectAll();
  app.selectionActions = [{ ...FAVORITE(false), add: true }];
  app._actionsCache = { a: [], b: [], c: [] };

  await app.favoriteSelection();

  assert.deepEqual(requests.map((r) => r[0]), ['POST', 'POST', 'POST']);
  assert.equal(page.tiles.a.dataset.favorite, '1');
  assert.equal(page.tiles.b.dataset.favorite, '0');
  assert.deepEqual(Object.keys(app._actionsCache), ['b']);
  assert.deepEqual(alerts, [['warning', 'Added 2 photos to favorites, 1 failed']]);
  assert.equal(app.selection.length, 0);
  assert.equal(app.selectionBusy, false);
});

test('unfavoriting the selection sends deletes', async () => {
  const page = grid(['a']);
  const requests = [];
  const app = load({
    document: page.document,
    getCSRFToken: () => 't',
    fetch: (url, opts) => { requests.push([opts.method, url]); return Promise.resolve({ ok: true }); },
    AppAlert: { success() {} },
  }).ctx.photosApp();
  app.selectAll();
  app.selectionActions = [{ ...FAVORITE(true), add: false }];

  await app.favoriteSelection();

  assert.deepEqual(requests, [['DELETE', '/api/v1/files/a/favorite']]);
  assert.equal(page.tiles.a.dataset.favorite, '0');
});

test('nothing is sent for an action the registry did not offer the whole selection', async () => {
  let called = false;
  const page = grid(['a']);
  const app = load({
    document: page.document,
    fetch: () => { called = true; return Promise.resolve({ ok: true }); },
    AppDialog: { confirm: () => { called = true; return Promise.resolve(true); } },
    fileActions: { downloadArchive: () => { called = true; } },
  }).ctx.photosApp();
  app.selectAll();
  app.selectionActions = [];

  await app.favoriteSelection();
  await app.trashSelection();
  await app.downloadSelection();

  assert.equal(called, false);
});

test('downloading hands the selection to the archive helper', async () => {
  const archives = [];
  const page = grid(['a', 'b']);
  const app = load({
    document: page.document,
    fileActions: { downloadArchive: (uuids, name) => { archives.push([Array.from(uuids), name]); } },
  }).ctx.photosApp();
  app.selectAll();
  app.selectionActions = [BULK('download')];

  await app.downloadSelection();

  assert.deepEqual(archives, [[['a', 'b'], 'photos.zip']]);
});

test('trashing the selection removes the tiles the server accepted', async () => {
  const page = grid(['a', 'b']);
  const day = { querySelector: () => ({}), remove() {} };
  for (const t of Object.values(page.tiles)) {
    t.closest = () => day;
    t.remove = () => { t.removed = true; };
  }
  // The grid still holds a tile: the header is refreshed, not the listing.
  const byUuid = page.document.querySelector;
  page.document.querySelector = (selector) => (
    selector === '#timeline-grid [data-uuid]' ? page.tiles.b : byUuid(selector)
  );
  const requests = [];
  const refreshed = [];
  const app = load({
    document: page.document,
    getCSRFToken: () => 't',
    AppDialog: { confirm: () => Promise.resolve(true) },
    fetch: (url, opts) => { requests.push([opts.method, url]); return Promise.resolve({ ok: url.endsWith('/a') }); },
    AppAlert: { warning() {} },
    location: { href: '/photos' },
  }).ctx.photosApp();
  app.$ajax = (url, opts) => { refreshed.push(Array.from(opts.targets)); return Promise.resolve(); };
  app.selectAll();
  app.selectionActions = [BULK('delete')];
  app.showPropertiesPanel = true;
  app.propertiesUuid = 'a';

  await app.trashSelection();
  await app._refreshing;

  assert.deepEqual(requests, [['DELETE', '/api/v1/files/a'], ['DELETE', '/api/v1/files/b']]);
  assert.equal(page.tiles.a.removed, true);
  assert.equal(page.tiles.b.removed, undefined);
  assert.equal(app.showPropertiesPanel, false);
  assert.equal(app.selection.length, 0);
  assert.deepEqual(refreshed, [['photos-nav', 'photos-header']]);
});

test('trashing the selection asks first, and a cancel sends nothing', async () => {
  let called = false;
  const page = grid(['a']);
  const app = load({
    document: page.document,
    AppDialog: { confirm: () => Promise.resolve(false) },
    fetch: () => { called = true; return Promise.resolve({ ok: true }); },
  }).ctx.photosApp();
  app.selectAll();
  app.selectionActions = [BULK('delete')];

  await app.trashSelection();

  assert.equal(called, false);
  assert.equal(app.selection.length, 1);
});
