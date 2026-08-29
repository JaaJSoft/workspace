'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * The message entrance animation is a chat preference: the CSS picks the
 * keyframes from data-msg-animation on the messages container, and a change
 * of preference replays the animation on the last bubble so the choice is
 * visible without waiting for a message.
 */
function fetchStub() {
  const p = { then: () => p, catch: () => p };
  return () => p;
}

test('the animation preference defaults to slide', () => {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/chat_preferences.js', {
    fetch: fetchStub(),
    document: { getElementById: () => null },
    getCSRFToken: () => 'csrf',
  });
  assert.strictEqual(ctx._chatPrefsDefaults.messageAnimation, 'slide');
  assert.strictEqual(ctx.chatPreferences().prefs.messageAnimation, 'slide');
});

function makeBubble() {
  const classes = new Set();
  const log = [];
  return {
    log,
    // Reading layout between the removal and the re-add is what restarts the
    // animation; the stub records the read so the test can pin the order.
    get offsetWidth() { log.push('reflow'); return 0; },
    classList: {
      add(name) { classes.add(name); log.push(`add:${name}`); },
      remove(name) { classes.delete(name); log.push(`remove:${name}`); },
      contains(name) { return classes.has(name); },
    },
  };
}

function buildApp(bubbles) {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    document: {
      getElementById(id) {
        if (id !== 'message-list-items') return null;
        return { querySelectorAll: () => bubbles };
      },
    },
  });
  return ctx.chatMessagesMixin();
}

test('replaying restarts the entrance animation on the last bubble only', () => {
  const first = makeBubble();
  const last = makeBubble();
  const app = buildApp([first, last]);

  app.replayMessageAnimation();

  assert.deepStrictEqual(last.log, ['remove:msg-enter', 'reflow', 'add:msg-enter']);
  assert.deepStrictEqual(first.log, []);
});

test('replaying with no bubble on screen is a no-op', () => {
  const app = buildApp([]);
  app.replayMessageAnimation();
  const detached = buildApp(null);
  detached.replayMessageAnimation();
});
