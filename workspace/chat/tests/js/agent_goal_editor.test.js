'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const GOAL = {
  uuid: 'goal-1',
  title: 'Find a flat',
  goal: 'Track listings in Lyon.',
  success_criteria: 'Lease signed.',
  constraints: 'Budget under 900 euros.',
  reporting: 'Only for visits worth booking.',
  notes: 'Three listings seen so far.',
  deadline: null,
  next_check_at: '2026-09-01T08:30:00Z',
  check_count: 4,
  last_checked_at: '2026-08-30T08:30:00Z',
};

function buildApp() {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/bot.js', {
    document: { getElementById: () => null },
  });
  return { ...ctx.chatBotMixin() };
}

test('opening the editor loads every editable field of the goal', () => {
  const app = buildApp();

  app.editGoal(GOAL);

  assert.equal(app.goalEditor.open, true);
  assert.equal(app.goalEditor.goal_uuid, 'goal-1');
  assert.equal(app.goalEditor.success_criteria, 'Lease signed.');
  assert.equal(app.goalEditor.constraints, 'Budget under 900 euros.');
  assert.equal(app.goalEditor.reporting, 'Only for visits worth booking.');
  assert.equal(app.goalEditor.notes, 'Three listings seen so far.');
  assert.equal(app.goalEditor.check_count, 4);
});

test('datetime fields round-trip between the API and the datetime-local input', () => {
  const app = buildApp();

  app.editGoal(GOAL);

  // The input holds wall-clock time, so it has no zone suffix...
  assert.match(app.goalEditor.next_check_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
  // ...and converting it back yields the instant we started from.
  assert.equal(
    new Date(app.goalEditorPayload().next_check_at).getTime(),
    new Date(GOAL.next_check_at).getTime(),
  );
});

test('an empty deadline input clears the deadline', () => {
  const app = buildApp();

  app.editGoal({ ...GOAL, deadline: '2026-10-01T10:00:00Z' });
  assert.notEqual(app.goalEditor.deadline, '');

  app.goalEditor.deadline = '';
  assert.equal(app.goalEditorPayload().deadline, null);
});

test('the payload trims the mission brief and keeps blanked fields', () => {
  const app = buildApp();

  app.editGoal(GOAL);
  app.goalEditor.reporting = '  Only on Sundays.  ';
  app.goalEditor.constraints = '';

  const payload = app.goalEditorPayload();
  assert.equal(payload.reporting, 'Only on Sundays.');
  // Clearing a brief field must reach the API, not be dropped as falsy.
  assert.equal(payload.constraints, '');
});

test('saving refuses an empty title or objective without calling the API', async () => {
  const app = buildApp();
  let called = false;
  app._patchGoal = async () => { called = true; };

  app.editGoal({ ...GOAL, title: '' });
  await app.saveGoalEdit();

  assert.equal(called, false);
  assert.equal(app.goalEditor.open, true);
  assert.match(app.goalEditor.error, /required/);
});

test('saving sends the edited fields and closes the editor', async () => {
  const app = buildApp();
  let sent = null;
  app._patchGoal = async (goal, payload) => { sent = { goal, payload }; return { ...GOAL }; };

  app.editGoal(GOAL);
  app.goalEditor.success_criteria = 'Lease signed and keys received.';
  await app.saveGoalEdit();

  assert.equal(sent.goal.uuid, 'goal-1');
  assert.equal(sent.payload.success_criteria, 'Lease signed and keys received.');
  assert.equal(sent.payload.notes, 'Three listings seen so far.');
  assert.equal(app.goalEditor.open, false);
  assert.equal(app.goalEditor.saving, false);
});

test('a cleared next check-in keeps the current schedule instead of sending null', async () => {
  const app = buildApp();
  let sent = null;
  app._patchGoal = async (goal, payload) => { sent = payload; return { ...GOAL }; };

  app.editGoal(GOAL);
  app.goalEditor.next_check_at = '';
  await app.saveGoalEdit();

  assert.ok(!('next_check_at' in sent));
});

test('a failed save keeps the editor open and reports the error', async () => {
  const app = buildApp();
  app._patchGoal = async () => null;

  app.editGoal(GOAL);
  await app.saveGoalEdit();

  assert.equal(app.goalEditor.open, true);
  assert.equal(app.goalEditor.saving, false);
  assert.match(app.goalEditor.error, /Could not save/);
});
