'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

// fetch is hit inside saveCallSounds. A thenable that returns itself
// satisfies the `.catch(...)` chain.
function fetchStub() {
  const p = { then: () => p, catch: () => p };
  return () => p;
}

function makeDocument(seedValue, prefsSeed) {
  return {
    getElementById(id) {
      if (id === 'call-sounds-enabled-data') {
        return { textContent: JSON.stringify(seedValue) };
      }
      if (id === 'chat-prefs-data' && prefsSeed) {
        return { textContent: JSON.stringify(prefsSeed) };
      }
      return null;
    },
  };
}

function load(seedValue, chatCallSounds, prefsSeed) {
  return loadScript('workspace/chat/ui/static/chat/ui/js/chat_preferences.js', {
    fetch: fetchStub(),
    document: makeDocument(seedValue, prefsSeed),
    getCSRFToken: () => 'csrf',
    chatCallSounds,
  });
}

test('the prefs cache is seeded from the chat-prefs-data json_script', () => {
  const ctx = load(true, undefined, { compactMessageView: true });
  assert.strictEqual(ctx._chatPrefsCache.compactMessageView, true);
  // Untouched keys keep their defaults.
  assert.strictEqual(ctx._chatPrefsCache.compactConversationList, false);
  assert.strictEqual(ctx.chatPreferences().prefs.compactMessageView, true);
});

test('callSounds is seeded from the call-sounds-enabled-data json_script', () => {
  const ctx = load(false, { setEnabled() {} });
  const comp = ctx.chatPreferences();
  assert.strictEqual(comp.callSounds, false);
});

test('saveCallSounds applies the value live to the audio engine', () => {
  const calls = [];
  const ctx = load(true, { setEnabled: (v) => calls.push(v) });
  const comp = ctx.chatPreferences();

  comp.saveCallSounds(false);

  assert.strictEqual(comp.callSounds, false);
  assert.deepStrictEqual(calls, [false]);
});

test('saveCallSounds does not throw when the audio engine is absent', () => {
  const ctx = load(true, undefined);
  const comp = ctx.chatPreferences();

  assert.doesNotThrow(() => comp.saveCallSounds(true));
  assert.strictEqual(comp.callSounds, true);
});
