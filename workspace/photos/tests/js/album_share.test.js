const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// A fetch answering from `routes` ("METHOD url" -> body), recording every
// request.
function fakeFetch(routes, requests = [], { failing = [] } = {}) {
  return (url, opts = {}) => {
    const method = opts.method || 'GET';
    requests.push([method, url, opts.body ? JSON.parse(opts.body) : undefined]);
    const key = `${method} ${url}`;
    const ok = !failing.includes(key);
    const body = routes[key];
    return Promise.resolve({ ok, status: ok ? 200 : 500, json: () => Promise.resolve(body) });
  };
}

const MEMBERS = {
  owner: { type: 'user', id: 1, username: 'owner' },
  shares: [{ type: 'user', id: 2, username: 'bob', role: 'contributor' }],
  allow_download: true,
};

function modal(routes = {}, { failing = [] } = {}) {
  const requests = [];
  const alerts = [];
  const events = [];
  const dialog = { open: false, showModal() { this.open = true; }, close() { this.open = false; } };
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/album_share.js', {
    fetch: fakeFetch({
      'GET /api/v1/photos/albums/al1/shares': MEMBERS,
      'GET /api/v1/groups': [{ id: 7, name: 'Team' }, { id: 8, name: 'Family' }],
      'GET /api/v1/photos/albums/al1/links': [],
      ...routes,
    }, requests, { failing }),
    getCSRFToken: () => 't',
    addEventListener() {},
    dispatchEvent: (event) => events.push(event),
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    AppAlert: { success: (m) => alerts.push(['success', m]), error: (m) => alerts.push(['error', m]) },
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    wallClockToIso: (wall) => `${wall}Z`,
  });
  const m = ctx.albumShareModal();
  m.$refs = { dialog };
  return { m, requests, alerts, events, dialog };
}

async function opened(routes, options) {
  const parts = modal(routes, options);
  parts.m.openModal({ uuid: 'al1', title: 'Trip' });
  await new Promise((resolve) => setImmediate(resolve));
  return parts;
}

test('opening loads the members, the groups and the links', async () => {
  const { m, dialog } = await opened();

  assert.equal(dialog.open, true);
  assert.equal(m.albumTitle, 'Trip');
  assert.equal(m.owner.username, 'owner');
  assert.deepEqual(m.displayList().map((e) => [e.key, e.role]), [['user:2', 'contributor']]);
  assert.equal(m.allowDownload(), true);
  assert.equal(m.hasChanges(), false);
});

test('a staged member joins the list as a viewer, and leaves it unsaved', async () => {
  const { m } = await opened();

  m.stageAdd({ type: 'user', id: 3, username: 'carol' });
  m.stageAdd({ type: 'user', id: 3, username: 'carol' });
  m.stageAdd({ type: 'user', id: 1, username: 'owner' });

  const added = m.displayList().filter((e) => e._pending);
  assert.deepEqual(added.map((e) => [e.key, e.role]), [['user:3', 'viewer']]);
  m.stageRemove('user:3');
  assert.equal(m.hasChanges(), false);
});

test('removing a member can be undone, and adding them back undoes it', async () => {
  const { m } = await opened();

  m.stageRemove('user:2');
  assert.equal(m.displayList()[0]._removed, true);
  m.stageAdd({ type: 'user', id: 2, username: 'bob' });
  assert.equal(m.displayList()[0]._removed, false);
  assert.equal(m.hasChanges(), false);
});

test('a role change back to the saved one is no change', async () => {
  const { m } = await opened();

  m.stageRoleChange('user:2', 'manager', false);
  assert.equal(m.displayList()[0].role, 'manager');
  m.stageRoleChange('user:2', 'contributor', false);
  assert.equal(m.hasChanges(), false);
});

test('the group picker leaves out the groups already members', async () => {
  const { m } = await opened();

  m.stageAdd({ type: 'group', id: 7, name: 'Team' });

  assert.deepEqual(m.selectableGroups().map((g) => g.name), ['Family']);
});

test('the group owning a group album is not offered', async () => {
  const { m } = await opened({
    'GET /api/v1/photos/albums/al1/shares': { ...MEMBERS, owner: { type: 'group', id: 8, name: 'Family' } },
  });

  assert.deepEqual(m.selectableGroups().map((g) => g.name), ['Team']);
  assert.equal(m.entrySubtitle(m.owner), 'Group');
});

