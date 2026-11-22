'use strict';

// loadMessages() refreshes the open list: after a sync, a failed action, and
// on every stream reconnection (resync). A list scrolled past its first page
// holds several pages, and the refresh has to give back all of them, not
// replace the list with the last one loaded.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

const PAGE_SIZE = 50;

function mails(from, to) {
  const rows = [];
  for (let n = from; n >= to; n--) rows.push({ uuid: `mail-${String(n).padStart(3, '0')}` });
  return rows;
}

function makeApp(server) {
  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/latest_only.js',
      'workspace/common/static/ui/js/attachment_input.js',
      'workspace/mail/ui/static/mail/ui/js/mail_accounts.js',
      'workspace/mail/ui/static/mail/ui/js/mail_folders.js',
      'workspace/mail/ui/static/mail/ui/js/mail_messages.js',
      'workspace/mail/ui/static/mail/ui/js/mail_body_frame.js',
      'workspace/mail/ui/static/mail/ui/js/mail_compose.js',
      'workspace/mail/ui/static/mail/ui/js/mail_labels.js',
      'workspace/mail/ui/static/mail/ui/js/mail_ai.js',
      'workspace/mail/ui/static/mail/ui/js/mail_rules.js',
      'workspace/mail/ui/static/mail/ui/js/mail_rules_form.js',
      'workspace/mail/ui/static/mail/ui/js/mail.js',
    ],
    { document: { getElementById: () => null }, sidebarPreference: { initial: () => false, save: () => {} } },
  );
  const app = ctx.mailApp();
  app.accounts = [];
  app.unifiedInbox = true;
  app.requested = [];
  app._fetch = async (url) => {
    const page = Number(new URL(url, 'http://x').searchParams.get('page'));
    app.requested.push(page);
    return server(page);
  };
  return app;
}

// Answers like GET /api/v1/mail/messages over `rows()`, read at request time.
function listing(rows, { failing = [] } = {}) {
  return async (page) => {
    if (failing.includes(page)) return { ok: false, json: async () => ({}) };
    const all = rows();
    return {
      ok: true,
      json: async () => ({
        results: all.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
        count: all.length,
        page,
        page_size: PAGE_SIZE,
      }),
    };
  };
}

async function scrolledToPageTwo(server) {
  const app = makeApp(server);
  await app.loadMessages();
  await app.loadMoreMessages();
  assert.equal(app.messages.length, 100);
  assert.equal(app.currentPage, 2);
  return app;
}

function uuids(app) {
  return Array.from(app.messages, (m) => m.uuid);
}

test('a stream reconnection keeps every page the list had loaded', async () => {
  const app = await scrolledToPageTwo(listing(() => mails(120, 1)));

  await app.resync();

  assert.equal(app.messages.length, 100);
  assert.equal(uuids(app)[0], 'mail-120');
  assert.equal(uuids(app)[99], 'mail-021');
  assert.equal(app.currentPage, 2);
  assert.equal(app.hasMoreMessages, true);
});

test('the refreshed pages show mail that arrived since', async () => {
  let rows = mails(120, 1);
  const app = await scrolledToPageTwo(listing(() => rows));
  rows = mails(122, 1);

  await app.loadMessages();

  assert.deepEqual(uuids(app).slice(0, 3), ['mail-122', 'mail-121', 'mail-120']);
  assert.equal(app.messages.length, 100);
  assert.equal(app.totalMessages, 122);
  assert.equal(app.hasMoreMessages, true);
});

test('a message arriving between two pages is not listed twice', async () => {
  let rows = mails(120, 1);
  let armed = false;
  const base = listing(() => rows);
  const app = await scrolledToPageTwo(async (page) => {
    const res = await base(page);
    // Lands once page 1 is answered: page 2 starts one row earlier.
    if (armed && page === 1) rows = mails(121, 1);
    return res;
  });
  armed = true;

  await app.loadMessages();

  assert.equal(new Set(uuids(app)).size, app.messages.length);
  assert.equal(uuids(app)[0], 'mail-120');
  assert.equal(uuids(app).at(-1), 'mail-022');
  await app.loadMoreMessages();
  assert.equal(uuids(app)[app.messages.length - 21], 'mail-021');
  assert.equal(new Set(uuids(app)).size, app.messages.length);
});

test('a page that fails keeps the ones before it, and scrolling loads it again', async () => {
  const failing = [];
  const app = await scrolledToPageTwo(listing(() => mails(120, 1), { failing }));
  failing.push(2);

  await app.loadMessages();

  assert.equal(app.messages.length, 50);
  assert.equal(app.currentPage, 1);
  assert.equal(app.hasMoreMessages, true);
  failing.length = 0;
  app.requested = [];
  await app.loadMoreMessages();
  assert.deepEqual(Array.from(app.requested), [2]);
  assert.equal(app.messages.length, 100);
});

test('a first page that fails leaves the list as it was', async () => {
  const failing = [];
  const app = await scrolledToPageTwo(listing(() => mails(120, 1), { failing }));
  failing.push(1);

  await app.loadMessages();

  assert.equal(app.messages.length, 100);
  assert.equal(app.currentPage, 2);
});

test('a list that shrank below its loaded pages stops at the last one left', async () => {
  let rows = mails(120, 1);
  const app = await scrolledToPageTwo(listing(() => rows));
  rows = mails(30, 1);

  await app.loadMessages();

  assert.equal(app.messages.length, 30);
  assert.equal(app.currentPage, 1);
  assert.equal(app.hasMoreMessages, false);
});

test('a list on its first page is refreshed with one request', async () => {
  const app = makeApp(listing(() => mails(120, 1)));
  await app.loadMessages();
  app.requested = [];

  await app.loadMessages();

  assert.deepEqual(Array.from(app.requested), [1]);
  assert.equal(app.messages.length, 50);
});

test('a page that throws after another selection loaded applies nothing', async () => {
  const app = await scrolledToPageTwo(listing(() => mails(120, 1)));
  const server = app._fetch;
  const other = [{ uuid: 'other-folder-mail' }];
  app._fetch = async (url) => {
    const page = Number(new URL(url, 'http://x').searchParams.get('page'));
    if (page === 1) return server(url);
    // A folder switch commits its list while page 2 is in flight.
    app._messagesRequestId += 1;
    app.messages = other;
    app.currentPage = 1;
    throw new TypeError('Failed to fetch');
  };

  await app.loadMessages();

  assert.equal(app.messages, other);
  assert.equal(app.currentPage, 1);
});

test('a load superseded by another selection applies nothing', async () => {
  const app = await scrolledToPageTwo(listing(() => mails(120, 1)));
  const before = app.messages;
  const server = app._fetch;
  app._fetch = async (url) => {
    const res = await server(url);
    app._messagesRequestId += 1;
    return res;
  };

  await app.loadMessages();

  assert.equal(app.messages, before);
});
