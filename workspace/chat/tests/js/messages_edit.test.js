'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * A rendered message bubble, modelling only what the edit path touches:
 * the `.msg-body` child it repaints and the `data-body` the composer reads
 * back when the message is edited again.
 */
function bubble(uuid, body) {
  const el = {
    dataset: { messageUuid: uuid, body },
    _children: { '.msg-body': { innerHTML: `<p>${body}</p>` } },
    querySelector(sel) {
      return el._children[sel] || null;
    },
    appendChild(child) {
      // Counted, not just stored: a stub that silently overwrites the marker
      // cannot tell one insertion from two, and the "added once" test below
      // needs exactly that distinction.
      el.appendCalls = (el.appendCalls || 0) + 1;
      el._children['.edited-indicator'] = child;
    },
  };
  return el;
}

function buildApp(bubbles, { editedBody }) {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    getCSRFToken: () => 'csrf-token',
    document: {
      querySelectorAll: (sel) =>
        bubbles.filter((b) => sel.includes(b.dataset.messageUuid)),
      createElement: () => ({ className: '', textContent: '' }),
    },
    fetch: async () => ({
      ok: true,
      json: async () => ({
        uuid: 'm1',
        body: editedBody,
        body_html: `<p>${editedBody}</p>`,
      }),
    }),
  });

  const app = ctx.chatMessagesMixin();
  Object.assign(app, {
    editingMessageUuid: 'm1',
    messageBody: editedBody,
    activeConversation: { uuid: 'c1' },
    getMessageInput: () => null,
    $nextTick(fn) {
      if (fn) fn();
    },
  });
  return app;
}

test('an edit repaints every rendered copy of the message', async () => {
  const copies = [bubble('m1', 'before'), bubble('m1', 'before')];
  const app = buildApp(copies, { editedBody: 'after' });

  await app.saveEdit();

  assert.deepStrictEqual(
    copies.map((c) => c.querySelector('.msg-body').innerHTML),
    ['<p>after</p>', '<p>after</p>'],
  );
  // data-body too, on every copy: it is what the next startEdit reads back,
  // and a copy left holding the pre-edit text would revert the message.
  assert.deepStrictEqual(
    copies.map((c) => c.dataset.body),
    ['after', 'after'],
  );
});

test('a second edit starts from the first edit, not the original text', async () => {
  // Regression: saveEdit repainted .msg-body but left data-body holding the
  // pre-edit text, so re-opening the composer prefilled the stale body and
  // silently reverted the first edit on save.
  const copies = [bubble('m1', 'before')];
  const app = buildApp(copies, { editedBody: 'after' });

  await app.saveEdit();
  app.startEdit('m1');

  assert.equal(app.messageBody, 'after');
});

test('the edited marker is added once, not on every edit', async () => {
  const copies = [bubble('m1', 'before')];
  const app = buildApp(copies, { editedBody: 'after' });

  await app.saveEdit();
  app.editingMessageUuid = 'm1';
  await app.saveEdit();

  assert.ok(copies[0].querySelector('.edited-indicator'), 'the marker is present');
  // The count, not just presence: appendChild overwrites the stored marker,
  // so without it a second insertion would be invisible to this test.
  assert.equal(copies[0].appendCalls, 1, 'the marker is inserted exactly once');
});
