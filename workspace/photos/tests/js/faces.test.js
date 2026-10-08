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

test('a face is offered every cluster but its own, the one of another face marked taken', () => {
  const faces = mixin();
  faces.facesDialog.clusters = [ALICE, BOB, CAROL];
  faces.facesDialog.faces = [
    { uuid: 'f1', cluster: 'a', assignment: 'auto' },
    { uuid: 'f2', cluster: 'b', assignment: 'auto' },
  ];
  const [first] = faces.facesDialog.faces;

  const offered = Array.from(faces.pickableClusters(first), (c) => c.uuid);

  assert.deepEqual(offered, ['b', 'c']);
  assert.equal(faces.faceClusterTaken(first, BOB).uuid, 'f2');
  assert.equal(faces.faceClusterTaken(first, CAROL), null);
});

test('picking a cluster another face of the photo is in sends the face there as it is', async () => {
  const requests = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      requests.push([url, options.method, options.body && JSON.parse(options.body)]);
      return { ok: true, status: 200, json: async () => [] };
    },
  });
  const faces = ctx.photosFacesMixin();
  faces.facesDialog.photo = { uuid: 'photo-1' };
  faces.facesDialog.clusters = [{ uuid: 'b', person: null }];
  faces.facesDialog.faces = [{ uuid: 'f1', cluster: null }, { uuid: 'f2', cluster: 'b' }];

  await faces.assignFace(faces.facesDialog.faces[0], { uuid: 'b' });

  assert.deepEqual(requests[0], ['/api/v1/photos/faces/f1', 'PATCH', { cluster: 'b' }]);
});

test('a face says who it is', () => {
  const faces = mixin();
  faces.facesDialog.clusters = [{ ...ALICE, person: 'p-ann', person_name: 'Ann' }, BOB];

  assert.equal(faces.faceLabel({ cluster: 'a' }), 'Ann');
  assert.equal(faces.faceLabel({ cluster: 'b' }), 'Unnamed, 1 photo');
  assert.equal(faces.faceLabel({ cluster: 'hidden-one' }), 'Hidden person');
  assert.equal(faces.faceLabel({ cluster: null, assignment: 'rejected' }), 'Left out of grouping');
  assert.equal(faces.faceLabel({ cluster: null, assignment: 'duplicate' }), 'Already named in this photo');
  assert.equal(faces.faceLabel({ cluster: null, assignment: 'auto' }), 'Not grouped yet');
  faces.facesDialog.photo = { mediaType: 'video' };
  assert.equal(faces.faceLabel({ cluster: null, assignment: 'duplicate' }), 'Already named in this video');
});

