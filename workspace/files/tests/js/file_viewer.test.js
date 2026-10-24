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
    location: null,
    replaceChildren() {},
    querySelector(selector) {
      if (selector === 'video') return video;
      if (selector === '[data-viewer-blocked]') return this.blocked ? {} : null;
      if (selector === '[data-viewer-location]' && this.location) {
        const attrs = this.location;
        return { getAttribute: (name) => (name in attrs ? attrs[name] : null) };
      }
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

function locationMarker(labels, url = '/files/f?open=a', space = 'personal') {
  return {
    'data-viewer-location': JSON.stringify(labels),
    'data-viewer-location-url': url,
    'data-viewer-location-space': space,
  };
}

test('the header reads the folder off the merged panel', async () => {
  const { shell, loads, panel } = makeShell();
  panel.location = locationMarker(['My Files', 'Photos', 'Brittany']);

  const opening = shell.openFile('a', 'a.jpg', 'image', false);
  assert.equal(shell.fileLocation, null);
  loads[0].resolve([{}]);
  await opening;

  assert.deepStrictEqual({ ...shell.fileLocation, labels: Array.from(shell.fileLocation.labels) }, {
    labels: ['My Files', 'Photos', 'Brittany'],
    url: '/files/f?open=a',
    space: 'personal',
  });
  assert.equal(shell.locationLabel(), 'My Files / Photos / Brittany');
  assert.equal(shell.locationTitle(), 'Show in Files: My Files / Photos / Brittany');
});

test('a superseded open does not name the folder of the newer file', async () => {
  const { shell, loads, panel } = makeShell();

  const first = shell.openFile('a', 'a.jpg', 'image', false);
  const second = shell.openFile('b', 'b.jpg', 'image', false);
  panel.location = locationMarker(['My Files', 'B']);
  loads[1].resolve([{}]);
  await second;
  panel.location = locationMarker(['My Files', 'A']);
  loads[0].resolve([{}]);
  await first;

  assert.equal(shell.locationLabel(), 'My Files / B');
});

test('a panel without a marker, or a closed viewer, names no folder', async () => {
  const { shell, loads, panel } = makeShell();

  const opening = shell.openFile('a', 'a.jpg', 'image', false);
  loads[0].resolve([{}]);
  await opening;
  assert.equal(shell.fileLocation, null);
  assert.equal(shell.locationLabel(), '');

  panel.location = locationMarker(['My Files']);
  const again = shell.openFile('b', 'b.jpg', 'image', false);
  loads[1].resolve([{}]);
  await again;
  shell._onClosed();
  assert.equal(shell.fileLocation, null);
});

test('a deep path keeps its root and the two folders nearest the file', async () => {
  const { shell, loads, panel } = makeShell();
  panel.location = locationMarker(['Family', 'Events', '2019', 'Wedding', 'Cake'], '', 'shared');

  const opening = shell.openFile('a', 'a.jpg', 'image', false);
  loads[0].resolve([{}]);
  await opening;

  assert.equal(shell.locationLabel(), 'Family / … / Wedding / Cake');
  assert.equal(shell.locationTitle(), 'Family / Events / 2019 / Wedding / Cake');
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

// The modal presentation (fileViewerModal): its items are not Files rows, so
// the gallery, the endpoints, the registry and the history are its options.
function makeModal(options = {}, { items = {}, location = 'http://x/chat/c' } = {}) {
  const panel = { replaceChildren() {}, querySelector: () => null };
  const actionCalls = [];
  const history = [];
  const listeners = {};
  // id -> [{uuid, name, type}], each a gallery container holding its items.
  const containers = {};
  for (const [id, list] of Object.entries(items)) {
    const rows = list.map((item) => ({
      offsetParent: {},
      dataset: {},
      getAttribute(attr) {
        return {
          'data-attachment-uuid': item.uuid,
          'data-attachment-name': item.name,
          'data-attachment-type': item.type,
        }[attr] ?? null;
      },
    }));
    containers[id] = {
      querySelectorAll: () => rows,
      querySelector(selector) {
        const uuid = /="([^"]+)"/.exec(selector)[1];
        return rows.find((r) => r.getAttribute('data-attachment-uuid') === uuid) || null;
      },
    };
  }
  const win = { href: location };
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
      dispatchEvent() {},
      addEventListener(type, handler) {
        listeners[type] = handler;
      },
      removeEventListener() {},
      attachSwipeNavigation: () => () => {},
      fileActions: {
        fetchActions(uuids) {
          actionCalls.push(Array.from(uuids));
          return new Promise(() => {});
        },
      },
      document: {
        getElementById: (id) => (id === 'viewer-panel' ? panel : containers[id] || null),
        querySelectorAll: () => [],
      },
      navHistory: {
        push: (url) => { history.push(['push', String(url)]); win.href = String(url); },
        replace: (url) => { history.push(['replace', String(url)]); win.href = String(url); },
        returnTo: (url) => { history.push(['returnTo', String(url)]); win.href = String(url); },
      },
      isValidUuid: () => true,
      URL,
      URLSearchParams,
    },
  );
  Object.defineProperty(ctx, 'location', { get: () => new URL(win.href) });
  const dialog = {
    open: false,
    showModal() { this.open = true; },
    close() { this.open = false; },
  };
  const loads = [];
  const modal = {
    ...ctx.fileViewerModal(options),
    $refs: { dialog },
    $ajax(url) {
      loads.push(url);
      return new Promise(() => {});
    },
  };
  modal.init();
  const opener = (detail) => listeners[options.event]({ detail });
  const restore = (href) => { win.href = href; return listeners['nav:restore'](); };
  return { modal, dialog, loads, actionCalls, history, opener, restore, listeners };
}

