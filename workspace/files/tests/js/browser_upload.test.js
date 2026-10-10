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

function makePage({ folder = 'folder-a', nameCollision = 'keep_both', existing = [], answers = [] } = {}) {
  const dialogs = [];
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
      getFilePrefs: () => ({ nameCollision }),
      URLSearchParams,
      fetch: async () => ({ ok: true, json: async () => existing.map((name) => ({ name })) }),
      AppDialog: {
        select: async (options) => { dialogs.push(options); return answers.shift(); },
      },
      addEventListener: () => {},
      dispatchEvent: () => true,
      CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
      XMLHttpRequest: FakeXhr,
      FormData: FakeFormData,
    },
  );
  alpineInit.forEach((fn) => fn());
  const browser = ctx.fileBrowser();
  return { browser, sent, state, dialogs };
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

test('a conflict answer can be applied to the remaining conflicts of the batch', async () => {
  const { browser, sent, dialogs } = makePage({
    nameCollision: 'ask',
    existing: ['a.jpg', 'b.jpg', 'c.jpg'],
    answers: [{ value: 'replace', checked: true }],
  });

  await browser.uploadFiles([fakeFile('a.jpg'), fakeFile('new.jpg'), fakeFile('b.jpg'), fakeFile('c.jpg')]);

  assert.equal(dialogs.length, 1, 'asked once for the whole batch');
  assert.equal(dialogs[0].checkbox, 'Do the same for the 2 other conflicts');
  for (const name of ['a.jpg', 'new.jpg', 'b.jpg']) {
    sent.at(-1).respond(201, { uuid: name, name });
    await settle();
  }
  assert.deepEqual(
    sent.map((xhr) => `${xhr.form.name}:${xhr.form.on_conflict || ''}`),
    ['a.jpg:replace', 'new.jpg:', 'b.jpg:replace', 'c.jpg:replace'],
  );
});

test('without the checkbox each conflict is asked on its own, the last without the option', async () => {
  const { browser, dialogs } = makePage({
    nameCollision: 'ask',
    existing: ['a.jpg', 'b.jpg'],
    answers: [{ value: 'skip', checked: false }, 'rename'],
  });

  await browser.uploadFiles([fakeFile('a.jpg'), fakeFile('b.jpg')]);

  assert.equal(dialogs.length, 2);
  assert.equal(dialogs[1].checkbox, '');
});
