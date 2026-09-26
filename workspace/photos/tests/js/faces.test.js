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

test('a face says who it is', () => {
  const faces = mixin();
  faces.facesDialog.clusters = [{ ...ALICE, person: 'p-ann', person_name: 'Ann' }, BOB];

  assert.equal(faces.faceLabel({ cluster: 'a' }), 'Ann');
  assert.equal(faces.faceLabel({ cluster: 'b' }), 'Unnamed, 1 photo');
  assert.equal(faces.faceLabel({ cluster: 'hidden-one' }), 'Hidden person');
  assert.equal(faces.faceLabel({ cluster: null, assignment: 'rejected' }), 'Left out of grouping');
  assert.equal(faces.faceLabel({ cluster: null, assignment: 'auto' }), 'Not grouped yet');
});

test('the picker never offers someone already in the photo, nor the face itself', () => {
  const faces = mixin();
  faces.facesDialog.clusters = [
    { uuid: 'c1', person: 'p-ann' },
    { uuid: 'c2', person: 'p-bea' },
    { uuid: 'c3', person: null },
  ];
  faces.facesDialog.faces = [
    { uuid: 'f1', cluster: 'c1' },
    { uuid: 'f2', cluster: 'c2' },
  ];
  faces.facesDialog.persons = [
    { uuid: 'p-ann', name: 'Ann' },
    { uuid: 'p-bea', name: 'Bea' },
    { uuid: 'p-cid', name: 'Cid' },
  ];

  const offered = Array.from(faces.pickablePersons(faces.facesDialog.faces[0]), (p) => p.name);

  // Ann is the face itself, Bea is the other face of the photo.
  assert.deepEqual(offered, ['Cid']);
  assert.deepEqual(
    Array.from(faces.pickableClusters(faces.facesDialog.faces[0]), (c) => c.uuid),
    ['c3'],
  );
});

test('a name is offered for creation unless a contact already has it', () => {
  const faces = mixin();
  faces.nameDialog.results = [{ uuid: 'p', name: 'Alice' }];

  faces.nameDialog.query = 'alice';
  assert.equal(faces.canCreateName(), false);
  faces.nameDialog.query = 'Alicia';
  assert.equal(faces.canCreateName(), true);
  faces.nameDialog.query = '  ';
  assert.equal(faces.canCreateName(), false);
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

test('a face starting a new person is sent, then the dialog reloads', async () => {
  const requests = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      requests.push([url, options.method]);
      const body = url.startsWith('/api/v1/photos/files/') ? [{ uuid: 'f1', cluster: 'new' }] : [];
      return { ok: true, status: options.method === 'POST' ? 201 : 200, json: async () => body };
    },
  });
  const faces = ctx.photosFacesMixin();
  faces.facesDialog.photo = { uuid: 'photo-1' };
  faces.facesDialog.faces = [{ uuid: 'f1', cluster: 'a', assignment: 'auto' }];

  await faces.startCluster(faces.facesDialog.faces[0]);

  assert.deepEqual(requests[0], ['/api/v1/photos/clusters', 'POST']);
  assert.equal(faces.facesDialog.faces[0].cluster, 'new');
  assert.equal(faces.facesDialog.changed, true);
});

