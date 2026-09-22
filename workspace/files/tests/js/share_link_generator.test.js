// The share-link form hosting the shared password generator panel: what the
// field holds after each panel event, what the create request carries, and
// where the generated value may and may not end up.
const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

const WORDLIST = 'workspace/common/static/ui/js/password_wordlist.js';
const GENERATOR = 'workspace/common/static/ui/js/password_generator.js';
const SHARE_MODAL = 'workspace/files/ui/static/files/ui/js/share_modal.js';

function fakeStorage() {
  const entries = new Map();
  return {
    entries,
    getItem: (key) => (entries.has(key) ? entries.get(key) : null),
    setItem: (key, value) => entries.set(key, String(value)),
    removeItem: (key) => entries.delete(key),
  };
}

function setup(globals = {}) {
  const requests = [];
  const ctx = loadScripts([WORDLIST, GENERATOR, SHARE_MODAL], {
    crypto: globalThis.crypto,
    localStorage: fakeStorage(),
    getCSRFToken: () => 'csrf',
    fetch: async (url, init) => {
      requests.push({ url, init });
      return { ok: true, json: async () => [] };
    },
    ...globals,
  });
  const modal = ctx.shareModal();
  modal.fileUuid = 'file-uuid';
  modal.showLinkForm = true;
  modal.linkGeneratorOpen = true;
  // The panel's events bubble to the wrapper the template binds on the host;
  // routing them the same way here is that binding.
  const panel = ctx.passwordGeneratorPanel({}, {});
  panel.$watch = () => {};
  panel.$dispatch = (name, detail) => {
    if (name === 'password-apply') modal.applyGeneratedLinkPassword(detail.value);
    if (name === 'password-regenerate') modal.trackGeneratedLinkPassword(detail.value);
    if (name === 'password-copy') modal.copyGeneratedLinkPassword(detail.value);
  };
  panel.init();
  return { ctx, modal, panel, requests };
}

test('Use writes the drawn value into the field and folds the panel', () => {
  const { modal, panel } = setup();
  panel.apply();
  assert.ok(panel.value);
  assert.equal(modal.newLinkPassword, panel.value);
  assert.equal(modal.linkGeneratorOpen, false);
});

test('an applied password is shown, since the sender has to pass it on', () => {
  const { modal, panel } = setup();
  assert.equal(modal.linkPasswordRevealed, false);
  panel.apply();
  assert.equal(modal.linkPasswordRevealed, true);
});

test('the generated value is the one the create request stores', async () => {
  const { modal, panel, requests } = setup();
  panel.apply();
  const applied = modal.newLinkPassword;
  await modal.createShareLink();

  const create = requests.find((r) => r.init && r.init.method === 'POST');
  assert.equal(create.url, '/api/v1/files/file-uuid/share-links');
  assert.equal(JSON.parse(create.init.body).password, applied);
});

test('a redraw after Use follows into the field', () => {
  const { modal, panel } = setup();
  panel.apply();
  panel.regenerate();
  assert.equal(modal.newLinkPassword, panel.value);
});

test('a redraw never overwrites a password the sender typed after Use', () => {
  const { modal, panel } = setup();
  panel.apply();
  modal.newLinkPassword = 'typed-by-hand';
  modal.noteLinkPasswordEdited();
  panel.regenerate();
  assert.equal(modal.newLinkPassword, 'typed-by-hand');
});

test('a redraw before any Use leaves the field alone', () => {
  const { modal, panel } = setup();
  modal.newLinkPassword = 'typed-by-hand';
  panel.regenerate();
  assert.equal(modal.newLinkPassword, 'typed-by-hand');
});

test('a failed draw does not strip the password already applied', () => {
  const { modal, panel } = setup();
  panel.apply();
  const applied = modal.newLinkPassword;
  panel.upper = panel.lower = panel.digits = panel.symbols = false;
  panel.regenerate();
  assert.equal(panel.value, '');
  assert.equal(modal.newLinkPassword, applied);
});

test('Copy goes through the plain clipboard', async () => {
  const written = [];
  const { modal, panel } = setup({
    navigator: { clipboard: { writeText: async (text) => written.push(text) } },
  });
  await modal.copyGeneratedLinkPassword(panel.value);
  assert.deepStrictEqual(written, [panel.value]);
  assert.equal(modal.linkGeneratorError, '');
});

test('a refused copy is reported beside the panel', async () => {
  const { modal, panel } = setup({
    navigator: { clipboard: { writeText: async () => { throw new Error('denied'); } } },
  });
  await modal.copyGeneratedLinkPassword(panel.value);
  assert.match(modal.linkGeneratorError, /could not be copied/);
});

test('the generated password never reaches localStorage', async () => {
  const { ctx, modal, panel } = setup();
  const seen = [];
  panel.persist();
  panel.apply();
  seen.push(modal.newLinkPassword);
  modal.linkGeneratorOpen = true;
  panel.length = 32;
  panel.regenerate();
  seen.push(panel.value);
  panel.persist();
  await modal.createShareLink();

  const stored = Array.from(ctx.localStorage.entries.values()).join('\n');
  for (const value of seen) assert.ok(!stored.includes(value));
});

test('cancelling the form clears the password and the generator state', () => {
  const { modal, panel } = setup();
  panel.apply();
  modal.linkGeneratorOpen = true;
  modal.linkGeneratorError = 'stale';
  modal.closeLinkForm();
  assert.equal(modal.showLinkForm, false);
  assert.equal(modal.newLinkPassword, '');
  assert.equal(modal.linkGeneratorOpen, false);
  assert.equal(modal.linkPasswordFollowsGenerator, false);
  assert.equal(modal.linkPasswordRevealed, false);
  assert.equal(modal.linkGeneratorError, '');
});

test('a panel still open after cancel cannot write into the next link', () => {
  const { modal, panel } = setup();
  panel.apply();
  modal.closeLinkForm();
  panel.regenerate();
  assert.equal(modal.newLinkPassword, '');
});
