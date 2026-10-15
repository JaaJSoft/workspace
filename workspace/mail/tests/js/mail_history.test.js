'use strict';

// Mail's history contract: opening a message adds one entry, moving to
// another message, a folder or a label rewrites it, closing steps back
// through returnTo, and landing on the page or restoring an entry never
// adds one - whatever the screen size.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

const ACCOUNT = '11111111-1111-4111-8111-111111111111';
const INBOX = '22222222-2222-4222-8222-222222222222';
const SENT = '33333333-3333-4333-8333-333333333333';
const LABEL = '44444444-4444-4444-8444-444444444444';
const MSG_A = '55555555-5555-4555-8555-555555555555';
const MSG_B = '66666666-6666-4666-8666-666666666666';

function makeApp(search = '') {
  const location = new URL(`http://testserver/mail${search}`);
  const writes = [];
  const navHistory = {
    push(url) {
      writes.push(['push', String(url)]);
      location.href = new URL(url, location.href).href;
    },
    replace(url) {
      writes.push(['replace', String(url)]);
      location.href = new URL(url, location.href).href;
    },
    returnTo(url) {
      writes.push(['returnTo', String(url)]);
      return false;
    },
  };
  const listeners = {};
  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/uuid.js',
      'workspace/common/static/ui/js/attachment_input.js',
      'workspace/mail/ui/static/mail/ui/js/mail_accounts.js',
      'workspace/mail/ui/static/mail/ui/js/mail_folders.js',
      'workspace/mail/ui/static/mail/ui/js/mail_messages.js',
      'workspace/mail/ui/static/mail/ui/js/mail_compose.js',
      'workspace/mail/ui/static/mail/ui/js/mail_labels.js',
      'workspace/mail/ui/static/mail/ui/js/mail_ai.js',
      'workspace/mail/ui/static/mail/ui/js/mail_rules.js',
      'workspace/mail/ui/static/mail/ui/js/mail_rules_form.js',
      'workspace/mail/ui/static/mail/ui/js/mail.js',
    ],
    {
      URL,
      URLSearchParams,
      location,
      navHistory,
      innerWidth: 390,
      document: { getElementById: () => null, addEventListener() {} },
      addEventListener(type, fn) { listeners[type] = fn; },
      sidebarPreference: { initial: () => false, save: () => {} },
      _mailPrefsReady: Promise.resolve(),
      _mailPrefsCache: {},
    },
  );
  const app = ctx.mailApp();
  const folders = [
    { uuid: INBOX, account_id: ACCOUNT, folder_type: 'inbox', name: 'INBOX', aliases: [] },
    { uuid: SENT, account_id: ACCOUNT, folder_type: 'sent', name: 'Sent', aliases: [] },
  ];
  const fetched = [];
  Object.assign(app, {
    accounts: [{ uuid: ACCOUNT }],
    $nextTick: (fn) => fn(),
    async loadFolders(uuid) { this.folders[uuid] = folders; },
    async fetchLabels(uuid) { this.labels[uuid] = [{ uuid: LABEL, account_id: ACCOUNT }]; },
    async loadMessages() {},
    async _fetch(url) {
      fetched.push(url);
      const uuid = url.split('/').pop();
      return {
        ok: true,
        json: async () => ({ uuid, account_id: ACCOUNT, folder_id: SENT, is_read: true }),
      };
    },
  });
  return { app, writes, location, listeners, fetched };
}

const address = (location) => location.pathname + location.search;

test('opening a message pushes, moving to another one replaces', async () => {
  const { app, writes } = makeApp();
  await app.init();
  writes.length = 0;

  await app.selectMessage({ uuid: MSG_A, is_read: true });
  await app.selectMessage({ uuid: MSG_B, is_read: true });

  assert.deepEqual(writes, [
    ['push', `/mail?message=${MSG_A}`],
    ['replace', `/mail?message=${MSG_B}`],
  ]);
});

test('switching folder, label or inbox replaces, on a phone too', async () => {
  const { app, writes } = makeApp();
  await app.init();
  writes.length = 0;

  await app.selectFolder(app.folders[ACCOUNT][1]);
  app.selectLabel(app.labels[ACCOUNT][0]);
  await app.selectUnifiedInbox();

  assert.deepEqual(writes, [
    ['replace', `/mail?folder=${SENT}`],
    ['replace', `/mail?label=${LABEL}`],
    ['replace', '/mail'],
  ]);
});

