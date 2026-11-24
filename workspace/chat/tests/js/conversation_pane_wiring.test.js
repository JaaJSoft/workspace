'use strict';

// The conversation pane's page-level wiring, shared by the chat page and the
// call room: a pick in the emoji picker lands in the composer or as a
// reaction, and Save to Files from the attachment viewer reaches the mixin.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function makePane() {
  const listeners = {};
  const ctx = loadScripts([
    'workspace/common/static/ui/js/attachment_input.js',
    'workspace/chat/ui/static/chat/ui/js/input.js',
  ], {
    addEventListener: (type, handler) => { listeners[type] = handler; },
    document: { activeElement: null, body: {} },
    innerWidth: 1280,
    innerHeight: 900,
  });
  const pickerListeners = {};
  const pane = {
    ...ctx.chatInputMixin(),
    $refs: { emojiPicker: { addEventListener: (type, handler) => { pickerListeners[type] = handler; } } },
    $nextTick: (fn) => fn(),
    calls: [],
  };
  pane.insertEmoji = (emoji) => pane.calls.push(['insert', emoji]);
  pane.toggleReaction = (uuid, emoji) => pane.calls.push(['react', uuid, emoji]);
  pane.saveAttachmentToFiles = (uuid) => pane.calls.push(['save', uuid]);
  pane.listenToConversationPane();
  return { pane, listeners, pickerListeners };
}

test('Save to Files from the attachment viewer reaches the pane', () => {
  const { pane, listeners } = makePane();
  listeners['chat-save-attachment-to-files']({ detail: { uuid: 'att-1' } });
  assert.deepStrictEqual(pane.calls.map((c) => Array.from(c)), [['save', 'att-1']]);
});

test('an emoji picked for a message becomes a reaction', () => {
  const { pane, pickerListeners } = makePane();
  pane.emojiPickerMode = 'reaction';
  pane.emojiPickerTargetMsg = 'msg-1';
  pickerListeners['emoji-click']({ detail: { unicode: '👍' } });
  assert.deepStrictEqual(pane.calls.map((c) => Array.from(c)), [['react', 'msg-1', '👍']]);
  assert.equal(pane.emojiPickerVisible, false);
});

test('a thread panel borrows the picker and gets its pick back', () => {
  const { pane, listeners, pickerListeners } = makePane();
  const panel = { calls: [], getMessageInput: () => null };
  panel.insertEmoji = (emoji) => panel.calls.push(['insert', emoji]);
  panel.toggleReaction = (uuid, emoji) => panel.calls.push(['react', uuid, emoji]);
  const anchor = { getBoundingClientRect: () => ({ left: 10, top: 400, bottom: 420 }) };
  pane.focusEmojiSearch = () => {};

  listeners['chat:open-emoji-picker']({ detail: { mode: 'input', anchor, requester: panel } });
  assert.equal(pane.emojiPickerVisible, true);
  pickerListeners['emoji-click']({ detail: { unicode: '🎉' } });

  assert.deepStrictEqual(panel.calls.map((c) => Array.from(c)), [['insert', '🎉']]);
  assert.deepStrictEqual(pane.calls, []);
  assert.equal(pane.emojiPickerVisible, false);

  // The next pick, made for the page itself, stays on the page.
  pane.emojiPickerMode = 'input';
  pickerListeners['emoji-click']({ detail: { unicode: '👋' } });
  assert.deepStrictEqual(pane.calls.map((c) => Array.from(c)), [['insert', '👋']]);
});
