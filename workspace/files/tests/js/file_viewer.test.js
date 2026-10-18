'use strict';

// The shell every Files viewer presentation spreads (file_viewer.js): what an
// openFile does once its panel load settles, when another open went out
// meanwhile, and how it offers the favorite star and the page's properties
// panel.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function makeShell({ properties = false, favoriteOk = true } = {}) {
  const video = { currentTime: 0 };
  const panel = {
    blocked: false,
    replaceChildren() {},
    querySelector(selector) {
      if (selector === 'video') return video;
      if (selector === '[data-viewer-blocked]') return this.blocked ? {} : null;
      return null;
    },
  };
  const events = [];
  const actionCalls = [];
  const requests = [];
  const alerts = [];
  const ctx = loadScripts(
    [
      'workspace/files/ui/static/files/ui/js/viewer_panel.js',
      'workspace/files/ui/static/files/ui/js/file_viewer.js',
    ],
    {
      CustomEvent: class {
        constructor(type, opts) {
          this.type = type;
          this.detail = opts && opts.detail;
        }
      },
      dispatchEvent(event) {
        events.push(event);
      },
      fileActions: {
        fetchActions(uuids) {
          const answer = deferred();
          actionCalls.push({ uuids: Array.from(uuids), answer });
          return answer.promise;
        },
      },
      document: {
        getElementById: (id) => (id === 'viewer-panel' ? panel : null),
        querySelector: () => null,
      },
      fetch: async (url, opts) => {
        requests.push({ url, method: opts && opts.method });
        return { ok: favoriteOk };
      },
      getCSRFToken: () => 'token',
      AppAlert: { error: (message) => alerts.push(message) },
    },
  );
  const loads = [];
  const shell = {
    ...ctx.fileViewerShellMixin(),
    _propertiesEnabled: properties,
    _present() {},
    _dismiss() {},
    $ajax() {
      const load = deferred();
      loads.push(load);
      return load.promise;
    },
  };
  return { shell, loads, panel, video, events, actionCalls, requests, alerts };
}

// Lets every pending continuation of the shell run.
function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function shown(events, type) {
  return events.filter((e) => e.type === type).map((e) => ({ ...e.detail }));
}

test('a superseded open does not seek the video the newer one shows', async () => {
  const { shell, loads, video } = makeShell();

  const first = shell.openFile('a', 'a.webm', 'video', false, 30);
  const second = shell.openFile('b', 'b.webm', 'video', false, null);
  loads[1].resolve([{}]);
  await second;
  loads[0].resolve([{}]);
  await first;

  assert.equal(shell.fileUuid, 'b');
  assert.equal(video.currentTime, 0);
});

test('a superseded open does not report the newer panel as blocked or not', async () => {
  const { shell, loads, panel } = makeShell();

  const first = shell.openFile('a', 'a.exe', 'binary', false);
  const second = shell.openFile('b', 'b.exe', 'binary', false);
  panel.blocked = true;
  loads[1].resolve([{}]);
  await second;
  assert.equal(shell.viewerBlocked, true);
  // The first file's answer lands once the second one's panel was cleared
  // for a reload: it must not speak for it.
  panel.blocked = false;
  loads[0].resolve([{}]);
  await first;

  assert.equal(shell.viewerBlocked, true);
});

test('an open the viewer was closed under leaves the closed state alone', async () => {
  const { shell, loads, video } = makeShell();

  const opening = shell.openFile('a', 'a.webm', 'video', false, 30);
  shell._onClosed();
  loads[0].resolve([{}]);
  await opening;

  assert.equal(shell.open, false);
  assert.equal(video.currentTime, 0);
});

test('the open that wins applies its start time and blocked state', async () => {
  const { shell, loads, panel, video } = makeShell();
  panel.blocked = true;

  const opening = shell.openFile('a', 'a.webm', 'video', false, 30);
  loads[0].resolve([{}]);
  await opening;

  assert.equal(video.currentTime, 30);
  assert.equal(shell.viewerBlocked, true);
});

test('without properties the shell offers no properties, whatever the registry says', async () => {
  const { shell, actionCalls, events } = makeShell();

  shell.openFile('a', 'a.jpg', 'image', false);
  actionCalls[0].answer.resolve({ a: [{ id: 'properties' }] });
  await flush();

  assert.equal(shell.canShowProperties, false);
  shell.toggleProperties();
  assert.deepEqual(shown(events, 'open-properties'), []);
  assert.deepEqual(shown(events, 'viewer-file-shown'), []);
});

