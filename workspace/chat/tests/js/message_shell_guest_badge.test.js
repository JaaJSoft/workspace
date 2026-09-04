'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript, CUSTOM_ELEMENT_STUBS } = require('../../../common/tests/js/loader');

/**
 * Only _header() is exercisable without a real DOM (see the note atop
 * messages_optimistic.test.js: render() needs classList/children/
 * replaceChildren, out of reach for the node:vm loader). _header() touches
 * nothing but getAttribute/hasAttribute and the document stub's
 * createElement, so capturing the class through customElements.define and
 * calling the method on a bare instance is enough to pin the guest badge.
 */
function captureShellClass() {
  let Shell;
  loadScript('workspace/chat/ui/static/chat/ui/js/message_shell.js', {
    ...CUSTOM_ELEMENT_STUBS,
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

function renderHeader(attrs) {
  const Shell = captureShellClass();
  const instance = Object.create(Shell.prototype);
  instance.getAttribute = (name) => (name in attrs ? attrs[name] : null);
  instance.hasAttribute = (name) => name in attrs;
  return instance._header();
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
