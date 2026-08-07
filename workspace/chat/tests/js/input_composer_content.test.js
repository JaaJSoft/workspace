const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/input.js');

// hasComposerContent drives the mobile mic/send swap: the mic shows only while
// the composer has nothing to send, the send button only once it has.
function makeComposer(overrides) {
  return Object.assign(ctx.chatInputMixin(), { messageBody: '' }, overrides || {});
}

test('empty composer has no content', () => {
  assert.equal(makeComposer().hasComposerContent(), false);
});

test('whitespace-only body does not count as content', () => {
  assert.equal(makeComposer({ messageBody: '   \n\t ' }).hasComposerContent(), false);
});

test('undefined body does not throw', () => {
  assert.equal(makeComposer({ messageBody: undefined }).hasComposerContent(), false);
});

test('typed text counts as content', () => {
  assert.equal(makeComposer({ messageBody: 'hello' }).hasComposerContent(), true);
});

test('a pending upload counts as content on its own', () => {
  assert.equal(makeComposer({ pendingFiles: [{ name: 'a.png' }] }).hasComposerContent(), true);
});

test('a pending workspace file counts as content on its own', () => {
  assert.equal(makeComposer({ pendingPickedFiles: [{ uuid: 'x' }] }).hasComposerContent(), true);
});