test('closing a message returns to its list', async () => {
  const { app, writes } = makeApp(`?folder=${INBOX}&message=${MSG_A}`);
  await app.init();
  writes.length = 0;

  app.closeMessage();

  assert.deepEqual(writes, [['returnTo', `/mail?folder=${INBOX}`]]);
  assert.equal(app.selectedMessage, null);
});

test('landing on /mail rewrites the entry instead of adding one', async () => {
  const { app, writes } = makeApp();
  await app.init();

  assert.deepEqual(writes, [['replace', '/mail']]);
  assert.equal(app.unifiedInbox, true);
});

test('landing on a folder and a message keeps both in the URL', async () => {
  const { app, writes, location } = makeApp(`?folder=${INBOX}&message=${MSG_A}`);
  await app.init();

  assert.ok(writes.every(([kind]) => kind === 'replace'), JSON.stringify(writes));
  assert.equal(address(location), `/mail?folder=${INBOX}&message=${MSG_A}`);
  assert.equal(app.selectedFolder.uuid, INBOX);
  assert.equal(app.messageDetail.uuid, MSG_A);
});

test('landing on a label and a message keeps the label listed', async () => {
  const { app, location } = makeApp(`?label=${LABEL}&message=${MSG_A}`);
  await app.init();

  assert.equal(address(location), `/mail?label=${LABEL}&message=${MSG_A}`);
  assert.equal(app.selectedLabel.uuid, LABEL);
  assert.equal(app.selectedFolder, null);
});

test('landing on a bare message lists its folder and records it', async () => {
  const { app, location } = makeApp(`?message=${MSG_A}`);
  await app.init();

  assert.equal(address(location), `/mail?folder=${SENT}&message=${MSG_A}`);
  assert.equal(app.selectedFolder.uuid, SENT);
});

test('landing on ?compose= drops it from the URL', async () => {
  const { app, location } = makeApp('?compose=bob%40example.com');
  let composed = null;
  app.showCompose = (opts) => { composed = opts; };
  await app.init();

  assert.equal(address(location), '/mail');
  assert.deepEqual({ ...composed }, { to: 'bob@example.com' });
});

test('a restore follows the URL and writes nothing', async () => {
  const { app, writes, location, listeners } = makeApp(`?folder=${INBOX}`);
  await app.init();
  writes.length = 0;

  location.href = `http://testserver/mail?folder=${INBOX}&message=${MSG_B}`;
  let prevented = false;
  listeners['nav:restore']({
    detail: { stale: false },
    preventDefault() { prevented = true; },
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(app.messageDetail.uuid, MSG_B);

  location.href = `http://testserver/mail?folder=${INBOX}`;
  listeners['nav:restore']({ detail: { stale: false }, preventDefault() {} });
  assert.equal(app.selectedMessage, null);

  assert.deepEqual(writes, []);
  assert.equal(prevented, false);
});

test('a restore onto an earlier page load is rebuilt, not reloaded', async () => {
  const { app, location, listeners, fetched } = makeApp(`?folder=${INBOX}&message=${MSG_A}`);
  await app.init();
  fetched.length = 0;

  location.href = `http://testserver/mail?folder=${INBOX}&message=${MSG_A}`;
  let prevented = false;
  listeners['nav:restore']({ detail: { stale: true }, preventDefault() { prevented = true; } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(prevented, true);
  assert.deepEqual(fetched, [`/api/v1/mail/messages/${MSG_A}`]);
});

test('a restore onto a message of the unified inbox stays in the unified inbox', async () => {
  const { app, writes, location, listeners } = makeApp();
  await app.init();
  writes.length = 0;

  location.href = `http://testserver/mail?message=${MSG_A}`;
  listeners['nav:restore']({ detail: { stale: false }, preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(app.unifiedInbox, true);
  assert.equal(app.selectedFolder, null);
  assert.equal(app.messageDetail.uuid, MSG_A);
  assert.deepEqual(writes, []);
});
