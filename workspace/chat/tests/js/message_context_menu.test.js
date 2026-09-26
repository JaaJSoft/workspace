'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

// A bubble and a right-click on it. `selectionTouches` is what the selection
// answers when asked whether it reaches the bubble; its anchor is always
// elsewhere, like a drag started in the message above.
function rightClick({ selectionTouches }) {
  const bubble = {
    dataset: { messageUuid: 'm1', authorName: 'Alice', body: 'hello' },
    contains: () => false,
  };
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    document: { getElementById: () => null },
    getSelection: () => ({
      isCollapsed: false,
      anchorNode: {},
      containsNode: (node, partly) => partly && node === bubble && selectionTouches,
    }),
  });
  const app = ctx.chatMessagesMixin();
  app.$nextTick = () => {};
  const event = {
    shiftKey: false,
    clientX: 10,
    clientY: 20,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    target: {
      closest: (selector) => (selector === '[data-message-uuid]' ? bubble : null),
    },
  };
  app.openMessageContextMenu(event);
  return { app, event };
}

test('a selection ending inside the bubble keeps the browser menu', () => {
  const { app, event } = rightClick({ selectionTouches: true });

  assert.equal(event.defaultPrevented, false);
  assert.equal(app.msgMenu.open, false);
});

test('a selection elsewhere on the page does not block the menu', () => {
  const { app, event } = rightClick({ selectionTouches: false });

  assert.equal(event.defaultPrevented, true);
  assert.equal(app.msgMenu.open, true);
});