test('the picker offers someone already in the photo, as taken, never the face itself', () => {
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

  const [first] = faces.facesDialog.faces;
  const offered = Array.from(faces.pickablePersons(first), (p) => p.name);

  // Ann is the face itself; Bea is the other face of the photo, offered to
  // say this face is her instead.
  assert.deepEqual(offered, ['Bea', 'Cid']);
  assert.equal(faces.facePersonTaken(first, { uuid: 'p-bea' }).uuid, 'f2');
  assert.equal(faces.facePersonTaken(first, { uuid: 'p-cid' }), null);
  assert.deepEqual(Array.from(faces.pickableClusters(first), (c) => c.uuid), ['c3']);
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
  faces.mergeDialog.target = { uuid: 't', person: 'p-bea' };
  faces.mergeDialog.clusters = [{ uuid: 's', members: ['s', 's2'] }];
  faces.mergeDialog.selected = ['s'];

  await faces.submitMerge();
  assert.deepEqual(Array.from(faces.mergeDialog.choices, (c) => c.name), ['Ann', 'Bea']);
  // The name of the person merged into is the one offered first.
  assert.equal(faces.mergeDialog.person, 'p-bea');
  // A named person is merged with every cluster of theirs.
  assert.deepEqual(Array.from(bodies[0].clusters), ['s', 's2']);

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

test('a typed name narrows the merge candidates, accents and case aside', () => {
  const faces = mixin();
  faces.mergeDialog.clusters = [
    { uuid: 'lea', person_name: 'Léa Martin' },
    { uuid: 'u1', person_name: null },
    { uuid: 'nina', person_name: 'Nina' },
  ];
  const visible = () => Array.from(faces.visibleMergeClusters(), (c) => c.uuid);

  assert.deepEqual(visible(), ['lea', 'u1', 'nina']);
  faces.mergeDialog.query = '  LEA ';
  assert.deepEqual(visible(), ['lea']);
  faces.mergeDialog.query = 'zoe';
  assert.deepEqual(visible(), []);
});

test('a picked candidate stays in sight whatever is typed', () => {
  const faces = mixin();
  faces.mergeDialog.clusters = [
    { uuid: 'u1', person_name: null },
    { uuid: 'nina', person_name: 'Nina' },
  ];
  faces.toggleMergeSelection({ uuid: 'u1' });
  faces.mergeDialog.query = 'nina';

  assert.deepEqual(Array.from(faces.visibleMergeClusters(), (c) => c.uuid), ['u1', 'nina']);
});

test('the search is cleared each time the merge dialog opens', async () => {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => ({ showModal() {} }) },
    getCSRFToken: () => 'token',
    fetch: async () => ({ ok: true, status: 200, json: async () => [] }),
  });
  const faces = ctx.photosFacesMixin();
  faces.mergeDialog.query = 'nina';

  await faces.openMergeDialog({ uuid: 't', person: null });

  assert.equal(faces.mergeDialog.query, '');
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

function reviewPage({ cards = [], left = cards.length, responses = {}, alerts = [] } = {}) {
  const requests = [];
  const events = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: {
      getElementById: (id) => (id === 'photos-review-data' ? { textContent: JSON.stringify({ cards, left }) } : null),
    },
    getCSRFToken: () => 'token',
    location: { pathname: '/photos/people/review', search: '' },
    setTimeout,
    clearTimeout,
    CustomEvent: class { constructor(type) { this.type = type; } },
    dispatchEvent: (event) => { events.push(event.type); },
    fetch: async (url, options) => {
      // The contact search is not what these tests look at.
      if (url.includes('/persons')) return { ok: true, status: 200, json: async () => [] };
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, method: options.method, body });
      const answer = responses[url];
      const data = typeof answer === 'function' ? answer(body) : answer;
      if (data instanceof Error) return { ok: false, status: 400, json: async () => ({ detail: data.message }) };
      return { ok: true, status: data === undefined ? 204 : 200, json: async () => data };
    },
    AppAlert: {
      success: (message, options) => alerts.push({ type: 'success', message, options }),
      warning: (message) => alerts.push({ type: 'warning', message }),
      error: (message) => alerts.push({ type: 'error', message }),
    },
  });
  const component = ctx.facesReview();
  component.reloads = 0;
  component.$ajax = () => { component.reloads += 1; };
  component.$nextTick = () => {};
  component.$refs = {};
  component.init();
  return { ctx, component, requests, alerts, events };
}

const faces = (...uuids) => uuids.map((uuid) => ({ uuid }));
const BATCH = '/api/v1/photos/faces/batch';
const batchDone = (body) => ({ done: body.faces, skipped: [], undo: `t-${body.action}` });

const clusterCard = (key, ...uuids) => ({ key, kind: 'cluster', cluster: key, guess: null, faces: faces(...uuids) });

test('naming an unnamed cluster takes the faces left out of it first', async () => {
  const { component, requests } = reviewPage({
    cards: [clusterCard('c1', 'f1', 'f2', 'f3'), clusterCard('c2', 'f4')],
    responses: { [BATCH]: batchDone },
  });
  const card = component.cards[0];
  component.toggleExcluded(card, 'f2');

  await component.pick(card, { kind: 'create', name: 'Léa' });

  assert.deepEqual(requests.map((r) => [r.url, r.method, r.body]), [
    [BATCH, 'POST', { action: 'reject', faces: ['f2'] }],
    ['/api/v1/photos/clusters/c1', 'PATCH', { new_person: 'Léa' }],
  ]);
  assert.deepEqual(Array.from(component.cards, (c) => c.key), ['c2']);
});

test('each card keeps its own faces left out', () => {
  const { component } = reviewPage({ cards: [clusterCard('c1', 'f1', 'f2'), clusterCard('c2', 'f3')] });
  const [first, second] = component.cards;

  component.toggleExcluded(first, 'f1');

  assert.equal(component.keptCount(first), 1);
  assert.equal(component.keptCount(second), 1);
  assert.equal(component.isExcluded(second, 'f1'), false);
});

