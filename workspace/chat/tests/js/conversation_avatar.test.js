'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

// Pins the three branches conversationAvatar() renders, so the merge with the
// server-rendered copy in _conversation_item.html is provably
// behaviour-preserving.
function buildApp() {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', {
    document: { getElementById: () => null },
    userAvatarTag: (id, username, opts) =>
      `<user-avatar user-id="${id}" username="${username}" size="${opts.size}"></user-avatar>`,
  });
  const app = ctx.chatConversationsMixin();
  app.currentUserId = 1;
  app.memberDisplayName = (m) => m.user.username;
  return app;
}

const dm = {
  kind: 'dm',
  uuid: 'c-1',
  members: [{ user: { id: 1, username: 'me' } }, { user: { id: 2, username: 'Sam' } }],
};

test('a direct message delegates to the shared user avatar', () => {
  const html = buildApp().conversationAvatar(dm);

  assert.match(html, /^<user-avatar /);
  assert.match(html, /user-id="2"/);
  assert.match(html, /username="Sam"/);
});

test('a direct message with nobody else falls back to a neutral circle', () => {
  const html = buildApp().conversationAvatar({ kind: 'dm', uuid: 'c-1', members: [] });

  assert.match(html, /bg-neutral/);
  assert.match(html, />\?</);
});

test('a group with an uploaded avatar renders the image, cache-busted', () => {
  const app = buildApp();

  const plain = app.conversationAvatar({ kind: 'group', uuid: 'c-2', has_avatar: true });
  assert.match(plain, /src="\/api\/v1\/chat\/conversations\/c-2\/avatar\/image"/);

  const busted = app.conversationAvatar({
    kind: 'group',
    uuid: 'c-2',
    has_avatar: true,
    _avatar_bust: '42',
  });
  assert.match(busted, /avatar\/image\?t=42/);
});

test('a group without an avatar shows up to two members initials', () => {
  const app = buildApp();
  const members = [
    { user: { id: 1, username: 'me' } },
    { user: { id: 2, username: 'Sam' } },
    { user: { id: 3, username: 'Jordan' } },
    { user: { id: 4, username: 'Robin' } },
  ];

  const html = app.conversationAvatar({ kind: 'group', uuid: 'c-3', members });
  assert.match(html, />SJ</);

  const empty = app.conversationAvatar({ kind: 'group', uuid: 'c-4', members: [] });
  assert.match(empty, />G</);
});
