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

function makePage({
  folder = 'folder-a', nameCollision = 'keep_both', existing = [], answers = [],
  search = '', inbox = {}, picked = null,
} = {}) {
  const dialogs = [];
  const pickers = [];
  const alerts = [];
  const history = [];
  const navigations = [];
  const removed = [];
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
        effect: (fn) => fn(),
      },
      location: { pathname: '/files', search },
      getCSRFToken: () => 'token',
      getFilePrefs: () => ({ nameCollision }),
      URLSearchParams,
      fetch: async () => ({ ok: true, json: async () => existing.map((name) => ({ name })) }),
      AppDialog: {
        select: async (options) => { dialogs.push(options); return answers.shift(); },
        folderPicker: async (options) => { pickers.push(options); return picked; },
      },
      AppAlert: { error: (message) => alerts.push(message) },
      navHistory: { replace: (url) => history.push(url) },
      folderNav: { navigateTo: (url) => navigations.push(url) },
      incomingFiles: {
        get: async (id) => inbox[id] || null,
        remove: async (id) => { removed.push(id); },
      },
      addEventListener: () => {},
      dispatchEvent: () => true,
      CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
      XMLHttpRequest: FakeXhr,
      FormData: FakeFormData,
      setTimeout: () => 0,
      clearTimeout: () => {},
    },
  );
  alpineInit.forEach((fn) => fn());
  const browser = ctx.fileBrowser();
  return { browser, sent, state, dialogs, pickers, alerts, history, navigations, removed };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

function fakeFile(name, size = 100) {
  return { name, size };
}

test('files dropped in a folder still land there after the user navigates away', async () => {
  const { browser, sent, state } = makePage({ folder: 'folder-a' });

  browser.uploadFiles(['one.jpg', 'two.jpg', 'three.jpg', 'four.jpg'].map((name) => fakeFile(name)));
  await settle();
  assert.equal(sent.length, 3, 'the fourth file waits for a free slot');

  state.folder = 'folder-b';
  sent[0].respond(201, { uuid: 'u1', name: 'one.jpg' });
  await settle();

  assert.equal(sent.length, 4);
  assert.deepEqual(
    sent.map((xhr) => xhr.form.parent),
    ['folder-a', 'folder-a', 'folder-a', 'folder-a'],
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
  for (const xhr of sent) {
    xhr.respond(201, { uuid: xhr.form.name, name: xhr.form.name });
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

test('incoming files go up to the folder picked for them', async () => {
  const { browser, sent, pickers, history, navigations, removed } = makePage({
    folder: '',
    search: '?incoming=share-1',
    inbox: { 'share-1': [fakeFile('a.jpg'), fakeFile('b.pdf')] },
    picked: { uuid: 'folder-b', name: 'B' },
  });

  await browser.receiveIncomingFiles();
  await settle();

  assert.equal(pickers.length, 1);
  assert.equal(pickers[0].message, 'Choose the folder to save the 2 shared files in.');
  assert.deepEqual(navigations, ['/files/folder-b']);
  assert.deepEqual(sent.map((xhr) => `${xhr.form.name}:${xhr.form.parent}`), ['a.jpg:folder-b', 'b.pdf:folder-b']);
  assert.deepEqual(removed, ['share-1']);
  assert.deepEqual(history, ['/files']);
});

test('incoming files picked for the personal root go up without a parent', async () => {
  const { browser, sent, navigations } = makePage({
    folder: 'folder-a',
    search: '?incoming=share-1',
    inbox: { 'share-1': [fakeFile('a.jpg')] },
    picked: { uuid: null, name: 'My files' },
  });

  await browser.receiveIncomingFiles();
  await settle();

  assert.deepEqual(navigations, ['/files']);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].form.parent, undefined);
});

test('closing the picker drops the incoming files without uploading them', async () => {
  const { browser, sent, removed, history } = makePage({
    search: '?incoming=share-1',
    inbox: { 'share-1': [fakeFile('a.jpg')] },
    picked: null,
  });

  await browser.receiveIncomingFiles();
  await settle();

  assert.equal(sent.length, 0);
  assert.deepEqual(removed, ['share-1']);
  assert.deepEqual(history, ['/files']);
});

test('a share the inbox does not hold is reported instead of opening the picker', async () => {
  for (const search of ['?incoming=gone', '?incoming=']) {
    const { browser, pickers, alerts, history } = makePage({ search });

    await browser.receiveIncomingFiles();

    assert.equal(pickers.length, 0);
    assert.deepEqual(alerts, ['The shared files could not be received. Share them again.']);
    assert.deepEqual(history, ['/files']);
  }
});

test('a page opened without incoming files does nothing', async () => {
  const { browser, pickers, alerts, history } = makePage({ search: '?tag=x' });

  await browser.receiveIncomingFiles();

  assert.equal(pickers.length + alerts.length + history.length, 0);
});