test('the modal opens on its own event and fills the endpoints with the uuid', () => {
  const { modal, dialog, loads } = makeModal({
    event: 'open-chat-attachment-viewer',
    viewerUrl: '/chat/view-attachment/{uuid}',
    downloadUrl: '/api/v1/chat/attachments/{uuid}',
  });
  modal.openFile('a', 'a.png', 'image/png', false);

  assert.equal(dialog.open, true);
  assert.deepEqual(Array.from(loads), ['/chat/view-attachment/a']);
  assert.equal(modal.downloadUrl, '/api/v1/chat/attachments/a');
});

test('the opener names the endpoints of the item it opens', () => {
  const { modal, loads, opener } = makeModal({
    event: 'open-task-attachment-viewer',
    viewerUrl: '/projects/view-attachment/{uuid}',
  });

  opener({ uuid: 'f', name: 'logo.png', type: 'image', viewerUrl: '/files/view/f', downloadUrl: '/dl/f' });

  assert.equal(modal.fileName, 'logo.png');
  assert.deepEqual(Array.from(loads), ['/files/view/f']);
  assert.equal(modal.downloadUrl, '/dl/f');
});

test('an item that is not a Files file asks the registry nothing', () => {
  const { modal, actionCalls, opener } = makeModal({ event: 'open' });

  opener({ uuid: 'a', name: 'a.png', type: 'image/png' });

  assert.equal(modal.filesItem, false);
  assert.equal(modal.canFavorite, false);
  assert.deepEqual(actionCalls, []);
});

test('an opener saying isFile brings the registry in for its item only', () => {
  const { modal, actionCalls, opener } = makeModal(
    { event: 'open', gallery: 'messages' },
    { items: { messages: [{ uuid: 'f', name: 'f.png' }, { uuid: 'g', name: 'g.png' }] } },
  );

  opener({ uuid: 'f', name: 'f.png', type: 'image', isFile: true });
  assert.equal(modal.filesItem, true);
  modal.navigateNext();

  assert.equal(modal.fileUuid, 'g');
  assert.equal(modal.filesItem, false);
  assert.deepEqual(actionCalls.map((uuids) => Array.from(uuids)), [['f']]);
});

