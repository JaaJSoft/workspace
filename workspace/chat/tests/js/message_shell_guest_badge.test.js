'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript, CUSTOM_ELEMENT_STUBS } = require('../../../common/tests/js/loader');

/**
 * Only _header() and _avatarColumn() are exercisable without a real DOM
 * (see the note atop messages_optimistic.test.js: render() needs
 * classList/children/replaceChildren, out of reach for the node:vm loader).
 * Both touch nothing but getAttribute/hasAttribute and the document stub's
 * createElement, so capturing the class through customElements.define and
 * calling the method on a bare instance is enough to pin the guest badge
 * and the avatar the guest page asks for.
 */
function captureShellClass(avatarCalls) {
  let Shell;
  loadScript('workspace/chat/ui/static/chat/ui/js/message_shell.js', {
    ...CUSTOM_ELEMENT_STUBS,
    userAvatarTag: (userId, username, options) => {
      if (avatarCalls) avatarCalls.push({ userId, username, options });
      return `<user-avatar user-id="${userId}" username="${username}"></user-avatar>`;
    },
    customElements: {
      get: () => undefined,
      define: (name, cls) => {
        Shell = cls;
      },
    },
    document: {
      createElement: (tag) => ({
        tagName: tag.toUpperCase(),
        className: '',
        textContent: '',
        attrs: {},
        children: [],
        setAttribute(attrName, value) {
          this.attrs[attrName] = String(value);
        },
        appendChild(child) {
          this.children.push(child);
        },
      }),
    },
  });
  return Shell;
}

function instanceWith(attrs, avatarCalls) {
  const Shell = captureShellClass(avatarCalls);
  const instance = Object.create(Shell.prototype);
  instance.getAttribute = (name) => (name in attrs ? attrs[name] : null);
  instance.hasAttribute = (name) => name in attrs;
  return instance;
}

function renderHeader(attrs) {
  return instanceWith(attrs)._header();
}

function avatarOptions(attrs) {
  const calls = [];
  instanceWith(attrs, calls)._avatarColumn();
  return calls;
}

function headerText(row) {
  return row.children.map((child) => child.textContent).join(' ');
}

test('a guest group renders a Guest badge in the header, a member group does not', () => {
  const guestHeader = renderHeader({ 'author-username': 'alice', guest: '' });
  assert.match(headerText(guestHeader), /Guest/);

  const memberHeader = renderHeader({ 'author-username': 'alice', 'author-id': '3' });
  assert.doesNotMatch(headerText(memberHeader), /Guest/);
});

test('a guest viewer gets a plain avatar: no presence dot, no profile card', () => {
  // A guest holds a meeting token, not a session, so both extras would ask
  // endpoints it cannot reach - while the image and the initials still
  // render from what the group already carries.
  const calls = avatarOptions({ 'author-id': '3', 'author-username': 'alice', 'viewer-guest': '' });
  assert.equal(calls.length, 2, 'one avatar per density branch');
  for (const call of calls) {
    assert.equal(call.userId, '3');
    assert.equal(call.username, 'alice');
    assert.equal(call.options.presence, false);
    assert.equal(call.options.card, false);
  }
  assert.deepStrictEqual(calls.map((c) => c.options.size), ['sm', 'xs']);
});

test('a member viewer keeps the presence dot and the profile card', () => {
  const calls = avatarOptions({ 'author-id': '3', 'author-username': 'alice' });
  for (const call of calls) {
    assert.equal(call.options.presence, true);
    assert.equal(call.options.card, true);
  }
});
