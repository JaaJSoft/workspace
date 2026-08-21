const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const ctx = loadScripts([
  'workspace/common/static/ui/js/attachment_input.js',
  'workspace/chat/ui/static/chat/ui/js/input.js',
]);

// Minimal composer: handleMentionInput only reads selectionStart/value from the
// textarea and needs a member list for filterMentionResults.
function makeComposer(value, members) {
  const comp = ctx.chatInputMixin();
  comp.getMessageInput = () => ({ value, selectionStart: value.length });
  comp.activeConversation = {
    members: (members || []).map((username, i) => ({
      user: { id: i + 1, username, display_name: username },
    })),
  };
  return comp;
}

test('mention dropdown opens on @ after whitespace', () => {
  const comp = makeComposer('hello @al', ['alice']);
  comp.handleMentionInput();
  assert.equal(comp.mentionActive, true);
  assert.equal(comp.mentionQuery, 'al');
});

test('mention dropdown keeps filtering past a dot', () => {
  const comp = makeComposer('hello @jean.du', ['jean.dupont']);
  comp.handleMentionInput();
  assert.equal(comp.mentionActive, true);
  assert.equal(comp.mentionQuery, 'jean.du');
  assert.equal(comp.mentionStartPos, 6);
});

test('mention dropdown keeps filtering past a hyphen', () => {
  const comp = makeComposer('hi @marie-cl', ['marie-claire']);
  comp.handleMentionInput();
  assert.equal(comp.mentionActive, true);
  assert.equal(comp.mentionQuery, 'marie-cl');
});

test('an email typed after whitespace never opens the dropdown', () => {
  const comp = makeComposer('write to alice@exa', ['alice']);
  comp.handleMentionInput();
  assert.equal(comp.mentionActive, false);
});