test('saving sends one request per change and announces it', async () => {
  const { m, requests, alerts, events, dialog } = await opened({
    'POST /api/v1/photos/albums/al1/shares': {},
    'DELETE /api/v1/photos/albums/al1/shares': null,
    'PATCH /api/v1/photos/albums/al1': {},
  });
  m.stageAdd({ type: 'group', id: 7, name: 'Team' });
  m.stageRoleChange('group:7', 'contributor', true);
  m.stageAdd({ type: 'user', id: 3, username: 'carol' });
  m.stageRemove('user:3');
  m.stageRoleChange('user:2', 'manager', false);
  m.stageAllowDownload(false);
  requests.length = 0;

  await m.save();

  assert.deepEqual(requests.map((r) => [r[0], r[1], r[2]]), [
    ['POST', '/api/v1/photos/albums/al1/shares', { group: 7, role: 'contributor' }],
    ['POST', '/api/v1/photos/albums/al1/shares', { shared_with: 2, role: 'manager' }],
    ['PATCH', '/api/v1/photos/albums/al1', { allow_download: false }],
  ]);
  assert.deepEqual(alerts, [['success', 'Sharing updated']]);
  assert.equal(events[0].type, 'album-shares-changed');
  assert.equal(dialog.open, false);
});

test('a removal names its target, a project by uuid', async () => {
  const { m, requests } = await opened({
    'GET /api/v1/photos/albums/al1/shares': {
      ...MEMBERS,
      shares: [{ type: 'project', id: 'p-1', name: 'Website', role: 'viewer' }],
    },
  });
  m.stageRemove('project:p-1');
  requests.length = 0;

  await m.save();

  assert.deepEqual(requests[0].slice(0, 3), ['DELETE', '/api/v1/photos/albums/al1/shares', { project: 'p-1' }]);
  assert.equal(m.entrySubtitle({ type: 'project' }), 'Project');
  assert.equal(m.entryName({ type: 'project', name: 'Website' }), 'Website');
});

test('failed changes are counted', async () => {
  const { m, alerts } = await opened({}, { failing: ['POST /api/v1/photos/albums/al1/shares'] });
  m.stageAdd({ type: 'user', id: 3, username: 'carol' });
  m.stageAdd({ type: 'user', id: 4, username: 'dave' });

  await m.save();

  assert.deepEqual(alerts, [['error', 'Some changes failed (2 errors)']]);
});

test('creating a link sends its settings and copies it', async () => {
  const link = { uuid: 'l1', url: 'https://x/photos/shared/t', allow_download: true, view_count: 0 };
  const { m, requests, alerts } = await opened({ 'POST /api/v1/photos/albums/al1/links': link });
  m.showLinkForm = true;
  m.newLinkExpiry = '2030-01-31';
  m.newLinkPassword = 'secret';
  m.newLinkAllowDownload = true;

  await m.createLink();

  assert.deepEqual(requests.at(-1).slice(0, 3), [
    'POST',
    '/api/v1/photos/albums/al1/links',
    { allow_download: true, expires_at: '2030-01-31T23:59:59Z', password: 'secret' },
  ]);
  assert.equal(m.links[0].uuid, 'l1');
  assert.equal(m.showLinkForm, false);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(alerts, [['success', 'Link copied to clipboard']]);
});

test('a failed link creation says so', async () => {
  const { m, alerts } = await opened({}, { failing: ['POST /api/v1/photos/albums/al1/links'] });

  await m.createLink();

  assert.deepEqual(alerts, [['error', 'Failed to create the link']]);
  assert.equal(m.creatingLink, false);
});

test('revoking a link drops it', async () => {
  const { m, requests } = await opened({
    'GET /api/v1/photos/albums/al1/links': [{ uuid: 'l1' }, { uuid: 'l2' }],
    'DELETE /api/v1/photos/albums/al1/links/l1': null,
  });

  await m.revokeLink('l1');

  assert.deepEqual(requests.at(-1).slice(0, 2), ['DELETE', '/api/v1/photos/albums/al1/links/l1']);
  assert.deepEqual(m.links.map((l) => l.uuid), ['l2']);
});

test('labels', () => {
  const { m } = modal();

  assert.equal(m.formatExpiry(null), 'Permanent');
  assert.match(m.formatExpiry('2030-01-31T12:00:00Z'), /^Until /);
  assert.equal(m.viewsLabel(1), '1 view');
  assert.equal(m.viewsLabel(0), '0 views');
  assert.equal(m.roleHint('viewer'), 'Browses the album');
  assert.equal(m.roleHint('nope'), '');
  assert.equal(m.entrySubtitle({ type: 'user', first_name: 'Bob', last_name: 'B' }), 'Bob B');
});
