const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

// Each fetch call is captured as a deferred so tests control completion
// order - the out-of-order failure races below depend on it.
function deferredFetch() {
  const calls = [];
  const impl = (url, opts) =>
    new Promise((resolve, reject) => calls.push({ url, opts, resolve, reject }));
  return { impl, calls };
}

function load(fetchImpl) {
  return loadScripts(['workspace/core/static/core/js/user_settings.js', 'workspace/projects/ui/static/projects/ui/js/preferences.js'], {
    document: { getElementById: () => null },
    fetch: fetchImpl,
    getCSRFToken: () => 'token',
    dispatchEvent: () => {},
    CustomEvent: function CustomEvent(name, opts) {
      this.name = name;
      this.detail = opts && opts.detail;
    },
  });
}

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

test('update puts the value under its key and announces a new notify level', async () => {
  const { impl, calls } = deferredFetch();
  const events = [];
  const ctx = load(impl);
  ctx.dispatchEvent = (event) => events.push(event);
  const prefs = ctx.projectsPreferences();

  prefs.update('notify_level', 'none');
  assert.equal(calls[0].url, '/api/v1/settings/projects/notify_level');
  assert.equal(calls[0].opts.method, 'PUT');
  assert.deepEqual(JSON.parse(calls[0].opts.body), { value: 'none' });
  calls[0].resolve({ ok: true });
  await settle();

  assert.deepEqual(events.map((e) => [e.name, e.detail.level]), [['projects-notify-level-changed', 'none']]);
});

test('update rolls back the optimistic value when the request fails', async () => {
  const { impl, calls } = deferredFetch();
  const prefs = load(impl).projectsPreferences();

  prefs.update('reminder_hour', 9);
  calls[0].resolve({ ok: false });
  await settle();

  assert.equal(prefs.prefs.reminder_hour, 8);
});

test('a stale failure does not clobber a newer successful update', async () => {
  const { impl, calls } = deferredFetch();
  const prefs = load(impl).projectsPreferences();

  prefs.update('notify_level', 'in_app'); // A, will fail late
  prefs.update('notify_level', 'none'); // B, succeeds first
  calls[1].resolve({ ok: true });
  await settle();
  calls[0].resolve({ ok: false });
  await settle();

  assert.equal(prefs.prefs.notify_level, 'none');
});

test('set rolls back the override when the request fails', async () => {
  const { impl, calls } = deferredFetch();
  const level = load(impl).projectNotificationLevel({
    url: '/nl',
    override: '',
    moduleLevel: 'all',
  });

  level.set('in_app');
  calls[0].resolve({ ok: false });
  await settle();

  assert.equal(level.override, '');
});

test('a stale failure does not clobber a newer successful override', async () => {
  const { impl, calls } = deferredFetch();
  const level = load(impl).projectNotificationLevel({
    url: '/nl',
    override: '',
    moduleLevel: 'all',
  });

  level.set('in_app'); // A, will fail late
  level.set('none'); // B, succeeds first
  calls[1].resolve({ ok: true });
  await settle();
  calls[0].reject(new Error('network'));
  await settle();

  assert.equal(level.override, 'none');
});

test('a stale failure does not resurrect an override reset in the meantime', async () => {
  const { impl, calls } = deferredFetch();
  const level = load(impl).projectNotificationLevel({
    url: '/nl',
    override: 'in_app',
    moduleLevel: 'all',
  });

  level.set('none'); // A, will fail late
  level.reset(); // B (DELETE), succeeds first
  calls[1].resolve({ ok: true });
  await settle();
  calls[0].resolve({ ok: false });
  await settle();

  assert.equal(level.override, '');
});