test('the undo of a named cluster clears the name, then puts back the faces left out', async () => {
  const { component, requests, alerts, events } = reviewPage({
    cards: [clusterCard('c1', 'f1', 'f2'), clusterCard('c2', 'f3')],
    responses: { [BATCH]: batchDone, '/api/v1/photos/faces/undo': { restored: 1 } },
  });
  const card = component.cards[0];
  component.toggleExcluded(card, 'f2');
  await component.pick(card, { kind: 'person', person: NOAH });
  requests.length = 0;

  assert.equal(alerts[0].message, 'Named Noah');
  await alerts[0].options.actions[0].onClick();

  assert.deepEqual(requests.map((r) => [r.url, r.method, r.body]), [
    ['/api/v1/photos/clusters/c1', 'PATCH', { person: null }],
    ['/api/v1/photos/faces/undo', 'POST', { token: 't-reject' }],
  ]);
  assert.equal(alerts.at(-1).message, '1 face put back');
  assert.deepEqual(events, ['photos-faces-changed']);
});

test('the undo of a hidden cluster shows it again', async () => {
  const { component, requests, alerts } = reviewPage({ cards: [clusterCard('c1', 'f1'), clusterCard('c2', 'f2')] });
  await component.hideCard(component.cards[0]);
  requests.length = 0;

  await alerts[0].options.actions[0].onClick();

  assert.deepEqual(requests.map((r) => r.body), [{ hidden: false }]);
  assert.equal(alerts.at(-1).message, 'Put back');
});

test('saying yes to a person confirms the faces kept and takes the others out', async () => {
  const { component, requests, alerts } = reviewPage({
    cards: [{ key: 'k', kind: 'check', guess: NINA, faces: faces('f1', 'f2') }],
    responses: { [BATCH]: batchDone },
  });
  const card = component.cards[0];
  component.toggleExcluded(card, 'f2');

  await component.pickGuess(card);

  assert.deepEqual(requests.map((r) => r.body), [
    { action: 'confirm', faces: ['f1'] },
    { action: 'reject', faces: ['f2'] },
  ]);
  assert.equal(alerts[0].message, '1 face confirmed');
  // One undo puts both batches back.
  assert.equal(alerts[0].options.actions.length, 1);
});

test('leaving every face out makes the guess a no', async () => {
  const { component, requests } = reviewPage({
    cards: [{ key: 'k', kind: 'check', guess: NINA, faces: faces('f1', 'f2') }],
    responses: { [BATCH]: batchDone },
  });
  const card = component.cards[0];
  component.toggleExcluded(card, 'f1');
  component.toggleExcluded(card, 'f2');

  assert.equal(component.guessLabel(card), 'None of them is Nina Petit');
  assert.equal(component.canPick(card, { kind: 'create', name: 'Léa' }), false);
  await component.pickGuess(card);

  assert.deepEqual(requests.map((r) => r.body), [{ action: 'reject', faces: ['f1', 'f2'] }]);
});

test('naming someone else moves the kept faces and leaves the others be', async () => {
  const { component, requests, alerts } = reviewPage({
    cards: [{ key: 'k', kind: 'check', guess: NINA, faces: faces('f1', 'f2') }],
    responses: { [BATCH]: batchDone },
  });
  const card = component.cards[0];
  component.toggleExcluded(card, 'f2');

  await component.pick(card, { kind: 'person', person: NOAH });

  assert.deepEqual(requests.map((r) => r.body), [{ action: 'assign', faces: ['f1'], person: 'o' }]);
  assert.equal(alerts[0].message, '1 face moved to Noah');
});

test('faces in no group are named or hidden, the ones left out stay', async () => {
  const loose = (key) => ({ key, kind: 'loose', guess: null, faces: faces('f1', 'f2', 'f3') });
  const { component, requests } = reviewPage({ cards: [loose('a'), loose('b')], responses: { [BATCH]: batchDone } });
  const [first, second] = component.cards;
  component.toggleExcluded(first, 'f3');
  component.toggleExcluded(second, 'f1');

  await component.pick(first, { kind: 'create', name: 'Léa' });
  await component.hideCard(second);

  assert.deepEqual(requests.map((r) => r.body), [
    { action: 'assign', faces: ['f1', 'f2'], new_person: 'Léa' },
    { action: 'hide', faces: ['f2', 'f3'] },
  ]);
});

