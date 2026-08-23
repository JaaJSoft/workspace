const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// Each fetch call is captured as a deferred so tests control completion
// order - the out-of-order failure races below depend on it.
function deferredFetch() {
  const calls = [];
  const impl = (url, opts) =>
    new Promise((resolve, reject) => calls.push({ url, opts, resolve, reject }));
  return { impl, calls };
}

function load(fetchImpl) {
  return loadScript('workspace/projects/ui/static/projects/ui/js/preferences.js', {
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
