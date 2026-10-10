const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

function load(prefs) {
  return loadScripts(
    [
      'workspace/common/static/ui/js/attachment_input.js',
      'workspace/chat/ui/static/chat/ui/js/input.js',
    ],
    { _chatPrefsCache: prefs },
  );
}

test('the composer starts in the formatted mode by default', () => {
  const comp = load({}).chatInputMixin();
  assert.equal(comp.composerMode, 'rendered');
});

test('the composer starts in the markdown mode when the preference says so', () => {
  const comp = load({ composerMode: 'markdown' }).chatInputMixin();
  assert.equal(comp.composerMode, 'markdown');
});

test('until the editor has loaded, the formatted mode is not active', () => {
  const comp = load({}).chatInputMixin();
  assert.equal(comp.richComposerActive(), false);
  assert.equal(comp.formatActive('bold'), false);
});

test('without the editor, a format wraps the selection in its markdown markers', () => {
  const ctx = load({});
  const textarea = {
    selectionStart: 6,
    selectionEnd: 10,
    focus() {},
    setSelectionRange() {},
  };
  const comp = Object.assign(ctx.chatInputMixin(), {
    messageBody: 'hello word!',
    getMessageInput: () => textarea,
    $nextTick: (fn) => fn(),
  });

  comp.applyFormat('strike');

  assert.equal(comp.messageBody, 'hello ~~word~~!');
});

test('the toggle saves the other mode through the shared preference', () => {
  const ctx = load({});
  const calls = [];
  ctx.updateChatPref = (key, value) => calls.push([key, value]);
  const comp = Object.assign(ctx.chatInputMixin(), {
    $nextTick: () => {},
  });

  comp.toggleComposerMode();

  assert.deepStrictEqual(calls.map((c) => [...c]), [['composerMode', 'markdown']]);
});

test('a preference change switches the mode; an unknown value means formatted', () => {
  const comp = Object.assign(load({ composerMode: 'markdown' }).chatInputMixin(), {
    $nextTick: () => {},
    _loadRichComposer: () => Promise.resolve(),
  });

  comp._applyComposerMode(undefined);
  assert.equal(comp.composerMode, 'rendered');

  comp._applyComposerMode('markdown');
  assert.equal(comp.composerMode, 'markdown');
});
