'use strict';

// The poll modals: each action owns its busy flag, a poll that failed to load
// says so instead of leaving the detail modal blank, and an answer for a poll
// that is no longer the one on screen is dropped.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

const POLL_A = '0190a2b4-0000-7000-8000-00000000000a';
const POLL_B = '0190a2b4-0000-7000-8000-00000000000b';

function poll(uuid, extra = {}) {
  return { uuid, title: 'Poll ' + uuid.slice(-1), status: 'open', slots: [], votes: [], ...extra };
}

// Every fetch waits until the test answers it, in whatever order it likes.
function makeApp() {
  const requests = [];
  const ctx = loadScripts([
    'workspace/common/static/ui/js/uuid.js',
    'workspace/common/static/ui/js/latest_only.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar_polls.js',
  ], {
    document: { body: { dataset: { userId: '1' } } },
    location: { pathname: '/calendar', search: '' },
    navHistory: { replace: () => {} },
    URLSearchParams,
    getCSRFToken: () => 'token',
    fetch: (url, opts = {}) => new Promise((resolve, reject) => {
      requests.push({
        url,
        method: opts.method || 'GET',
        answer: (body, status = 200) => resolve({
          ok: status >= 200 && status < 300,
          status,
          json: async () => body,
        }),
        fail: () => reject(new TypeError('Failed to fetch')),
      });
    }),
  });
  ctx.wallClockToIso = (value) => value;
  const app = {
    ...ctx.calendarPollsMixin(),
    showPollListModal: false,
    showPollDetailModal: false,
    showPollEditModal: false,
    showPollCreateModal: false,
    pollForm: { title: '', description: '', slots: [] },
    pollFormError: null,
    currentPoll: null,
    currentPollLoading: false,
    currentPollError: null,
    pollMyVotes: {},
    pollFinalizeSlotId: null,
    pollVoting: false,
    pollFinalizing: false,
    pollCreating: false,
    pollEditSaving: false,
    calendar: null,
    refetchAgenda: () => {},
    toLocalDatetime: (iso) => iso,
  };
  return { app, requests };
}

const twoSlotForm = (title) => ({
  title,
  description: '',
  slots: [{ start: '2026-11-10T09:00' }, { start: '2026-11-11T09:00' }],
});

test('a poll that fails to load shows an error in the open modal', async () => {
  const { app, requests } = makeApp();
  const opening = app.openPollDetail(POLL_A);
  requests[0].answer({ detail: 'Not found.' }, 404);
  await opening;
  assert.equal(app.showPollDetailModal, true);
  assert.equal(app.currentPollLoading, false);
  assert.equal(app.currentPoll, null);
  assert.equal(app.currentPollError, 'Failed to load poll.');
});

test('a network failure while loading a poll shows the same error', async () => {
  const { app, requests } = makeApp();
  const opening = app.openPollDetail(POLL_A);
  requests[0].fail();
  await opening;
  assert.equal(app.currentPollLoading, false);
  assert.equal(app.currentPollError, 'Failed to load poll.');
});

test('opening a poll clears the error left by the previous one', async () => {
  const { app, requests } = makeApp();
  const first = app.openPollDetail(POLL_A);
  requests[0].answer({}, 500);
  await first;
  const second = app.openPollDetail(POLL_B);
  assert.equal(app.currentPollError, null);
  requests[1].answer(poll(POLL_B));
  await second;
  assert.equal(app.currentPollError, null);
  assert.equal(app.currentPoll.uuid, POLL_B);
});

test('a late answer for the previously opened poll is dropped', async () => {
  const { app, requests } = makeApp();
  const first = app.openPollDetail(POLL_A);
  const second = app.openPollDetail(POLL_B);
  requests[1].answer(poll(POLL_B));
  await second;
  requests[0].answer(poll(POLL_A));
  await first;
  assert.equal(app.currentPoll.uuid, POLL_B);
  assert.equal(app.currentPollLoading, false);
});

test('a late failure for the previously opened poll leaves no error', async () => {
  const { app, requests } = makeApp();
  const first = app.openPollDetail(POLL_A);
  const second = app.openPollDetail(POLL_B);
  requests[1].answer(poll(POLL_B));
  await second;
  requests[0].answer({}, 500);
  await first;
  assert.equal(app.currentPollError, null);
  assert.equal(app.currentPoll.uuid, POLL_B);
});

test('closing the detail drops the poll still loading', async () => {
  const { app, requests } = makeApp();
  const opening = app.openPollDetail(POLL_A);
  app.closePollDetail();
  requests[0].answer(poll(POLL_A));
  await opening;
  assert.equal(app.currentPoll, null);
});

test('a vote finishing does not clear the finalize in flight', async () => {
  const { app, requests } = makeApp();
  app.currentPoll = poll(POLL_A);
  app.pollMyVotes = { s1: 'yes' };
  app.pollFinalizeSlotId = 's1';
  const voting = app.submitPollVotes();
  const finalizing = app.finalizePoll();
  assert.equal(app.pollVoting, true);
  assert.equal(app.pollFinalizing, true);
  requests[0].answer(poll(POLL_A));
  await voting;
  assert.equal(app.pollVoting, false);
  assert.equal(app.pollFinalizing, true);
  requests[1].answer(poll(POLL_A, { status: 'closed' }));
  await finalizing;
  assert.equal(app.pollFinalizing, false);
});

test('a finalize finishing does not clear the vote in flight', async () => {
  const { app, requests } = makeApp();
  app.currentPoll = poll(POLL_A);
  app.pollMyVotes = { s1: 'yes' };
  app.pollFinalizeSlotId = 's1';
  const voting = app.submitPollVotes();
  const finalizing = app.finalizePoll();
  requests[1].answer(poll(POLL_A, { status: 'closed' }));
  await finalizing;
  assert.equal(app.pollVoting, true);
  requests[0].answer(poll(POLL_A));
  await voting;
  assert.equal(app.pollVoting, false);
});

test('a vote answering after another poll was opened leaves that poll alone', async () => {
  const { app, requests } = makeApp();
  app.currentPoll = poll(POLL_A);
  app.pollMyVotes = { s1: 'yes' };
  const voting = app.submitPollVotes();
  const opening = app.openPollDetail(POLL_B);
  requests[1].answer(poll(POLL_B));
  await opening;
  requests[0].answer(poll(POLL_A));
  await voting;
  assert.equal(app.currentPoll.uuid, POLL_B);
});

test('saving an edit does not clear a create still in flight', async () => {
  const { app, requests } = makeApp();
  app.pollForm = twoSlotForm('New');
  const creating = app.submitPoll();
  app.currentPoll = poll(POLL_A);
  app.pollForm = twoSlotForm('Edited');
  const editing = app.savePollEdit();
  assert.equal(app.pollCreating, true);
  assert.equal(app.pollEditSaving, true);
  requests[1].answer(poll(POLL_A, { title: 'Edited' }));
  await editing;
  assert.equal(app.pollEditSaving, false);
  assert.equal(app.pollCreating, true);
  requests[0].answer({}, 400);
  await creating;
  assert.equal(app.pollCreating, false);
});
