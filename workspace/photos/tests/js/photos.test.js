const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function load({ mobile = false, collapsed = false, observers = [] } = {}) {
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
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/photos.js', {
    sidebarPreference: { initial: () => collapsed, save: (m, v) => saved.push([m, v]) },
    matchMedia: () => ({ matches: mobile }),
    document: { getElementById: () => null },
    IntersectionObserver: FakeObserver,
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    dispatchEvent: (event) => dispatched.push(event),
  });
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