test('hiding an unnamed cluster hides the whole group', async () => {
  const { component, requests } = reviewPage({ cards: [clusterCard('c1', 'f1')] });

  await component.hideCard(component.cards[0]);

  assert.deepEqual(requests.map((r) => [r.url, r.body]), [['/api/v1/photos/clusters/c1', { hidden: true }]]);
});

test('the last card settled brings the next ones from the server', async () => {
  const { component } = reviewPage({
    cards: [{ key: 'k', kind: 'loose', guess: null, faces: faces('f1') }],
    left: 40,
    responses: { [BATCH]: batchDone },
  });

  await component.hideCard(component.cards[0]);

  assert.equal(component.cards.length, 0);
  assert.equal(component.left, 39);
  assert.equal(component.reloads, 1);
});

test('a refused answer keeps the card, and reloads once something went through', async () => {
  const { component, alerts } = reviewPage({
    cards: [clusterCard('c1', 'f1', 'f2')],
    responses: { [BATCH]: batchDone, '/api/v1/photos/clusters/c1': new Error('Already in one of the photos') },
  });
  const card = component.cards[0];
  component.toggleExcluded(card, 'f2');

  const settled = await component.pick(card, { kind: 'person', person: NOAH });

  assert.equal(settled, false);
  assert.deepEqual(Array.from(component.cards, (c) => c.key), ['c1']);
  assert.equal(alerts[0].message, 'Already in one of the photos');
  assert.equal(component.reloads, 1);
});

test('a card asks the question its kind asks', () => {
  const { component } = reviewPage({
    cards: [
      { key: 'k', kind: 'check', guess: NINA, total: 3, faces: faces('f1', 'f2') },
      { ...clusterCard('c1', 'f3'), guess: NINA, photo_count: 4, total: 4 },
    ],
  });
  const [check, cluster] = component.cards;

  assert.equal(component.question(check), 'Is this Nina Petit?');
  assert.equal(component.guessLabel(check), "Yes, it's Nina Petit");
  assert.equal(component.guessHint(check), 'Confirms 2 faces');
  component.toggleExcluded(check, 'f2');
  assert.equal(component.guessHint(check), 'Confirms 1 face, takes 1 out');
  assert.equal(component.question(cluster), 'Who is this?');
  assert.equal(component.guessLabel(cluster), "It's Nina Petit");
});

const key = (k, extra = {}) => ({ key: k, code: '', altKey: false, shiftKey: false, preventDefault() { this.prevented = true; }, ...extra });

test('the first card has the keyboard when the page opens', () => {
  const { component } = reviewPage({ cards: [clusterCard('c1', 'f1'), clusterCard('c2', 'f2')] });

  assert.deepEqual({ ...component.focusRequest }, { key: 'c1', scroll: false });
});

test('enter answers with the guess and hands the keyboard to the next card', async () => {
  const { component, requests } = reviewPage({
    cards: [{ ...clusterCard('c1', 'f1'), guess: NINA }, clusterCard('c2', 'f2')],
  });
  const card = component.cards[0];
  component.focusCard(card);

  await component.onKeydown(card, key('Enter'));
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(requests.map((r) => r.body), [{ person: 'n' }]);
  assert.deepEqual({ ...component.focusRequest }, { key: 'c2', scroll: true });
});

test('enter without a guess opens the contacts instead of taking the first one', () => {
  const { component, requests } = reviewPage({ cards: [clusterCard('c1', 'f1')] });
  const card = component.cards[0];
  component.results = [NOAH];
  component.focusCard(card);

  component.onKeydown(card, key('Enter'));

  assert.equal(component.pickerOpen, true);
  assert.deepEqual(requests, []);
  component.onKeydown(card, key('Enter'));
  assert.deepEqual(requests.map((r) => r.body), [{ person: 'o' }]);
});

test('tab and shift+tab go from card to card, past the last one tab leaves the list', () => {
  const { component } = reviewPage({ cards: [clusterCard('c1', 'f1'), clusterCard('c2', 'f2')] });
  const [first, second] = component.cards;

  const forward = key('Tab');
  component.onKeydown(first, forward);
  assert.equal(forward.prevented, true);
  assert.equal(component.focusRequest.key, 'c2');
  component.onKeydown(second, key('Tab', { shiftKey: true }));
  assert.equal(component.focusRequest.key, 'c1');
  const out = key('Tab');
  component.onKeydown(second, out);
  assert.equal(out.prevented, undefined);
});

