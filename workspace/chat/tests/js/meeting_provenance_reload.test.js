'use strict';

// The list payload carries a meeting's title and join link but not its
// occurrence or lock, so selecting a meeting conversation on /chat reads the
// single-conversation endpoint once and merges that one key back. The risk
// this pins down is the switch that outruns its own request.

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function app(fetchImpl) {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', {
    document: { getElementById: () => null },
    fetch: fetchImpl,
  });
  return ctx.chatConversationsMixin();
}

function listRow(uuid, title) {
  // What ConversationListSerializer emits: no next_start key at all.
  return { uuid, meeting: { event_title: title, join_url: `https://x/meet/${uuid}` } };
}

function detailResponse(uuid, title, nextStart) {
  return {
    ok: true,
    json: async () => ({
      uuid,
      meeting: {
        event_title: title,
        join_url: `https://x/meet/${uuid}`,
        next_start: nextStart,
        locked: false,
      },
    }),
  };
}

test('selecting a meeting conversation fills in the occurrence it was not sent', async () => {
  const urls = [];
  const a = app(async (url) => {
    urls.push(url);
    return detailResponse('conv-1', 'Standup', '2026-06-25T09:30:00Z');
  });
  a.activeConversation = listRow('conv-1', 'Standup');

  await a._loadMeetingProvenance(a.activeConversation);

  assert.deepStrictEqual(Array.from(urls), ['/api/v1/chat/conversations/conv-1']);
  assert.equal(a.activeConversation.meeting.next_start, '2026-06-25T09:30:00Z');
  assert.equal(a.activeConversation.meeting.locked, false);
});

test('a conversation with no meeting asks the server nothing', async () => {
  const urls = [];
  const a = app(async (url) => { urls.push(url); return detailResponse('c', 'x', null); });
  a.activeConversation = { uuid: 'conv-1', meeting: null };

  await a._loadMeetingProvenance(a.activeConversation);

  assert.equal(urls.length, 0);
});

test('a payload that already carries the occurrence is not re-read', async () => {
  const urls = [];
  const a = app(async (url) => { urls.push(url); return detailResponse('c', 'x', null); });
  a.activeConversation = { uuid: 'conv-1', meeting: { event_title: 'S', next_start: null } };

  await a._loadMeetingProvenance(a.activeConversation);

  assert.equal(urls.length, 0);
});

test('a switch that outruns its request drops the answer it no longer wants', async () => {
  // The first request resolves last, which is exactly the ordering a slow
  // conversation followed by a fast one produces.
  const gates = {};
  const a = app((url) => new Promise((resolve) => { gates[url] = resolve; }));

  const first = listRow('conv-1', 'Standup');
  const second = listRow('conv-2', 'Retro');

  a.activeConversation = first;
  const slow = a._loadMeetingProvenance(first);
  a.activeConversation = second;
  const fast = a._loadMeetingProvenance(second);

  gates['/api/v1/chat/conversations/conv-2'](
    detailResponse('conv-2', 'Retro', '2026-07-01T10:00:00Z'),
  );
  await fast;
  gates['/api/v1/chat/conversations/conv-1'](
    detailResponse('conv-1', 'Standup', '2026-06-25T09:30:00Z'),
  );
  await slow;

  assert.equal(a.activeConversation.uuid, 'conv-2');
  assert.equal(a.activeConversation.meeting.next_start, '2026-07-01T10:00:00Z');
  // The loser wrote nothing anywhere, not even onto its own stale row.
  assert.equal('next_start' in first.meeting, false);
});

test('a failing request leaves the row exactly as the list sent it', async () => {
  const a = app(async () => ({ ok: false, status: 500, json: async () => ({}) }));
  const row = listRow('conv-1', 'Standup');
  a.activeConversation = row;

  await a._loadMeetingProvenance(row);

  assert.deepStrictEqual({ ...row.meeting }, { ...listRow('conv-1', 'Standup').meeting });
});
