const test = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

function mixin(documentData = {}) {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: {
      getElementById: (id) => (id in documentData ? { textContent: JSON.stringify(documentData[id]) } : null),
    },
  });
  return ctx.photosFacesMixin();
}

const ALICE = { uuid: 'a', photo_count: 7, cover_url: '/a' };
const BOB = { uuid: 'b', photo_count: 1, cover_url: '/b' };
const CAROL = { uuid: 'c', photo_count: 3, cover_url: '/c' };

test('the page state is read from the embedded JSON', () => {
  const faces = mixin({ 'photos-faces-enabled': true, 'photos-cluster-data': { uuid: 'a', hidden: false } });

  assert.equal(faces.facesEnabled(), true);
  assert.equal(faces.currentCluster().uuid, 'a');
});

test('without the embedded JSON, faces are off and no person is on screen', () => {
  const faces = mixin();

  assert.equal(faces.facesEnabled(), false);
  assert.equal(faces.currentCluster(), null);
});

test('a face is never offered its own cluster, nor one another face of the photo is in', () => {
  const faces = mixin();
  faces.facesDialog.clusters = [ALICE, BOB, CAROL];
  faces.facesDialog.faces = [
    { uuid: 'f1', cluster: 'a', assignment: 'auto' },
    { uuid: 'f2', cluster: 'b', assignment: 'auto' },
  ];

  const offered = Array.from(faces.pickableClusters(faces.facesDialog.faces[0]), (c) => c.uuid);

  assert.deepEqual(offered, ['c']);
});

test('a face says where it stands', () => {
  const faces = mixin();
  faces.facesDialog.clusters = [ALICE, BOB];

  assert.equal(faces.faceLabel({ cluster: 'a' }), '7 photos');
  assert.equal(faces.faceLabel({ cluster: 'b' }), '1 photo');
  assert.equal(faces.faceLabel({ cluster: 'hidden-one' }), 'Hidden person');
  assert.equal(faces.faceLabel({ cluster: null, assignment: 'rejected' }), 'Left out of grouping');
  assert.equal(faces.faceLabel({ cluster: null, assignment: 'auto' }), 'Not grouped yet');
});

test('merge selection toggles', () => {
  const faces = mixin();

  faces.toggleMergeSelection(BOB);
  faces.toggleMergeSelection(CAROL);
  faces.toggleMergeSelection(BOB);

  assert.deepEqual(Array.from(faces.mergeDialog.selected), ['c']);
  assert.equal(faces.isMergeSelected(CAROL), true);
  assert.equal(faces.isMergeSelected(BOB), false);
});

test('a face starting a new person joins it, confirmed, and the list grows', async () => {
  const requests = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null, cookie: '' },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      requests.push([url, options.method, JSON.parse(options.body)]);
      return { ok: true, status: 201, json: async () => ({ uuid: 'new', photo_count: 1, cover_url: '/n' }) };
    },
  });
  const faces = ctx.photosFacesMixin();
  faces.facesDialog.clusters = [ALICE];
  faces.facesDialog.faces = [{ uuid: 'f1', cluster: 'a', assignment: 'auto' }];

  await faces.startCluster(faces.facesDialog.faces[0]);

  assert.deepEqual(requests.map((r) => [r[0], r[1], { ...r[2] }]), [
    ['/api/v1/photos/clusters', 'POST', { face: 'f1' }],
  ]);
  assert.equal(faces.facesDialog.faces[0].cluster, 'new');
  assert.equal(faces.facesDialog.faces[0].assignment, 'confirmed');
  assert.deepEqual(Array.from(faces.facesDialog.clusters, (c) => c.uuid), ['a', 'new']);
  assert.equal(faces.facesDialog.changed, true);
});

test('the progress badge follows the total, which moves while the page is open', async () => {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
    getCSRFToken: () => 'token',
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ enabled: true, analyzed: 12, total: 15 }) }),
  });
  const progress = ctx.facesProgress(10, 10);
  progress.$ajax = () => {};

  await progress.poll();

  assert.equal(progress.analyzed, 12);
  assert.equal(progress.total, 15);
});