test('alt+h hides the card', async () => {
  const { component, requests } = reviewPage({ cards: [clusterCard('c1', 'f1'), clusterCard('c2', 'f2')] });

  await component.onKeydown(component.cards[0], key('˙', { altKey: true, code: 'KeyH' }));
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(requests.map((r) => r.body), [{ hidden: true }]);
});

test('escape closes the contacts first, then clears the name', () => {
  const { component } = reviewPage({ cards: [clusterCard('c1', 'f1')] });
  const card = component.cards[0];
  component.typeName(card, 'Lé');
  clearTimeout(component._searchTimer);

  component.onKeydown(card, key('Escape'));
  assert.equal(component.pickerOpen, false);
  assert.equal(component.query, 'Lé');
  component.onKeydown(card, key('Escape'));
  assert.equal(component.query, '');
});

test('a face clicked hands the keyboard back to its card, and a mouse answer moves on', async () => {
  const { component } = reviewPage({ cards: [clusterCard('c1', 'f1', 'f2'), clusterCard('c2', 'f3')] });
  const card = component.cards[0];

  component.keepKeyboard(card);
  assert.deepEqual({ ...component.focusRequest }, { key: 'c1', scroll: false });
  await component.hideCard(card);
  assert.equal(component.focusRequest.key, 'c2');
});

test('the typed name belongs to the card whose field is in use', () => {
  const { component } = reviewPage({ cards: [clusterCard('c1', 'f1'), clusterCard('c2', 'f2')] });
  const [first, second] = component.cards;
  component.results = [NOAH];

  component.typeName(first, 'Léa');

  assert.deepEqual(Array.from(component.options(first), (o) => o.kind), ['person', 'create']);
  assert.deepEqual(Array.from(component.options(second), (o) => o.kind), ['person']);
  component.focusCard(second);
  assert.equal(component.query, '');
  assert.equal(component.isActive(second), true);
  clearTimeout(component._searchTimer);
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

// ── Face boards ─────────────────────────────────────

function board({ responses = {}, alerts = [] } = {}) {
  const requests = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, method: options.method, body });
      const answer = responses[url];
      const data = typeof answer === 'function' ? answer(body) : answer;
      return { ok: true, status: 200, json: async () => data };
    },
    AppAlert: {
      success: (message, options) => alerts.push({ type: 'success', message, options }),
      warning: (message) => alerts.push({ type: 'warning', message }),
      error: (message) => alerts.push({ type: 'error', message }),
    },
  });
  const component = { ...ctx.faceSelectionMixin(), settled: [] };
  component.facesSettled = (done, action) => component.settled.push([Array.from(done), action]);
  return { ctx, component, requests, alerts };
}

const action = (id, bulk = true) => ({ id, bulk });

test('the selection offers what every picked face offers, in the registry order', () => {
  const { ctx } = board();

  const offered = ctx.faceSelectionActions([
    [action('confirm'), action('assign'), action('reject'), action('hide')],
    [action('assign'), action('reject'), action('hide')],
    [action('assign'), action('hide'), action('reject')],
  ]);

  assert.deepEqual(Array.from(offered, (a) => a.id), ['assign', 'reject', 'hide']);
  assert.deepEqual(Array.from(ctx.faceSelectionActions([])), []);
});

test('the toast says what was done and why the rest was left', () => {
  const { ctx } = board();

  assert.equal(
    ctx.faceBatchMessage('assign', { done: ['a', 'b'], skipped: [] }, 'Nina'),
    '2 faces moved to Nina',
  );
  assert.equal(
    ctx.faceBatchMessage('hide', { done: ['a'], skipped: [{ face: 'b', reason: 'already_in_photo' }] }),
    '1 face hidden. 1 face left as it was: another face of their photo is already in that group',
  );
  assert.equal(
    ctx.faceBatchMessage('confirm', { done: [], skipped: [{ face: 'a', reason: 'unavailable' }, { face: 'b', reason: 'missing' }] }),
    '2 faces left as they were',
  );
});

test('shift picks every face between the last one picked and this one', () => {
  const { component } = board();
  component._faceUuids = () => ['a', 'b', 'c', 'd', 'e'];

  component.toggleFace('b');
  component.toggleFace('d', { shiftKey: true });

  assert.deepEqual(Array.from(component.selectedFaces), ['b', 'c', 'd']);
  component.toggleFace('c');
  assert.deepEqual(Array.from(component.selectedFaces), ['b', 'd']);
});

