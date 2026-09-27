'use strict';

// Uploading from the Files page: the browser component decides name
// collisions, then hands the files to the upload queue. These tests drive
// the real queue over a fake XMLHttpRequest to pin what reaches the server.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

class FakeFormData {
  constructor() { this.fields = {}; }
  append(key, value) { this.fields[key] = value; }
}

function makePage({ folder = 'folder-a' } = {}) {
  const sent = [];
  const state = { folder };
  const alpineInit = [];
  const stores = {};

  class FakeXhr {
    constructor() { this.upload = {}; this.status = 0; this.responseText = ''; }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader() {}
    send(form) { this.form = form.fields; sent.push(this); }
    abort() { this.aborted = true; if (this.onabort) this.onabort(); }
    respond(status, body) {
      this.status = status;
      this.responseText = JSON.stringify(body);
      this.onload();
    }
  }

  const ctx = loadScripts(
    [
      'workspace/files/ui/static/files/ui/js/file_actions.js',
      'workspace/files/ui/static/files/ui/js/upload_queue.js',
      'workspace/files/ui/static/files/ui/js/browser.js',
    ],
    {
      tagsMixin: () => ({ toggleFileTag: async () => {} }),
      propertiesPanelMixin: () => ({}),
      document: {
        getElementById: (id) => {
          if (id === 'folder-browser') return { dataset: { folder: state.folder } };
          return null;
        },
        querySelector: () => null,
        addEventListener: (type, fn) => { if (type === 'alpine:init') alpineInit.push(fn); },
      },
      Alpine: {
        store: (name, value) => {
          if (value !== undefined) stores[name] = value;
          return stores[name];
        },
      },
      location: { pathname: '/files', search: '' },
      getCSRFToken: () => 'token',
      getFilePrefs: () => ({ nameCollision: 'keep_both' }),
      addEventListener: () => {},
      dispatchEvent: () => true,
      CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
      XMLHttpRequest: FakeXhr,
      FormData: FakeFormData,
    },
  );
  alpineInit.forEach((fn) => fn());
  const browser = ctx.fileBrowser();
  return { browser, sent, state };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

function fakeFile(name, size = 100) {
  return { name, size };
}

test('files dropped in a folder still land there after the user navigates away', async () => {
  const { browser, sent, state } = makePage({ folder: 'folder-a' });

  browser.uploadFiles([fakeFile('one.jpg'), fakeFile('two.jpg')]);
  await settle();
  assert.equal(sent.length, 1, 'files go up one at a time');

  state.folder = 'folder-b';
  sent[0].respond(201, { uuid: 'u1', name: 'one.jpg' });
  await settle();

  assert.equal(sent.length, 2);
  assert.deepEqual(
    sent.map((xhr) => xhr.form.parent),
    ['folder-a', 'folder-a'],
  );
});