test('a merge refused for two names asks which one to keep, then sends it', async () => {
  const bodies = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => ({ close() {} }) },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      bodies.push(body);
      if (!body.person) {
        return {
          ok: false,
          status: 400,
          json: async () => ({ detail: 'pick', persons: [{ uuid: 'p-ann', name: 'Ann' }, { uuid: 'p-bea', name: 'Bea' }] }),
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
    AppAlert: { error() {} },
  });
  const faces = ctx.photosFacesMixin();
  faces.$ajax = async () => {};
  faces.mergeDialog.target = { uuid: 't' };
  faces.mergeDialog.selected = ['s'];

  await faces.submitMerge();
  assert.deepEqual(Array.from(faces.mergeDialog.choices, (c) => c.name), ['Ann', 'Bea']);
  assert.equal(faces.mergeDialog.person, 'p-ann');

  faces.mergeDialog.person = 'p-bea';
  await faces.submitMerge();
  assert.equal(bodies[1].person, 'p-bea');
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

test('a merge offers each named person once, never the target itself', () => {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
  });
  const clusters = [
    { uuid: 't', person: null },
    { uuid: 'nina-big', person: 'p-nina' },
    { uuid: 'u1', person: null },
    { uuid: 'nina-small', person: 'p-nina' },
    { uuid: 'lea', person: 'p-lea' },
  ];

  const offered = Array.from(ctx.mergeCandidates(clusters, { uuid: 't', person: null }), (c) => c.uuid);
  assert.deepEqual(offered, ['nina-big', 'u1', 'lea']);

  const fromLea = Array.from(ctx.mergeCandidates(clusters, { uuid: 'lea', person: 'p-lea' }), (c) => c.uuid);
  assert.deepEqual(fromLea, ['t', 'nina-big', 'u1']);
});

function coverMixin(confirmAnswer) {
  const requests = [];
  const asked = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      requests.push([url, options.method]);
      return { ok: true, status: 204, json: async () => null };
    },
    AppDialog: { confirm: async (opts) => { asked.push(opts.title); return confirmAnswer; } },
    AppAlert: { success() {}, error() {} },
  });
  return { faces: ctx.photosFacesMixin(), requests, asked };
}

test('a new cover becomes the contact photo at once when the contact has none', async () => {
  const { faces, requests, asked } = coverMixin(false);

  await faces._offerCoverToContact({ name: 'Ann', has_avatar: false }, 'c1');

  assert.deepEqual(asked, []);
  assert.deepEqual(requests, [['/api/v1/photos/clusters/c1/avatar', 'POST']]);
});

test('a contact who has a photo keeps it unless the user says to replace it', async () => {
  const declined = coverMixin(false);
  await declined.faces._offerCoverToContact({ name: 'Ann', has_avatar: true }, 'c1');
  assert.equal(declined.asked.length, 1);
  assert.deepEqual(declined.requests, []);

  const accepted = coverMixin(true);
  await accepted.faces._offerCoverToContact({ name: 'Ann', has_avatar: true }, 'c1');
  assert.deepEqual(accepted.requests, [['/api/v1/photos/clusters/c1/avatar', 'POST']]);
});

test('an unnamed cluster has no contact to offer its cover to', async () => {
  const { faces, requests, asked } = coverMixin(true);

  await faces._offerCoverToContact(null, 'c1');

  assert.deepEqual(asked, []);
  assert.deepEqual(requests, []);
});

function review(documentData = {}) {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: {
      getElementById: (id) => (id in documentData ? { textContent: JSON.stringify(documentData[id]) } : null),
    },
  });
  return { ctx, component: ctx.facesReview() };
}

const NINA = { uuid: 'n', name: 'Nina Petit', photo_count: 4 };
const NOAH = { uuid: 'o', name: 'Noah', photo_count: 0 };

test('with nothing typed, the suggested person comes first and only once', () => {
  const { ctx } = review();

  const options = Array.from(ctx.reviewOptions('', [NOAH, NINA], NINA));

  assert.deepEqual(options.map((o) => [o.person.uuid, o.suggested]), [['n', true], ['o', false]]);
});

test('a typed name hides the suggestion and offers to add the name', () => {
  const { ctx } = review();

  const options = Array.from(ctx.reviewOptions('Léa', [NINA], NINA));

  assert.deepEqual(options.map((o) => o.kind), ['person', 'create']);
  assert.equal(options[1].name, 'Léa');
});

test('adding a name is not offered when a contact has exactly that name', () => {
  const { ctx } = review();

  const options = Array.from(ctx.reviewOptions(' nina petit ', [NINA], null));

  assert.deepEqual(options.map((o) => o.kind), ['person']);
});