test('the actions are asked once per face, and the selection keeps the shared ones', async () => {
  const { component, requests } = board({
    responses: {
      '/api/v1/photos/faces/actions': (body) => Object.fromEntries(body.uuids.map((uuid) => [
        uuid, uuid === 'a' ? [action('confirm'), action('assign')] : [action('assign')],
      ])),
    },
  });

  component.selectedFaces = ['a', 'b'];
  await component._loadFaceActions();
  component.selectedFaces = ['a'];
  await component._loadFaceActions();

  assert.equal(requests.length, 1);
  assert.equal(component.faceAllows('confirm'), true);
  assert.equal(component.faceAllows('reject'), false);
});

test('a batch settles the done faces, keeps the skipped ones picked and offers the undo', async () => {
  const alerts = [];
  const { component, requests } = board({
    alerts,
    responses: {
      '/api/v1/photos/faces/batch': { done: ['a'], skipped: [{ face: 'b', reason: 'already_in_photo' }], undo: 'tok' },
    },
  });
  component.selectedFaces = ['a', 'b'];

  await component.applyFaceBatch({ action: 'hide' });

  assert.deepEqual(requests[0].body, { action: 'hide', faces: ['a', 'b'] });
  assert.deepEqual(component.settled, [[['a'], 'hide']]);
  assert.deepEqual(Array.from(component.selectedFaces), ['b']);
  assert.equal(alerts[0].type, 'success');
  assert.equal(alerts[0].options.actions[0].label, 'Undo');
});

test('a batch that changed nothing warns and offers no undo', async () => {
  const alerts = [];
  const { component } = board({
    alerts,
    responses: {
      '/api/v1/photos/faces/batch': { done: [], skipped: [{ face: 'a', reason: 'unavailable' }], undo: null },
    },
  });
  component.selectedFaces = ['a'];

  await component.applyFaceBatch({ action: 'confirm' });

  assert.deepEqual(component.settled, []);
  assert.equal(alerts[0].type, 'warning');
});

test('assigning to a new name sends it with the picked faces', async () => {
  const { component, requests } = board({
    responses: { '/api/v1/photos/faces/batch': { done: ['a'], skipped: [], undo: 't' } },
  });
  component.closeAssignDialog = () => {};
  component.selectedFaces = ['a'];
  component.assignDialog.query = '  Léa ';

  await component.assignToNewPerson();

  assert.deepEqual(requests[0].body, { action: 'assign', new_person: 'Léa', faces: ['a'] });
});

function plainBoard(data, options) {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: (id) => (id === 'board-data' ? { textContent: JSON.stringify(data) } : null) },
  });
  const component = ctx.faceBoard('board-data', options);
  component.$watch = () => {};
  component.init();
  return component;
}

test('a board lets corrected faces go', () => {
  const component = plainBoard({ total: 3, faces: [{ uuid: 'a' }, { uuid: 'b' }, { uuid: 'c' }] });

  component.facesSettled(['b'], 'unhide');

  assert.deepEqual(Array.from(component.faces, (f) => f.uuid), ['a', 'c']);
  assert.equal(component.total, 2);
});

test('on a person\'s board, a confirmed face stays and shows as confirmed', () => {
  const component = plainBoard({ total: 2, faces: [{ uuid: 'a', assignment: 'auto' }, { uuid: 'b', assignment: 'auto' }] }, { stays: ['confirm'] });

  component.facesSettled(['a'], 'confirm');

  assert.deepEqual(Array.from(component.faces, (f) => f.assignment), ['confirmed', 'auto']);
  assert.equal(component.total, 2);
});

test('a moment of a video reads the way its player shows it', () => {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js');

  assert.equal(ctx.faceTimeLabel(0), '0:00');
  assert.equal(ctx.faceTimeLabel(42.7), '0:42');
  assert.equal(ctx.faceTimeLabel(725), '12:05');
  assert.equal(ctx.faceTimeLabel(3725), '1:02:05');
});

function viewerEvents() {
  const events = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => ({ close() {} }) },
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init.detail;
      }
    },
    dispatchEvent: (event) => events.push({ event: event.type, ...event.detail }),
  });
  return { faces: ctx.photosFacesMixin(), board: ctx.faceSelectionMixin(), events };
}

