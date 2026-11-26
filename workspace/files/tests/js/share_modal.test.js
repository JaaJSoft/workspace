const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function modal() {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/share_modal.js');
  return ctx.shareModal();
}

test('a file target offers no mode choice', () => {
  const app = modal();
  app.nodeType = 'file';
  assert.equal(app.canChooseMode(), false);
});

test('a folder target offers the three modes', () => {
  const app = modal();
  app.nodeType = 'folder';
  assert.equal(app.canChooseMode(), true);
  assert.deepStrictEqual(
    Array.from(app.availableModes()).map(m => m.value),
    ['read', 'drop', 'both'],
  );
});

test('the caps only apply to a mode that accepts uploads', () => {
  const app = modal();
  app.nodeType = 'folder';
  app.newLinkMode = 'read';
  assert.equal(app.showsCaps(), false);
  app.newLinkMode = 'drop';
  assert.equal(app.showsCaps(), true);
  app.newLinkMode = 'both';
  assert.equal(app.showsCaps(), true);
});

test('a link mode renders a human label', () => {
  const app = modal();
  assert.equal(app.modeLabel('drop'), 'Upload only');
});

test('only a file target offers user-to-user sharing', () => {
  const app = modal();
  app.nodeType = 'file';
  assert.equal(app.canShareWithPeople(), true);
  app.nodeType = 'folder';
  assert.equal(app.canShareWithPeople(), false);
});

test('user and group entries with the same id are distinct', () => {
  const app = modal();
  app.shares = [
    { type: 'user', id: 3, username: 'ann', permission: 'ro' },
    { type: 'group', id: 3, name: 'Team', permission: 'rw' },
  ];
  const keys = Array.from(app.displayList).map(e => e.key);
  assert.deepStrictEqual(keys, ['user:3', 'group:3']);

  app.stageRemove('group:3');
  const removed = Array.from(app.displayList).filter(e => e._removed).map(e => e.key);
  assert.deepStrictEqual(removed, ['group:3']);
});

test('a group already shared with is not offered again', () => {
  const app = modal();
  app.groups = [{ id: 1, name: 'Team' }, { id: 2, name: 'Ops' }];
  app.shares = [{ type: 'group', id: 1, name: 'Team', permission: 'ro' }];
  assert.deepStrictEqual(Array.from(app.selectableGroups()).map(g => g.id), [2]);

  app.stageAdd({ type: 'group', id: 2, name: 'Ops' });
  assert.deepStrictEqual(Array.from(app.selectableGroups()), []);
  assert.equal(app.pendingAdds.length, 1);
});

test('a share target maps to its request body', () => {
  const app = modal();
  assert.deepStrictEqual({ ...app.targetBody('group', 7) }, { group: 7 });
  assert.deepStrictEqual({ ...app.targetBody('user', 7) }, { shared_with: 7 });
  assert.deepStrictEqual({ ...app.splitKey('group:7') }, { type: 'group', id: '7' });
});

test('a failed group listing clears the groups from the previous open', async () => {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/share_modal.js', {
    fetch: async () => ({ ok: false, status: 500 }),
  });
  const app = ctx.shareModal();
  app.groups = [{ id: 1, name: 'Stale' }];
  await app.loadGroups();
  assert.deepStrictEqual(Array.from(app.groups), []);
});

function linkModal(fetchImpl) {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/share_modal.js', {
    fetch: fetchImpl,
    getCSRFToken: () => 'token',
    gettext: (text) => text,
  });
  const app = ctx.shareModal();
  app.fileUuid = 'f1';
  return app;
}

const reply = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

test('a revoke the server refused keeps the link listed and says so', async () => {
  const app = linkModal(async () => reply(500, {}));
  app.shareLinks = [{ uuid: 'l1' }];
  await app.deleteShareLink('l1');
  assert.equal(app.shareLinks.length, 1);
  assert.equal(app.linkError, 'The link could not be revoked.');
});

