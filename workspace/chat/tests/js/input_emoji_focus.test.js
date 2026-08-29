const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

// closeEmojiPicker reads document.activeElement, and openEmojiPicker measures
// the viewport, so the context needs both before the mixin runs.
const documentStub = { body: { tag: 'body' }, activeElement: null };
const ctx = loadScripts(
  [
    'workspace/common/static/ui/js/attachment_input.js',
    'workspace/chat/ui/static/chat/ui/js/input.js',
  ],
  { document: documentStub, innerWidth: 1280, innerHeight: 900 },
);

function makeInput() {
  return { className: 'search', focusCount: 0, focus() { this.focusCount += 1; } };
}

// The web component only builds its shadow DOM once its module has loaded, so
// a picker can legitimately be at any of these three stages.
function makeUpgradedPicker(searchInput) {
  return { shadowRoot: { querySelector: (sel) => (sel === 'input.search' ? searchInput : null) } };
}

function makePendingPicker() {
  return {}; // element parsed, module not loaded yet - no shadowRoot
}

function makeComposer({ picker, textarea } = {}) {
  return Object.assign(ctx.chatInputMixin(), {
    messageBody: '',
    getMessageInput: () => textarea,
    $refs: { emojiPicker: picker },
    $nextTick: (fn) => fn(),
  });
}

function triggerEvent() {
  return {
    currentTarget: {
      getBoundingClientRect: () => ({ left: 100, top: 400, bottom: 430, right: 130 }),
    },
  };
}

test('opening the picker focuses its search field', () => {
  const search = makeInput();
  const composer = makeComposer({ picker: makeUpgradedPicker(search) });

  composer.openEmojiPicker('input', triggerEvent());

  assert.equal(composer.emojiPickerVisible, true);
  assert.equal(search.focusCount, 1);
});

test('reopening focuses the search field again', () => {
  const search = makeInput();
  const composer = makeComposer({ picker: makeUpgradedPicker(search) });

  composer.openEmojiPicker('input', triggerEvent());
  composer.closeEmojiPicker();
  composer.openEmojiPicker('input', triggerEvent());

  // The picker is a singleton toggled with x-show, never re-created, so a
  // focus applied once at mount would leave every later open unfocused.
  assert.equal(search.focusCount, 2);
});

test('opening does not throw when the component has not upgraded yet', () => {
  const composer = makeComposer({ picker: makePendingPicker() });

  assert.doesNotThrow(() => composer.openEmojiPicker('input', triggerEvent()));
  assert.equal(composer.emojiPickerVisible, true);
});

test('opening does not throw when the picker ref is missing', () => {
  const composer = makeComposer({ picker: undefined });

  assert.doesNotThrow(() => composer.openEmojiPicker('input', triggerEvent()));
  assert.equal(composer.emojiPickerVisible, true);
});

test('closing hands focus back to the composer', () => {
  const search = makeInput();
  const picker = makeUpgradedPicker(search);
  const textarea = makeInput();
  const composer = makeComposer({ picker, textarea });

  composer.openEmojiPicker('input', triggerEvent());
  // Focus inside a shadow root reports the host element on document.
  documentStub.activeElement = picker;
  composer.closeEmojiPicker();

  assert.equal(textarea.focusCount, 1);
});

test('closing hands focus back when the click landed on dead space', () => {
  const search = makeInput();
  const picker = makeUpgradedPicker(search);
  const textarea = makeInput();
  const composer = makeComposer({ picker, textarea });

  composer.openEmojiPicker('input', triggerEvent());
  documentStub.activeElement = documentStub.body;
  composer.closeEmojiPicker();

  assert.equal(textarea.focusCount, 1);
});

test('closing does not steal focus from the element the user just clicked', () => {
  const search = makeInput();
  const picker = makeUpgradedPicker(search);
  const textarea = makeInput();
  const composer = makeComposer({ picker, textarea });

  composer.openEmojiPicker('input', triggerEvent());
  documentStub.activeElement = { tag: 'sidebar-search-input' };
  composer.closeEmojiPicker();

  assert.equal(textarea.focusCount, 0);
});

test('closing leaves focus alone when the search was never focused', () => {
  const textarea = makeInput();
  const composer = makeComposer({ picker: makePendingPicker(), textarea });

  composer.openEmojiPicker('input', triggerEvent());
  documentStub.activeElement = documentStub.body;
  composer.closeEmojiPicker();

  assert.equal(textarea.focusCount, 0);
});

test('closing resets the picker state', () => {
  const composer = makeComposer({ picker: makeUpgradedPicker(makeInput()) });

  composer.openEmojiPicker('reaction', triggerEvent(), 'msg-uuid');
  documentStub.activeElement = null;
  composer.closeEmojiPicker();

  assert.equal(composer.emojiPickerVisible, false);
  assert.equal(composer.emojiPickerMode, null);
  assert.equal(composer.emojiPickerTargetMsg, null);
});
