const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

const SCRIPTS = ['workspace/core/static/core/js/user_settings.js', 'workspace/photos/ui/static/photos/ui/js/photo_map.js'];

function preference(answer) {
  const requests = [];
  const errors = [];
  const events = [];
  const ctx = loadScripts(SCRIPTS, {
    getCSRFToken: () => 't',
    fetch: (url, opts) => {
      requests.push([url, opts.method, opts.body]);
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    },
    AppAlert: { error: (message) => errors.push(message) },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    dispatchEvent: (event) => events.push(event.type),
  });
  return { pref: ctx.photoMapPreference(), requests, errors, events };
}

test('showing the map writes the photos setting and has the panel reloaded', async () => {
  const { pref, requests, errors, events } = preference({ ok: true, status: 200 });

  await pref.setShowMap(true);

  assert.deepEqual(requests, [['/api/v1/settings/photos/show_map', 'PUT', '{"value":true}']]);
  assert.deepEqual(errors, []);
  assert.deepEqual(events, ['photo-map-preference', 'properties-section-changed']);
});

test('a refused map write says so, reloads nothing and lets the user try again', async () => {
  for (const answer of [{ ok: false, status: 400 }, new Error('offline')]) {
    const { pref, errors, events } = preference(answer);

    await pref.setShowMap(false);

    assert.equal(errors.length, 1);
    assert.deepEqual(events, []);
    assert.equal(pref.savingMapPreference, false);
  }
});
