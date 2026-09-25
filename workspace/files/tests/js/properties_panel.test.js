const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function panel() {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/properties_panel.js');
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
  return { host, listeners, requests };
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
