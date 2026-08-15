'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript, loadScripts, CUSTOM_ELEMENT_STUBS } = require('../../../common/tests/js/loader');

// The element needs a DOM; what is testable here is the decision each of its
// three branches makes - which partner a direct message stands for, which
// colours the fallback circle wears, and which letters it falls back to.
function load() {
  return loadScripts(
    [
      'workspace/common/static/ui/js/user_avatar.js',
      'workspace/chat/ui/static/chat/ui/js/conversation_avatar.js',
    ],
    {
      ...CUSTOM_ELEMENT_STUBS,
      // user_avatar.js subscribes to alpine:initialized at load time.
      document: { ...CUSTOM_ELEMENT_STUBS.document, addEventListener() {} },
      escapeHtml: (s) => String(s == null ? '' : s),
    },
  );
}

function buildApp() {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', {
    document: { getElementById: () => null },
  });
  const app = ctx.chatConversationsMixin();
  app.currentUserId = 1;
  return app;
}

const dm = {
  kind: 'dm',
  uuid: 'c-1',
  members: [{ user: { id: 1, username: 'me' } }, { user: { id: 2, username: 'Sam' } }],
};

test('a direct message resolves to the other participant', () => {
  const partner = buildApp().dmPartner(dm);

  assert.equal(partner.id, 2);
  assert.equal(partner.username, 'Sam');
});

test('a group, an empty direct message and no conversation have no partner', () => {
  const app = buildApp();

  assert.equal(app.dmPartner({ kind: 'group', uuid: 'c-2', members: dm.members }), null);
  assert.equal(app.dmPartner({ kind: 'dm', uuid: 'c-3', members: [] }), null);
  assert.equal(app.dmPartner({ kind: 'dm', uuid: 'c-4' }), null);
  assert.equal(app.dmPartner(null), null);
});

test('a group fallback is tinted, and turns solid on the selected row', () => {
  const { conversationAvatarFaceClasses } = load();

  assert.deepEqual(conversationAvatarFaceClasses('group', false), ['bg-info/20', 'text-info']);
  assert.deepEqual(conversationAvatarFaceClasses('group', true), ['bg-info', 'text-info-content']);
});

test('a direct-message fallback stays neutral whether or not it is selected', () => {
  const { conversationAvatarFaceClasses } = load();

  const idle = conversationAvatarFaceClasses('dm', false);
  assert.deepEqual(idle, ['bg-neutral', 'text-neutral-content']);
  assert.deepEqual(conversationAvatarFaceClasses('dm', true), idle);
});

test('the initials come from the server, with a per-kind last resort', () => {
  const { conversationAvatarInitials } = load();

  assert.equal(conversationAvatarInitials('SJ', 'group'), 'SJ');
  assert.equal(conversationAvatarInitials('  S  ', 'dm'), 'S');
  // A conversation the server could not label: nobody else is in it.
  assert.equal(conversationAvatarInitials('', 'group'), 'G');
  assert.equal(conversationAvatarInitials(null, 'dm'), '?');
});

test('the avatar sizes on the named scale user avatars already use', () => {
  const { USER_AVATAR_SIZES } = load();

  // The three the chat UI asks for: compact row, row, header.
  for (const size of ['xs', 'sm', 'md']) {
    assert.ok(USER_AVATAR_SIZES[size], `${size} is missing from the scale`);
  }
});