test('confirming a person sends one request per cluster, the marked faces rejected', () => {
  const { ctx } = review();
  const faces = [
    { uuid: 'f1', cluster: 'c1' },
    { uuid: 'f2', cluster: 'c1' },
    { uuid: 'f3', cluster: 'c2' },
  ];

  const requests = JSON.parse(JSON.stringify(ctx.reviewRequests(faces, { f2: true, f3: false })));

  assert.deepEqual(requests, [
    { cluster: 'c1', body: { confirmed: ['f1'], rejected: ['f2'] } },
    { cluster: 'c2', body: { confirmed: ['f3'], rejected: [] } },
  ]);
});

test('the queues are read from the embedded JSON', () => {
  const { component } = review({
    'photos-review-data': {
      unnamed: [{ uuid: 'c1', suggestion: null }],
      doubts: [{ person: NINA, total: 3, faces: [] }, { person: NOAH, total: 2, faces: [] }],
    },
  });
  component.searchReviewNames = () => {};

  component.init();

  assert.equal(component.current().uuid, 'c1');
  assert.equal(component.doubtCount(), 5);
});

test('the confirm button says how many faces go each way', () => {
  const { component } = review();
  const block = { faces: [{ uuid: 'f1' }, { uuid: 'f2' }] };

  assert.equal(component.settleLabel(block), 'Confirm all');
  component.toggleMark(block.faces[0]);
  assert.equal(component.settleLabel(block), 'Confirm 1, remove 1');
  component.toggleMark(block.faces[1]);
  assert.equal(component.settleLabel(block), 'Remove 2');
});

test('skipping goes round the queue', () => {
  const { component } = review();
  component.unnamed = [{ uuid: 'a' }, { uuid: 'b' }];
  component.searchReviewNames = () => {};
  component.$nextTick = () => {};

  component.skip();
  assert.equal(component.current().uuid, 'b');
  component.skip();
  assert.equal(component.current().uuid, 'a');
  component.previous();
  assert.equal(component.current().uuid, 'b');
});

test('not this person detaches the face of every selected photo, past a failure', async () => {
  const requests = [];
  const errors = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: {
      getElementById: (id) => (id === 'photos-cluster-data' ? { textContent: JSON.stringify({ clusters: ['c1'] }) } : null),
    },
    getCSRFToken: () => 't',
    AppAlert: { error: (m) => errors.push(m) },
    fetch: async (url, opts) => {
      requests.push([opts.method, url]);
      const photo = /files\/([^/]+)\/faces/.exec(url);
      if (photo) {
        return { ok: true, json: async () => [{ uuid: `face-${photo[1]}`, cluster: 'c1' }] };
      }
      return { ok: !url.endsWith('face-p2'), status: 500, json: async () => ({}) };
    },
  });
  const faces = ctx.photosFacesMixin();
  let reloads = 0;
  faces.selection = ['p1', 'p2', 'p3'];
  faces.selectionBusy = false;
  faces.closeSelectionMenu = () => {};
  faces._reloadView = async () => { reloads += 1; };

  await faces.rejectSelectionFromCluster();

  assert.deepEqual(
    requests.filter(([method]) => method === 'PATCH').map(([, url]) => url),
    ['/api/v1/photos/faces/face-p1', '/api/v1/photos/faces/face-p2', '/api/v1/photos/faces/face-p3'],
  );
  assert.deepEqual(errors, ['Could not remove 1 photo']);
  assert.equal(faces.selectionBusy, false);
  assert.equal(reloads, 1);
});

test('a failed reload after not this person is reported, not swallowed', async () => {
  const errors = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: {
      getElementById: (id) => (id === 'photos-cluster-data' ? { textContent: JSON.stringify({ clusters: ['c1'] }) } : null),
    },
    getCSRFToken: () => 't',
    AppAlert: { error: (m) => errors.push(m) },
    fetch: async (url) => ({
      ok: true,
      json: async () => (url.includes('/files/') ? [{ uuid: 'f1', cluster: 'c1' }] : {}),
    }),
  });
  const faces = ctx.photosFacesMixin();
  faces.selection = ['p1'];
  faces.selectionBusy = false;
  faces.closeSelectionMenu = () => {};
  faces._reloadView = () => Promise.reject(new Error('offline'));

  await faces.rejectSelectionFromCluster();

  assert.deepEqual(errors, ['offline']);
});