test('the star reads its state from the registry', async () => {
  const { shell, actionCalls } = makeShell();

  shell.openFile('a', 'a.jpg', 'image', false);
  assert.equal(shell.canFavorite, false);
  actionCalls[0].answer.resolve({
    a: [{ id: 'toggle_favorite', state: { is_favorite: true } }],
  });
  await flush();

  assert.equal(shell.canFavorite, true);
  assert.equal(shell.isFavorite, true);
});

test('a file the registry gives no favorite action gets no star', async () => {
  const { shell, actionCalls, requests } = makeShell();

  shell.openFile('a', 'a.jpg', 'image', false);
  actionCalls[0].answer.resolve({ a: [{ id: 'view' }] });
  await flush();
  await shell.toggleFavorite();

  assert.equal(shell.canFavorite, false);
  assert.deepEqual(requests, []);
});

test('starring the file tells the page, unstarring too', async () => {
  const { shell, actionCalls, requests, events } = makeShell();
  shell.openFile('a', 'a.jpg', 'image', false);
  actionCalls[0].answer.resolve({
    a: [{ id: 'toggle_favorite', state: { is_favorite: false } }],
  });
  await flush();

  await shell.toggleFavorite();
  assert.equal(shell.isFavorite, true);
  await shell.toggleFavorite();
  assert.equal(shell.isFavorite, false);

  assert.deepEqual(requests.map((r) => ({ ...r })), [
    { url: '/api/v1/files/a/favorite', method: 'POST' },
    { url: '/api/v1/files/a/favorite', method: 'DELETE' },
  ]);
  assert.deepEqual(shown(events, 'file-favorite-changed'), [
    { uuid: 'a', isFavorite: true },
    { uuid: 'a', isFavorite: false },
  ]);
});

test('a refused star keeps its state and says so', async () => {
  const { shell, actionCalls, events, alerts } = makeShell({ favoriteOk: false });
  shell.openFile('a', 'a.jpg', 'image', false);
  actionCalls[0].answer.resolve({
    a: [{ id: 'toggle_favorite', state: { is_favorite: false } }],
  });
  await flush();

  await shell.toggleFavorite();

  assert.equal(shell.isFavorite, false);
  assert.deepEqual(shown(events, 'file-favorite-changed'), []);
  assert.deepEqual(Array.from(alerts), ['Failed to update favorites']);
});

test('the properties button waits for the registry, then follows its answer', async () => {
  const { shell, actionCalls, events } = makeShell({ properties: true });

  shell.openFile('a', 'a.jpg', 'image', false);
  assert.equal(shell.canShowProperties, false);
  assert.deepEqual(actionCalls[0].uuids, ['a']);
  actionCalls[0].answer.resolve({ a: [{ id: 'view' }, { id: 'properties' }] });
  await flush();

  assert.equal(shell.canShowProperties, true);
  assert.deepEqual(shown(events, 'viewer-file-shown'), [{ uuid: 'a', properties: true }]);

  shell.toggleProperties();
  assert.deepEqual(shown(events, 'open-properties'), [{ uuid: 'a', nodeType: 'file' }]);
});

test('a file the registry refuses gets no properties button', async () => {
  const { shell, actionCalls, events } = makeShell({ properties: true });

  shell.openFile('a', 'a.jpg', 'image', false);
  actionCalls[0].answer.resolve(null);
  await flush();

  assert.equal(shell.canShowProperties, false);
  shell.toggleProperties();
  assert.deepEqual(shown(events, 'open-properties'), []);
  assert.deepEqual(shown(events, 'viewer-file-shown'), [{ uuid: 'a', properties: false }]);
});

test('an answer about a file the gallery left behind is dropped', async () => {
  const { shell, actionCalls, events } = makeShell({ properties: true });

  shell.openFile('a', 'a.jpg', 'image', false);
  shell.openFile('b', 'b.jpg', 'image', false);
  actionCalls[1].answer.resolve({ b: [] });
  actionCalls[0].answer.resolve({ a: [{ id: 'properties' }] });
  await flush();

  assert.equal(shell.canShowProperties, false);
  assert.deepEqual(shown(events, 'viewer-file-shown'), [{ uuid: 'b', properties: false }]);
});
