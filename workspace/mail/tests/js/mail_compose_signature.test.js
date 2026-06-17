'use strict';

// Regression test for: _sigBlock not persisted to localStorage draft
// -> restoring a draft + switching accounts appends a second signature.
//
// This test pins the persist side of the fix: _saveComposeToLocalStorage()
// must write _sigBlock so the restore branch can hand the correct oldBlock to
// swapSignature. The swapSignature replace-when-given-correct-oldBlock
// behaviour is already covered by mail_signature.test.js.
//
// The showCompose restore branch is NOT tested here because it is tightly
// coupled to AppDialog.confirm (an async modal) and document.getElementById,
// both of which require non-trivial DOM stubs that would make the test brittle
// and couple it to implementation details rather than the observable guarantee.
// The guarantee tested here - that _sigBlock survives a round-trip through
// localStorage - is the necessary condition for the restore branch to work
// correctly, and is sufficient to detect a regression of the original bug.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// ----- Minimal stubs needed at load time -----

// localStorage stub - stores items in a plain object
const localStorageStub = (() => {
  const store = {};
  return {
    setItem(k, v) { store[k] = String(v); },
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    removeItem(k) { delete store[k]; },
    _store: store,
  };
})();

// _defaultCompose and _parseEmails are defined in mail.js (classic script).
// Provide minimal inline versions so mail_compose.js loads cleanly without
// pulling in the entire mail.js dependency tree.
function _defaultCompose() {
  return {
    account_id: '', to: [], cc: [], bcc: [],
    subject: '', body: '', is_reply: false, reply_message_id: null,
    attachments: [], picked_files: [], sending: false, error: '',
    draft_id: null, saving: false, last_saved: null,
    _saveTimer: null, _sigBlock: '',
  };
}

function _parseEmails(str) {
  if (Array.isArray(str)) return str.filter(Boolean);
  if (!str || typeof str !== 'string') return [];
  return str.split(/[,;]\s*/).map(s => s.trim()).filter(Boolean);
}

const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_compose.js', {
  localStorage: localStorageStub,
  _defaultCompose,
  _parseEmails,
  // AppDialog and document are only touched inside showCompose / closeCompose,
  // not inside _saveComposeToLocalStorage, so they are not needed here.
  AppDialog: {},
  document: { getElementById: () => ({ showModal() {}, close() {} }) },
  window: { mailSignature: {}, clipboardData: null },
});

// mailComposeMixin is registered as window.mailComposeMixin
const mixin = ctx.mailComposeMixin;

// Helper: build a minimal "this" context that mailComposeMixin methods
// expect. Methods are mixed into a component object - bind each method to an
// instance that exposes the required state fields.
function makeInstance() {
  const instance = {
    compose: _defaultCompose(),
    accounts: [],
    showCcBcc: false,
    // Pull all methods from the mixin onto this object
    ...mixin(),
  };
  // Bind each method so `this` resolves to instance
  for (const key of Object.keys(instance)) {
    if (typeof instance[key] === 'function') {
      instance[key] = instance[key].bind(instance);
    }
  }
  return instance;
}

test('_saveComposeToLocalStorage persists _sigBlock and it round-trips via _getLocalStorageDraft', () => {
  // Reset the store before the test
  delete localStorageStub._store['mail_compose_draft'];

  const app = makeInstance();
  const knownSigBlock = '\n-- \nTest Signature\n';

  // Set compose fields including a known _sigBlock
  app.compose.subject = 'Hello';
  app.compose.body = 'Body text' + knownSigBlock;
  app.compose._sigBlock = knownSigBlock;
  app.compose.to = ['recipient@example.com'];

  app._saveComposeToLocalStorage();

  const saved = app._getLocalStorageDraft();
  assert.notEqual(saved, null, 'draft should have been saved to localStorage');

  // This assertion FAILS against the buggy code (where _sigBlock is not
  // included in the persisted payload) and PASSES after the fix.
  assert.equal(
    saved._sigBlock,
    knownSigBlock,
    '_sigBlock must round-trip through localStorage so the restore branch can hand the correct oldBlock to swapSignature',
  );
});

test('_saveComposeToLocalStorage with empty _sigBlock persists empty string', () => {
  delete localStorageStub._store['mail_compose_draft'];

  const app = makeInstance();
  app.compose.subject = 'No sig';
  app.compose.body = 'Just text';
  app.compose._sigBlock = '';
  app.compose.to = ['a@b.com'];

  app._saveComposeToLocalStorage();

  const saved = app._getLocalStorageDraft();
  assert.notEqual(saved, null);
  // Empty _sigBlock should be persisted (as '' or undefined-but-falsy is fine,
  // but the restore branch does `saved._sigBlock || ''` so both are safe).
  assert.equal(saved._sigBlock || '', '', 'empty _sigBlock should round-trip as empty');
});
