'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const CONV_UUID = 'conv-1';

function member(id, role) {
  return { uuid: `m-${id}`, role, user: { id, username: `user${id}` } };
}

function buildApp({ kind = 'group', myRole = 'admin', response = null } = {}) {
  const requests = [];
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/members.js', {
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, json: async () => response };
    },
  });
  const conversation = {
    uuid: CONV_UUID,
    kind,
    my_role: myRole,
    members: [member(1, 'admin'), member(2, 'member')],
  };
  const app = Object.assign(ctx.chatMembersMixin(), {
    activeConversation: conversation,
    conversations: [{ ...conversation }],
  });
  return { app, requests };
}

test('only a group admin manages the group', () => {
  assert.equal(buildApp().app.isGroupAdmin(), true);
  assert.equal(buildApp({ myRole: 'member' }).app.isGroupAdmin(), false);
  assert.equal(buildApp({ kind: 'dm', myRole: 'member' }).app.isGroupAdmin(), false);
});

test('a direct message stays editable by both people, a group by its admins', () => {
  assert.equal(buildApp({ kind: 'dm', myRole: 'member' }).app.canEditConversationDetails(), true);
  assert.equal(buildApp({ myRole: 'member' }).app.canEditConversationDetails(), false);
  assert.equal(buildApp().app.canEditConversationDetails(), true);
});

test('groupAdminCount counts the admins of the open conversation', () => {
  assert.equal(buildApp().app.groupAdminCount(), 1);
});

test('setMemberRole applies the returned members and the reader role', async () => {
  const response = { members: [member(1, 'member'), member(2, 'admin')], my_role: 'member' };
  const { app, requests } = buildApp({ response });

  await app.setMemberRole(1, 'member');

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, `/api/v1/chat/conversations/${CONV_UUID}/members/1`);
  assert.equal(requests[0].options.method, 'PATCH');
  assert.deepStrictEqual(JSON.parse(requests[0].options.body), { role: 'member' });
  assert.equal(app.activeConversation.my_role, 'member');
  assert.equal(app.activeConversation.members[1].role, 'admin');
  assert.equal(app.conversations[0].my_role, 'member');
});

test('setMemberRole sends nothing for a member', async () => {
  const { app, requests } = buildApp({ myRole: 'member' });

  await app.setMemberRole(2, 'admin');

  assert.equal(requests.length, 0);
});
