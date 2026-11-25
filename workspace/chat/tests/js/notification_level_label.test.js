'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

// What the conversation pane embeds with |json_script.
const LEVELS = [
  { value: 'all', label: 'All messages', icon: 'bell' },
  { value: 'mentions', label: 'Mentions only', icon: 'at-sign' },
  { value: 'none', label: 'Nothing', icon: 'bell-off' },
];

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', {
  document: {
    getElementById: (id) =>
      id === 'chat-notification-levels-data' ? { textContent: JSON.stringify(LEVELS) } : null,
  },
});

test('each level maps to its menu label', () => {
  assert.equal(ctx.chatNotificationLevelLabel('all'), 'All messages');
  assert.equal(ctx.chatNotificationLevelLabel('mentions'), 'Mentions only');
  assert.equal(ctx.chatNotificationLevelLabel('none'), 'Nothing');
});

test('an absent or unknown level reads as the model default', () => {
  // A conversation serialized before the field existed, or a value added
  // server-side that this bundle predates, must not render a blank button.
  assert.equal(ctx.chatNotificationLevelLabel(undefined), 'All messages');
  assert.equal(ctx.chatNotificationLevelLabel(null), 'All messages');
  assert.equal(ctx.chatNotificationLevelLabel(''), 'All messages');
  assert.equal(ctx.chatNotificationLevelLabel('weekends'), 'All messages');
});
