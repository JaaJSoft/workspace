'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const { pollUtils } = loadScript('workspace/calendar/ui/static/calendar/ui/js/polls.js');

function vote(participant, slotId, isMine = false) {
  return { participant, slot_id: slotId, choice: 'yes', is_mine: isMine };
}

test('participantCount counts each voter once across slots', () => {
  const poll = { votes: [vote('p1', 's1'), vote('p1', 's2'), vote('p2', 's1')] };
  assert.equal(pollUtils.participantCount(poll), 2);
});

test("participantCount includes the visitor's own saved votes", () => {
  const poll = {
    votes: [vote('p1', 's1'), vote('p2', 's1'), vote('p3', 's1', true), vote('p3', 's2', true)],
  };
  assert.equal(pollUtils.participantCount(poll), 3);
});

test('participantCount is zero before the poll loads or without votes', () => {
  assert.equal(pollUtils.participantCount(null), 0);
  assert.equal(pollUtils.participantCount({ votes: [] }), 0);
});