test('the gallery is the element the opener names, else the default one', () => {
  const { modal, opener } = makeModal(
    { event: 'open', gallery: 'messages' },
    {
      items: {
        messages: [{ uuid: 'a', name: 'a.png' }, { uuid: 'b', name: 'b.png' }],
        thread: [{ uuid: 't1', name: 't1.png' }, { uuid: 't2', name: 't2.png' }],
      },
    },
  );

  opener({ uuid: 't1', name: 't1.png', galleryId: 'thread' });
  modal.navigateNext();
  assert.equal(modal.fileName, 't2.png');
  modal.navigateNext();
  assert.equal(modal.fileUuid, 't2');

  opener({ uuid: 'a', name: 'a.png' });
  modal.navigateNext();
  assert.equal(modal.fileName, 'b.png');
});

test('a modal without a gallery steps nowhere', () => {
  const { modal, opener } = makeModal({ event: 'open' });

  opener({ uuid: 'a', name: 'a.png' });

  assert.equal(modal._totalViewable(), 0);
  modal.navigateNext();
  assert.equal(modal.fileUuid, 'a');
});

test('without history, opening and closing write none', () => {
  const { modal, dialog, history, opener, listeners } = makeModal({ event: 'open' });

  opener({ uuid: 'a', name: 'a.png' });
  modal.close();

  assert.equal(dialog.open, false);
  assert.equal(modal.open, false);
  assert.deepEqual(history, []);
  assert.equal(listeners['nav:restore'], undefined);
});

test('with history, opening adds an entry, stepping rewrites it, closing steps back', () => {
  const { modal, history, opener } = makeModal(
    { event: 'open', gallery: 'messages', history: true },
    { items: { messages: [{ uuid: 'a', name: 'a.png' }, { uuid: 'b', name: 'b.png' }] } },
  );

  opener({ uuid: 'a', name: 'a.png' });
  modal.navigateNext();
  modal.close();

  assert.deepEqual(history.map((entry) => Array.from(entry)), [
    ['push', 'http://x/chat/c?open=a'],
    ['replace', 'http://x/chat/c?open=b'],
    ['returnTo', 'http://x/chat/c'],
  ]);
});

test('back closes the modal, forward reopens what the opener showed', async () => {
  const { modal, dialog, loads, opener, restore } = makeModal({
    event: 'open',
    viewerUrl: '/projects/view-attachment/{uuid}',
    history: true,
  });
  opener({ uuid: 'a', name: 'a.png', type: 'image/png', downloadUrl: '/dl/a' });

  await restore('http://x/chat/c');
  assert.equal(dialog.open, false);
  assert.equal(modal.open, false);

  await restore('http://x/chat/c?open=a');
  assert.equal(dialog.open, true);
  assert.equal(modal.fileName, 'a.png');
  assert.equal(modal.downloadUrl, '/dl/a');
  assert.deepEqual(Array.from(loads), ['/projects/view-attachment/a', '/projects/view-attachment/a']);
});

test('the dialog closing itself (Escape, backdrop) goes through close', () => {
  const { modal, dialog, history, opener } = makeModal({ event: 'open', history: true });
  opener({ uuid: 'a', name: 'a.png' });

  dialog.close();
  modal._onDialogClose();

  assert.equal(modal.open, false);
  assert.deepEqual(history.map((entry) => Array.from(entry)).at(-1), ['returnTo', 'http://x/chat/c']);
});

test('a close event landing after a reopen leaves the new item open', () => {
  const { modal, dialog, opener } = makeModal({ event: 'open' });
  opener({ uuid: 'a', name: 'a.png' });
  modal.close();
  opener({ uuid: 'b', name: 'b.png' });

  modal._onDialogClose();

  assert.equal(dialog.open, true);
  assert.equal(modal.fileUuid, 'b');
});
