'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript, loadScripts } = require('../../../common/tests/js/loader');

const PAGE_DATA = {
  'dashboard-apps-data': JSON.stringify([
    { slug: 'mail', hidden: false },
    { slug: 'chat', hidden: false },
  ]),
  'dashboard-today-sources-data': JSON.stringify([]),
  'dashboard-hidden-today-data': JSON.stringify([]),
};

const flush = () => new Promise((resolve) => setImmediate(resolve));

/** A fetch that holds every request until the test answers it. */
function heldFetch() {
  const requests = [];
  const fetch = (url, init) => new Promise((resolve) => {
    requests.push({ url, method: init.method, body: JSON.parse(init.body), answer: (reply = { ok: true }) => resolve(reply) });
  });
  return { fetch, requests };
}

function loadForm(fetch) {
  const ctx = loadScripts(['workspace/core/static/core/js/user_settings.js', 'workspace/dashboard/static/dashboard/js/preferences.js'], {
    document: { getElementById: (id) => ({ textContent: PAGE_DATA[id] }) },
    fetch,
    getCSRFToken: () => 'token',
  });
  const form = ctx.dashboardPrefsForm({
    temperatureUnit: 'celsius',
    todayUrl: '/dashboard/today',
    modulesUrl: '/dashboard/modules',
  });
  form.$ajax = () => {};
  form.$dispatch = () => {};
  return form;
}

test('the save queue starts a task only once the previous one settled', async () => {
  const enqueue = loadScript('workspace/dashboard/static/dashboard/js/preferences.js')
    .dashboardSaveQueue();
  const log = [];
  let finishFirst;
  enqueue(() => new Promise((resolve) => {
    log.push('first started');
    finishFirst = () => { log.push('first done'); resolve(); };
  }));
  const second = enqueue(() => { log.push('second started'); });

  await flush();
  assert.deepStrictEqual(log, ['first started']);
  finishFirst();
  await second;
  assert.deepStrictEqual(log, ['first started', 'first done', 'second started']);
});

test('a failed save does not block the next one', async () => {
  const enqueue = loadScript('workspace/dashboard/static/dashboard/js/preferences.js')
    .dashboardSaveQueue();
  enqueue(() => Promise.reject(new Error('offline'))).catch(() => {});
  let ran = false;
  await enqueue(() => { ran = true; });
  assert.strictEqual(ran, true);
});

test('two quick module toggles save one after the other, the last with the latest list', async () => {
  const { fetch, requests } = heldFetch();
  const form = loadForm(fetch);

  form.toggleApp('mail', false);
  form.toggleApp('chat', false);
  await flush();
  assert.strictEqual(requests.length, 1, 'the second save waits for the first');

  requests[0].answer();
  await flush();
  assert.strictEqual(requests.length, 2);
  assert.strictEqual(requests[1].url, '/api/v1/settings/dashboard/hidden_modules');
  assert.deepStrictEqual(requests[1].body.value, ['mail', 'chat']);
});

test('module and Today toggles share the queue', async () => {
  const { fetch, requests } = heldFetch();
  const form = loadForm(fetch);

  form.toggleApp('mail', false);
  form.toggleToday('events', false);
  await flush();
  assert.strictEqual(requests.length, 1);
  requests[0].answer();
  await flush();
  assert.strictEqual(requests[1].url, '/api/v1/settings/dashboard/hidden_today_sources');
});

test('a module toggle puts the hidden list, then re-renders the grid', async () => {
  const { fetch, requests } = heldFetch();
  const form = loadForm(fetch);
  const swaps = [];
  form.$ajax = (url, options) => swaps.push([url, options.target]);

  form.toggleApp('mail', false);
  await flush();
  assert.strictEqual(requests[0].method, 'PUT');
  assert.strictEqual(requests[0].url, '/api/v1/settings/dashboard/hidden_modules');
  requests[0].answer();
  await flush();
  assert.deepStrictEqual(swaps, [['/dashboard/modules', 'dashboard-modules-grid']]);
});

test('a refused save still re-renders, from what the server kept', async () => {
  const { fetch, requests } = heldFetch();
  const form = loadForm(fetch);
  const swaps = [];
  form.$ajax = (url, options) => swaps.push(options.target);

  form.toggleToday('events', false);
  await flush();
  requests[0].answer({ ok: false, status: 400 });
  await flush();
  assert.deepStrictEqual(swaps, ['dashboard-today']);
});
