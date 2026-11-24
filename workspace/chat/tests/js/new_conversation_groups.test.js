'use strict';

// The new-conversation dialog offers every group of the instance. A group
// conversation takes its members from its groups, so it needs one the creator
// is in: nothing is sent until one of the picked groups is theirs.

const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const DESIGN = { id: 1, name: 'Design', mine: true };
const SALES = { id: 2, name: 'Sales', mine: false };

function buildApp() {
  const sent = [];
  const ctx = loadScripts(['workspace/common/static/ui/js/uuid.js', 'workspace/chat/ui/static/chat/ui/js/conversations.js'], {
    document: { getElementById: () => null },
    localStorage: { getItem: () => '', setItem() {}, removeItem() {} },
    location: { pathname: '/chat' },
    getCSRFToken: () => 'token',
    navHistory: { push() {}, replace() {} },
    fetch: async (url, options) => {
      sent.push(JSON.parse(options.body));
      return { ok: false, json: async () => ({}) };
    },
  });
  const app = ctx.chatConversationsMixin();
  app.chatGroups = ctx.markOwnChatGroups([DESIGN, SALES]);
  app.newConvMode = 'groups';
  return { app, sent };
}

const picked = (app, id) => app.chatGroups.find(g => g.id === id);

test("the user's own groups carry the hint the picker shows", () => {
  const { app } = buildApp();
  assert.equal(picked(app, DESIGN.id).hint, 'Member');
  assert.equal(picked(app, SALES.id).hint, undefined);
});

test('every group is offered, and a picked one leaves the list', () => {
  const { app } = buildApp();
  assert.deepEqual(app.selectableGroups().map(g => g.name), ['Design', 'Sales']);
  app.addSelectedGroup(picked(app, SALES.id));
  assert.deepEqual(app.selectableGroups().map(g => g.name), ['Design']);
});

test("nothing is sent while none of the picked groups is the user's", async () => {
  const { app, sent } = buildApp();
  app.addSelectedGroup(picked(app, SALES.id));

  assert.equal(app.hasOwnGroupSelected(), false);
  await app.createConversation();

  assert.deepEqual(sent, []);
});

test('one group of the user is enough to send the others with it', async () => {
  const { app, sent } = buildApp();
  app.addSelectedGroup(picked(app, SALES.id));
  app.addSelectedGroup(picked(app, DESIGN.id));

  assert.equal(app.hasOwnGroupSelected(), true);
  await app.createConversation();

  assert.deepEqual(sent, [{ group_ids: [SALES.id, DESIGN.id] }]);
});
