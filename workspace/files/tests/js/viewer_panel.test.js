'use strict';

// The shared viewer panel loader (notes editor, files viewer modal, chat
// attachment viewer modal): all viewer HTML goes through alpine-ajax into
// #viewer-panel. Content staleness is arbitrated by alpine-ajax itself
// (newest request per target wins) - these tests pin the component-side
// contract around it: cleanup fires before each load, a superseded load
// never touches the flags the winning load owns, and a response without
// the panel id is refused instead of deleting the live panel.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeHost() {
  const events = [];
  const panel = {
    children: ['old'],
    replaceChildren() { this.children = []; },
  };
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/viewer_panel.js', {
    CustomEvent: class {
      constructor(type, opts) {
        this.type = type;
        this.detail = opts && opts.detail;
      }
    },
    document: { getElementById: (id) => (id === 'viewer-panel' ? panel : null) },
  });
  ctx.dispatchEvent = (e) => { events.push(e.type); };

  const ajaxCalls = [];
  const host = {
    ...ctx.viewerPanelMixin(),
    $ajax(url, options) {
      ajaxCalls.push({ url, options });
      events.push('ajax');
      return this._ajaxResult;
    },
  };
  return { host, events, ajaxCalls, panel };
}

test('a load clears the panel (after cleanup) and merges through alpine-ajax', async () => {
  const { host, events, ajaxCalls, panel } = makeHost();
  host._ajaxResult = Promise.resolve([{}]);

  const merged = await host.loadViewerPanel('/files/view/abc');

  assert.equal(merged, true);
  assert.equal(host.viewerLoading, false);
  assert.equal(host.viewerError, null);
  // Cleanup must reach the mounted editor BEFORE its DOM goes away and
  // before the next request is issued.
  assert.deepStrictEqual(Array.from(events), ['viewer-cleanup', 'ajax']);
  assert.deepStrictEqual(Array.from(panel.children), []);
  assert.equal(ajaxCalls[0].url, '/files/view/abc');
  assert.deepStrictEqual({ ...ajaxCalls[0].options }, {
    target: 'viewer-panel',
    focus: false,
  });
});

test('a superseded load leaves the flags to the load that replaced it', async () => {
  const { host } = makeHost();

  let resolveFirst;
  host._ajaxResult = new Promise((resolve) => { resolveFirst = resolve; });
  const first = host.loadViewerPanel('/files/view/aaa');

  let resolveSecond;
  host._ajaxResult = new Promise((resolve) => { resolveSecond = resolve; });
  const second = host.loadViewerPanel('/files/view/bbb');

  // The first response arrives late: alpine-ajax merged nothing for it
  // (renders resolve empty), and it must not clear the second's spinner.
  resolveFirst([]);
  assert.equal(await first, false);
  assert.equal(host.viewerLoading, true);
  assert.equal(host.viewerError, null);

  resolveSecond([{}]);
  assert.equal(await second, true);
  assert.equal(host.viewerLoading, false);
});

test('a superseded failure cannot paint an error over the winning load', async () => {
  const { host } = makeHost();

  let rejectFirst;
  host._ajaxResult = new Promise((_, reject) => { rejectFirst = reject; });
  const first = host.loadViewerPanel('/files/view/aaa');

  host._ajaxResult = new Promise(() => {});
  host.loadViewerPanel('/files/view/bbb');

  rejectFirst(new Error('network down'));
  assert.equal(await first, false);
  assert.equal(host.viewerError, null);
  assert.equal(host.viewerLoading, true);
});

test('a response without the panel id becomes an error, not a merge', async () => {
  const { host } = makeHost();
  // The guarded ajax:missing path: the render slot resolves empty.
  host._ajaxResult = Promise.resolve([undefined]);

  const merged = await host.loadViewerPanel('/files/view/abc');

  assert.equal(merged, false);
  assert.equal(host.viewerError, 'Failed to load viewer');
  assert.equal(host.viewerLoading, false);
});

test('a request failure surfaces its message', async () => {
  const { host } = makeHost();
  host._ajaxResult = Promise.reject(new Error('boom'));

  const merged = await host.loadViewerPanel('/files/view/abc');

  assert.equal(merged, false);
  assert.equal(host.viewerError, 'boom');
  assert.equal(host.viewerLoading, false);
});

test('the missing-target guard only cancels for the viewer panel', () => {
  const { host } = makeHost();

  let prevented = false;
  host._viewerMissing({
    detail: { target: { id: 'viewer-panel' } },
    preventDefault() { prevented = true; },
  });
  assert.equal(prevented, true);

  prevented = false;
  host._viewerMissing({
    detail: { target: { id: 'message-list' } },
    preventDefault() { prevented = true; },
  });
  assert.equal(prevented, false);
});

test('clearViewerPanel disposes the viewer and keeps the anchor element', () => {
  const { host, events, panel } = makeHost();

  host.clearViewerPanel();

  assert.deepStrictEqual(Array.from(events), ['viewer-cleanup']);
  assert.deepStrictEqual(Array.from(panel.children), []);
});

test('teardown cancels the in-flight load: its completion leaves the flags alone', async () => {
  const { host, events } = makeHost();

  let resolveLoad;
  host._ajaxResult = new Promise((resolve) => { resolveLoad = resolve; });
  const load = host.loadViewerPanel('/files/view/aaa');
  assert.equal(host.viewerLoading, true);

  host.teardownViewerPanel();
  assert.equal(host.viewerLoading, false);
  // The teardown's own cleanup dispatch, on top of the load's.
  assert.deepStrictEqual(Array.from(events), ['viewer-cleanup', 'ajax', 'viewer-cleanup']);

  resolveLoad([]);
  assert.equal(await load, false);
  assert.equal(host.viewerLoading, false);
  assert.equal(host.viewerError, null);
});

test('the merge guard refuses a response only for a canceled load', async () => {
  const { host } = makeHost();

  // An issued, still-active load merges normally.
  host._ajaxResult = Promise.resolve([{}]);
  await host.loadViewerPanel('/files/view/aaa');
  let prevented = false;
  const event = {
    target: { id: 'viewer-panel' },
    preventDefault() { prevented = true; },
  };
  host._viewerMerge(event);
  assert.equal(prevented, false);

  // After a cancel, the same load's late response must not mount.
  host.cancelViewerLoad();
  host._viewerMerge(event);
  assert.equal(prevented, true);

  // Merges for other targets bubbling through the same root pass through
  // even while a viewer load stands canceled.
  prevented = false;
  host._viewerMerge({
    target: { id: 'notes-sidebar' },
    preventDefault() { prevented = true; },
  });
  assert.equal(prevented, false);
});
