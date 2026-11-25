'use strict';

// Where each mail preference is written: the display options as one object,
// the AI features and notification options as settings of their own.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

function load({ reply = { ok: true, status: 200 } } = {}) {
  const puts = [];
  const events = [];
  const ctx = loadScripts([
    'workspace/core/static/core/js/user_settings.js',
    'workspace/mail/ui/static/mail/ui/js/mail_preferences.js',
  ], {
    getCSRFToken: () => 'csrf-token',
    setTimeout: (fn) => fn(),
    clearTimeout: () => {},
    fetch: (url, options) => {
      puts.push({ url, method: options.method, body: JSON.parse(options.body) });
      return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
    },
    document: { getElementById: () => null },
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init?.detail;
      }
    },
    dispatchEvent: (event) => events.push(event.type),
  });
  return { ctx, app: ctx.mailPreferences(), puts, events };
}

test('a display option puts the whole preferences object', () => {
  const { app, puts, events } = load();
  app.update('density', 'compact');

  assert.equal(puts.length, 1);
  assert.equal(puts[0].url, '/api/v1/settings/mail/preferences');
  assert.equal(puts[0].method, 'PUT');
  assert.equal(puts[0].body.value.density, 'compact');
  assert.equal(puts[0].body.value.showLabels, true);
  assert.deepEqual(events, ['mail:preferences-changed']);
});

test('updateMailPref puts the whole cache with the changed key', () => {
  const { ctx, puts } = load();
  ctx.updateMailPref('rulesCompact', true);

  assert.equal(puts[0].url, '/api/v1/settings/mail/preferences');
  assert.equal(puts[0].body.value.rulesCompact, true);
});

test('an AI feature is a setting of its own', () => {
  const { app, puts } = load();
  app.saveAiFeature('classify', false);

  assert.equal(app.ai.classify, false);
  assert.equal(puts[0].url, '/api/v1/settings/mail/ai_classify');
  assert.deepEqual(puts[0].body, { value: false });
});

test('the notification options are settings of their own', () => {
  const { app, puts } = load();
  app.saveNotifyMode('always');
  app.saveNotifyBurst(3);

  assert.deepEqual(puts.map((p) => [p.url, p.body.value]), [
    ['/api/v1/settings/mail/notify_mode', 'always'],
    ['/api/v1/settings/mail/notify_max_burst', 3],
  ]);
});

test('a refused or lost write is swallowed, the option keeps its new value on screen', async () => {
  for (const reply of [{ ok: false, status: 400 }, new TypeError('offline')]) {
    const { app } = load({ reply });
    app.saveNotifyMode('always');
    app.saveAiFeature('extract', false);
    app.update('previewLines', 2);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(app.notifyMode, 'always');
    assert.equal(app.ai.extract, false);
    assert.equal(app.prefs.previewLines, 2);
  }
});
