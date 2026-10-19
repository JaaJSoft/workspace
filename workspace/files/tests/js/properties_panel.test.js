const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

const U1 = '0190a6b2-0000-7000-8000-000000000001';
const U2 = '0190a6b2-0000-7000-8000-000000000002';

// navHistory as far as the panel sees it: an address, and the writes it gets.
function fakeHistory(ctx, address) {
  const writes = [];
  ctx.location = new URL(address, 'http://testserver');
  const go = (url) => { ctx.location = new URL(url, 'http://testserver'); };
  const path = (url) => { const u = new URL(url, 'http://testserver'); return u.pathname + u.search; };
  ctx.navHistory = {
    push(url) { writes.push(['push', path(url)]); go(url); },
    replace(url) { writes.push(['replace', path(url)]); go(url); },
    returnTo(url) { writes.push(['returnTo', path(url)]); go(url); },
    state: () => ({}),
  };
  return writes;
}

function panel(address = '/files') {
  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/uuid.js',
      'workspace/files/ui/static/files/ui/js/properties_panel.js',
    ],
    { URL, URLSearchParams, document: { querySelector: () => null } },
  );
  const history = fakeHistory(ctx, address);
  const listeners = {};
  const requests = [];
  const host = ctx.propertiesPanelMixin();
  host.$el = {
    addEventListener(type, fn, opts) {
      assert.deepEqual({ ...opts }, { once: true });
      listeners[type] = fn;
    },
  };
  host.$ajax = (url, opts) => requests.push([url, { ...opts }]);
  return { ctx, host, listeners, requests, history };
}

test('opening loads the file properties into the panel', () => {
  const { host, requests } = panel();

  host.openPropertiesPanel('u1', 'file');

  assert.equal(host.showPropertiesPanel, true);
  assert.equal(host.propertiesUuid, 'u1');
  assert.equal(host.propertiesLoading, true);
  assert.deepEqual(requests, [['/files/properties/u1', { target: 'properties-content' }]]);
});

test('the node type defaults to file', () => {
  const { host } = panel();

  host.openPropertiesPanel('u1');

  assert.equal(host.propertiesNodeType, 'file');
});

test('the loading flag clears after the swap, and an error is reported', () => {
  const { host, listeners } = panel();
  host.openPropertiesPanel('u1', 'folder');

  listeners['ajax:error']();
  listeners['ajax:after']();

  assert.equal(host.propertiesLoading, false);
  assert.equal(host.propertiesError, 'Failed to load properties');
});

test('asking again for the file already shown closes the panel', () => {
  const { host, requests } = panel();
  host.openPropertiesPanel('u1', 'file');
  host.selectedFile = { uuid: 'u1', tags: [] };

  host.openPropertiesPanel('u1', 'file');

  assert.equal(host.showPropertiesPanel, false);
  assert.equal(host.propertiesUuid, null);
  assert.equal(host.selectedFile, null);
  assert.equal(requests.length, 1);
});

test('switching files drops the previous tag target', () => {
  const { host } = panel();
  host.openPropertiesPanel('u1', 'file');
  host.selectedFile = { uuid: 'u1', tags: [{ uuid: 't' }] };

  host.openPropertiesPanel('u2', 'file');

  assert.equal(host.selectedFile, null);
  assert.equal(host.propertiesUuid, 'u2');
});

test('reloading fetches the file on show again and keeps the panel open', () => {
  const { host, requests } = panel();
  host.openPropertiesPanel('u1', 'folder');

  host.reloadPropertiesPanel();

  assert.equal(host.showPropertiesPanel, true);
  assert.equal(host.propertiesUuid, 'u1');
  assert.equal(host.propertiesNodeType, 'folder');
  assert.equal(requests.length, 2);
});

test('reloading a closed panel does nothing', () => {
  const { host, requests } = panel();

  host.reloadPropertiesPanel();

  assert.equal(host.showPropertiesPanel, false);
  assert.equal(requests.length, 0);
});

test('an open panel follows the file the viewer steps to', () => {
  const { host, requests } = panel();
  host.openPropertiesPanel('u1', 'file');

  host.followViewer({ uuid: 'u2', properties: true });

  assert.equal(host.propertiesUuid, 'u2');
  assert.deepEqual(requests.map((r) => r[0]), ['/files/properties/u1', '/files/properties/u2']);
});

test('the viewer opens no panel the user did not ask for', () => {
  const { host, requests } = panel();

  host.followViewer({ uuid: 'u2', properties: true });

  assert.equal(host.showPropertiesPanel, false);
  assert.equal(requests.length, 0);
});

test('the panel stays on the file it shows, and closes on one it may not describe', () => {
  const { host, requests } = panel();
  host.openPropertiesPanel('u1', 'file');

  host.followViewer({ uuid: 'u1', properties: true });
  assert.equal(host.showPropertiesPanel, true);
  assert.equal(requests.length, 1);

  host.followViewer({ uuid: 'u2', properties: false });
  assert.equal(host.showPropertiesPanel, false);
});

test('opening adds a history entry naming the file, and closing returns from it', () => {
  const { ctx, host, history } = panel('/files/f?open=x');

  host.openPropertiesPanel(U1, 'file');
  assert.equal(ctx.location.search, `?open=x&properties=${U1}`);

  host.closePropertiesPanel();

  assert.deepEqual(history.map((w) => Array.from(w)), [
    ['push', `/files/f?open=x&properties=${U1}`],
    ['returnTo', '/files/f?open=x'],
  ]);
});

test('moving the open panel to another file rewrites its entry', () => {
  const { host, history } = panel();
  host.openPropertiesPanel(U1, 'file');

  host.followViewer({ uuid: U2, properties: true });

  assert.deepEqual(history.map((w) => w[0]), ['push', 'replace']);
  assert.equal(history[1][1], `/files?properties=${U2}`);
});

test('reloading the file on show writes no history', () => {
  const { host, history } = panel();
  host.openPropertiesPanel(U1, 'file');

  host.reloadPropertiesPanel();

  assert.equal(history.length, 1);
});

test('a close after a navigation that dropped the parameter writes no history', () => {
  const { ctx, host, history } = panel();
  host.openPropertiesPanel(U1, 'file');
  ctx.location = new URL('http://testserver/files/other');

  host.closePropertiesPanel();

  assert.equal(history.length, 1);
});

test('back onto an entry without the parameter closes the panel', () => {
  const { ctx, host, history } = panel();
  host.openPropertiesPanel(U1, 'file');
  ctx.location = new URL('http://testserver/files');

  host.followPropertiesUrl();

  assert.equal(host.showPropertiesPanel, false);
  assert.equal(history.length, 1);
});

test('forward onto an entry naming a file opens it, without writing history', () => {
  const { ctx, host, requests, history } = panel();
  ctx.document.querySelector = (sel) => (sel.includes(U1) ? { dataset: { nodeType: 'folder' } } : null);
  ctx.location = new URL(`http://testserver/files?properties=${U1}`);

  host.followPropertiesUrl();
  host.followPropertiesUrl();

  assert.equal(host.showPropertiesPanel, true);
  assert.equal(host.propertiesUuid, U1);
  assert.equal(host.propertiesNodeType, 'folder');
  assert.equal(requests.length, 1);
  assert.equal(history.length, 0);
  assert.equal(host.propertiesAddressed(), true);
});

test('a malformed parameter opens nothing', () => {
  const { host, requests } = panel('/files?properties=nope');

  host.followPropertiesUrl();

  assert.equal(host.showPropertiesPanel, false);
  assert.equal(requests.length, 0);
});
