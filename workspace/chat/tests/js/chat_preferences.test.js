'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

// fetch is hit both at load time (preferences hydration) and inside
// saveCallSounds. A thenable that returns itself satisfies both the
// `.then(...).then(...).catch(...)` and the `.catch(...)` chains.
function fetchStub() {
  const p = { then: () => p, catch: () => p };
  return () => p;
}

function makeDocument(seedValue) {
  return {
    getElementById(id) {
      if (id === 'call-sounds-enabled-data') {
        return { textContent: JSON.stringify(seedValue) };
      }
      return null;
    },
  };
}

function load(seedValue, chatCallSounds) {
  return loadScript('workspace/chat/ui/static/chat/ui/js/chat_preferences.js', {
    fetch: fetchStub(),
    document: makeDocument(seedValue),
    getCSRFToken: () => 'csrf',
    chatCallSounds,
  });
}

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
