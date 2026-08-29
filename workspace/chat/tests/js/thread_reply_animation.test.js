'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

/**
 * A reply landing in the open thread panel animates in like a message in the
 * main flow: the panel reloads itself, then tags the bubble the reload
 * inserted. The messages mixin is the real one; only the alpine-ajax merge is
 * simulated, by inserting the bubble the server would have rendered.
 */
function makeElement() {
  const classes = new Set();
  return {
    classList: {
      add(name) { classes.add(name); },
      contains(name) { return classes.has(name); },
    },
    replaceChildren() {},
  };
}

function buildPanel() {
  const byId = new Map();
  const ctx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/messages.js',
      'workspace/chat/ui/static/chat/ui/js/threads.js',
    ],
    {
      chatUiHelpersMixin: () => ({}),
      chatInputMixin: () => ({}),
      chatRecorderMixin: () => ({}),
      getCSRFToken: () => 'csrf-token',
      document: {
        querySelectorAll: () => [],
        getElementById: (id) => byId.get(id) || null,
      },
      fetch: async () => ({ ok: true, json: async () => ({}) }),
    },
  );
  const panel = ctx.chatThreadPanel('r1');
  const loads = [];
  Object.assign(panel, {
    activeConversation: { uuid: 'c1' },
    scrollToBottom() {},
    async $ajax(url) {
      loads.push(url);
      byId.set('tmsg-m4', makeElement());
      return [true, true];
    },
  });
  return { panel, byId, loads };
}

test('a reply to the open thread reloads the panel and animates the new bubble', async () => {
  const { panel, byId, loads } = buildPanel();

  await panel.onReplyReceived({ root: 'r1', uuid: 'm4' });

  assert.equal(loads.length, 1, 'the panel reloads once');
  assert.ok(byId.get('tmsg-m4').classList.contains('msg-enter'));
});

test('a reply to another thread leaves the panel alone', async () => {
  const { panel, loads } = buildPanel();

  await panel.onReplyReceived({ root: 'r2', uuid: 'm5' });

  assert.deepStrictEqual(loads, []);
});