test('a revoked link leaves the list', async () => {
  const app = linkModal(async () => reply(204, null));
  app.shareLinks = [{ uuid: 'l1' }, { uuid: 'l2' }];
  await app.deleteShareLink('l1');
  assert.deepStrictEqual(app.shareLinks.map(l => l.uuid), ['l2']);
  assert.equal(app.linkError, '');
});

test('a refused link creation shows the reason and keeps the form open', async () => {
  const app = linkModal(async () => reply(400, { password: ['Too short.'] }));
  app.showLinkForm = true;
  await app.createShareLink();
  assert.equal(app.showLinkForm, true);
  assert.equal(app.linkError, 'Too short.');
  assert.equal(app.creatingLink, false);
});

test('a created link sends its expiry day, password and mode, then resets the form', async () => {
  const requests = [];
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/share_modal.js', {
    fetch: async (url, options = {}) => {
      requests.push([options.method || 'GET', url, options.body ? JSON.parse(options.body) : null]);
      return reply(options.method === 'POST' ? 201 : 200, []);
    },
    getCSRFToken: () => 'token',
    getUserTimeZone: () => 'Europe/Paris',
    wallClockToIso: (wall, tz) => `${wall}@${tz}`,
  });
  const app = ctx.shareModal();
  app.fileUuid = 'f1';
  app.nodeType = 'folder';
  app.showLinkForm = true;
  app.newLinkExpiry = '2030-01-31';
  app.newLinkPassword = 'secret';
  app.linkPasswordRevealed = true;
  app.newLinkMode = 'drop';
  app.newLinkMaxCount = '3';

  await app.createShareLink();

  assert.deepStrictEqual(requests[0], [
    'POST',
    '/api/v1/files/f1/share-links',
    { expires_at: '2030-01-31T23:59:59@Europe/Paris', password: 'secret', mode: 'drop', max_file_count: 3 },
  ]);
  assert.deepStrictEqual(
    [app.showLinkForm, app.newLinkExpiry, app.newLinkPassword, app.linkPasswordRevealed, app.newLinkMode],
    [false, '', '', false, 'read'],
  );
});

test('a copied link is announced', async () => {
  const copied = [];
  const alerts = [];
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/share_modal.js', {
    navigator: { clipboard: { writeText: async (text) => { copied.push(text); } } },
    AppAlert: { success: (message) => alerts.push(message) },
  });
  const app = ctx.shareModal();
  app.copyLinkUrl('https://x/s/t');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepStrictEqual([copied, alerts], [['https://x/s/t'], ['Link copied to clipboard']]);
});

test('one QR code is unfolded at a time, and its button folds it back', () => {
  const app = modal();
  app.toggleLinkQr('l1');
  assert.equal(app.qrLinkUuid, 'l1');
  app.toggleLinkQr('l2');
  assert.equal(app.qrLinkUuid, 'l2');
  app.toggleLinkQr('l2');
  assert.equal(app.qrLinkUuid, null);
});

test('a QR code that failed to load is asked for again on the next unfold', () => {
  const app = modal();
  app.toggleLinkQr('l1');
  app.qrFailed = true;
  app.toggleLinkQr('l1');
  app.toggleLinkQr('l1');
  assert.equal(app.qrFailed, false);
});

test('the QR code is shown as svg and downloaded as png, from where the link says it is', () => {
  const app = modal();
  const link = { uuid: 'l1', qrcode_url: '/api/v1/files/share-links/l1/qrcode' };
  assert.equal(app.linkQrUrl(link), '/api/v1/files/share-links/l1/qrcode?kind=svg');
  assert.equal(app.linkQrUrl(link, 'png'), '/api/v1/files/share-links/l1/qrcode?kind=png');
});

test('closing the modal folds the QR code, so reopening it starts from the list', () => {
  const app = modal();
  app.$refs = {};
  app.toggleLinkQr('l1');
  app.closeModal();
  assert.equal(app.qrLinkUuid, null);
});
