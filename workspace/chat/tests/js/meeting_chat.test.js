'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/meeting_chat.js');

function msg(overrides) {
  return {
    uuid: 'u' + Math.random(),
    body: 'x',
    body_html: '<p>x</p>',
    created_at: '2026-09-07T10:00:00Z',
    occurrence_start: '2026-09-07T09:55:00Z',
    author: { id: 1, username: 'a', display_name: 'A', is_guest: false, participant_key: 'u:1' },
    ...overrides,
  };
}

test('consecutive messages by one author within five minutes share a group', () => {
  const rows = Array.from(ctx.chatMeetingGroupMessages([
    msg({ created_at: '2026-09-07T10:00:00Z' }),
    msg({ created_at: '2026-09-07T10:03:00Z' }),
    msg({ created_at: '2026-09-07T10:09:00Z' }),
  ], 'u:1'));
  assert.equal(rows[0].type, 'divider');
  assert.equal(rows.filter((r) => r.type === 'group').length, 2);
  assert.equal(rows[1].items.length, 2);
  assert.equal(rows[1].own, true);
});

test('a new occurrence opens a new divider', () => {
  const rows = Array.from(ctx.chatMeetingGroupMessages([
    msg({ occurrence_start: '2026-08-31T09:55:00Z', created_at: '2026-08-31T10:00:00Z' }),
    msg({ occurrence_start: '2026-09-07T09:55:00Z' }),
  ], 'u:2'));
  assert.deepEqual(rows.map((r) => r.type), ['divider', 'group', 'divider', 'group']);
  assert.equal(rows[1].own, false);
});

test('a guest author is flagged on its group', () => {
  const rows = Array.from(ctx.chatMeetingGroupMessages([
    msg({ author: { id: null, username: 'Ada', display_name: 'Ada', is_guest: true, participant_key: 'g:1' } }),
  ], 'u:1'));
  assert.equal(rows[1].author.is_guest, true);
});

// Alpine scopes every x-if clone as a root of its own, and $refs/$root both
// resolve against the element the calling expression was bound to - which, for
// a frame arriving on the guest's stream, is the name-phase form whose branch
// is long gone. The pane is addressed by id so it is found whatever mounted it.
test('the pane is addressed by id, not through the calling scope', () => {
  const list = { tag: 'list' };
  const seen = [];
  const ctxWithDom = loadScript('workspace/chat/ui/static/chat/ui/js/meeting_chat.js', {
    document: {
      getElementById: (id) => { seen.push(id); return id === 'meeting-chat-list' ? list : null; },
    },
  });
  const component = ctxWithDom.chatMeetingChatMixin();

  assert.equal(component._meetingChatEl('meeting-chat-list'), list);
  assert.equal(component.getMessageInput(), null);
  assert.deepEqual(Array.from(seen), ['meeting-chat-list', 'meeting-chat-input']);
});

// -- The rendered list ------------------------------------------------------

// The shell element (message_shell.js) rebuilds this subtree when it upgrades,
// so the delete control cannot rely on Alpine walking an x-on attribute after
// the fact: in the browser that produced a button that swallowed every click.
function stubElement(tag) {
  return {
    tag,
    children: [],
    attrs: {},
    listeners: {},
    className: '',
    textContent: '',
    innerHTML: '',
    id: '',
    title: '',
    setAttribute(name, value) { this.attrs[name] = String(value); },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
    replaceChildren(fragment) { this.children = fragment.children.slice(); },
    querySelectorAll() { return []; },
  };
}

function descendants(node) {
  return (node.children || []).flatMap((child) => [child, ...descendants(child)]);
}

function renderedHostPane(messages) {
  const list = stubElement('div');
  const ctxWithDom = loadScript('workspace/chat/ui/static/chat/ui/js/meeting_chat.js', {
    document: {
      getElementById: (id) => (id === 'meeting-chat-list' ? list : null),
      createElement: stubElement,
      createDocumentFragment: () => stubElement('#fragment'),
    },
  });
  const component = ctxWithDom.chatMeetingChatMixin();
  component.meetingMessages = messages;
  component.currentParticipantKey = 'u:1';
  component._canDeleteMeetingMessages = () => true;
  component.$nextTick = (fn) => { if (fn) fn(); };
  component.deleted = [];
  component.deleteMeetingMessage = function (uuid) { this.deleted.push(uuid); };
  component.renderMeetingMessages();
  return { component, list };
}

test('the delete control calls deleteMeetingMessage with its own uuid', () => {
  const { component, list } = renderedHostPane([msg({ uuid: 'm-9' })]);

  const buttons = descendants(list).filter((node) => node.tag === 'button');
  assert.equal(buttons.length, 1, 'one delete control per message');
  assert.equal(buttons[0].title, 'Delete');
  const clicks = buttons[0].listeners.click || [];
  assert.equal(clicks.length, 1, 'bound with a real listener, not an x-on attribute');

  clicks[0]();
  assert.deepEqual(Array.from(component.deleted), ['m-9']);
});

test('a reader who cannot delete gets no control at all', () => {
  const list = stubElement('div');
  const ctxWithDom = loadScript('workspace/chat/ui/static/chat/ui/js/meeting_chat.js', {
    document: {
      getElementById: (id) => (id === 'meeting-chat-list' ? list : null),
      createElement: stubElement,
      createDocumentFragment: () => stubElement('#fragment'),
    },
  });
  const component = ctxWithDom.chatMeetingChatMixin();
  component.meetingMessages = [msg({ uuid: 'm-9' })];
  component.$nextTick = (fn) => { if (fn) fn(); };
  component.renderMeetingMessages();

  assert.equal(descendants(list).filter((node) => node.tag === 'button').length, 0);
});