test('a face of a video opens the video at the moment it was seen', () => {
  const { board, events } = viewerEvents();

  board.openFacePhoto({ file: 'v', file_name: 'beach.mp4', file_type: 'mp4', timestamp: 42.5 });
  board.openFacePhoto({ file: 'p', file_name: 'beach.jpg', file_type: 'jpeg', timestamp: null });

  assert.deepEqual(events, [
    { event: 'open-file-viewer', uuid: 'v', name: 'beach.mp4', type: 'mp4', at: 42.5 },
    { event: 'open-file-viewer', uuid: 'p', name: 'beach.jpg', type: 'jpeg', at: null },
  ]);
});

test('"People in this video" plays the video from where a face was seen', () => {
  const { faces, events } = viewerEvents();
  faces.facesDialog.photo = { uuid: 'v', name: 'beach.mp4', type: 'mp4', mediaType: 'video' };
  const face = { uuid: 'f', timestamp: 65 };

  assert.equal(faces.facesDialogIsVideo(), true);
  assert.equal(faces.faceTime(face), '1:05');
  faces.watchFace(face);

  assert.deepEqual(events, [
    { event: 'open-file-viewer', uuid: 'v', name: 'beach.mp4', type: 'mp4', at: 65 },
  ]);
});

test('a photo\'s faces have no moment', () => {
  const faces = mixin();
  faces.facesDialog.photo = { uuid: 'p', name: 'beach.jpg', type: 'jpeg', mediaType: 'photo' };

  assert.equal(faces.facesDialogIsVideo(), false);
  assert.equal(faces.faceTime({ uuid: 'f', timestamp: null }), '');
});

test('a merge candidate for a named person stands for every cluster of theirs', () => {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
  });
  const clusters = [
    { uuid: 'nina-big', person: 'p-nina', photo_count: 9 },
    { uuid: 'u1', person: null, photo_count: 5 },
    { uuid: 'nina-small', person: 'p-nina', photo_count: 6 },
  ];

  const candidates = ctx.mergeCandidates(clusters, { uuid: 't', person: null });

  assert.deepEqual(Array.from(candidates[0].members), ['nina-big', 'nina-small']);
  assert.equal(candidates[0].photo_count, 15);
  assert.deepEqual(Array.from(candidates[1].members), ['u1']);
  assert.equal(candidates[1].photo_count, 5);
  // A picked card finds its candidate through any of its clusters.
  assert.equal(ctx.candidateForCard(candidates, { clusters: ['nina-small'] }).uuid, 'nina-big');
  assert.equal(ctx.candidateForCard(candidates, { clusters: ['gone'] }), null);
});

test('picked cards merge into the first named one, else the first picked', () => {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
  });
  const unnamed = { clusters: ['u1'], person: null };
  const ann = { clusters: ['a1'], person: 'p-ann' };
  const bea = { clusters: ['b1'], person: 'p-bea' };

  assert.equal(ctx.mergeKeeper([unnamed, ann, bea]), ann);
  assert.equal(ctx.mergeKeeper([unnamed, { clusters: ['u2'], person: null }]), unnamed);
  assert.equal(ctx.mergeKeeper([]), null);
});

test('picking People cards toggles them, and stopping forgets them', () => {
  const faces = mixin();

  faces.startPickingPeople();
  faces.togglePeopleCard('a1,a2');
  faces.togglePeopleCard('u1');
  faces.togglePeopleCard('a1,a2');

  assert.equal(faces.peoplePicking, true);
  assert.deepEqual(Array.from(faces.peoplePicked), ['u1']);
  faces.stopPickingPeople();
  assert.equal(faces.peoplePicking, false);
  assert.deepEqual(Array.from(faces.peoplePicked), []);
});

test('naming a cluster is one request, whoever its photos already show', async () => {
  const bodies = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      bodies.push([url, options.method, JSON.parse(options.body)]);
      return { ok: true, status: 200, json: async () => ({ person: 'p-ann' }) };
    },
  });

  await ctx.nameCluster('c1', { person: 'p-ann' });

  assert.deepEqual(bodies, [['/api/v1/photos/clusters/c1', 'PATCH', { person: 'p-ann' }]]);
});

test('a batch toast offers Undo when something changed, nothing otherwise', () => {
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/faces.js', {
    document: { getElementById: () => null },
  });

  assert.deepEqual(Array.from(ctx.faceBatchActions(['tok']), (a) => a.label), ['Undo']);
  assert.deepEqual(Array.from(ctx.faceBatchActions([])), []);
});
