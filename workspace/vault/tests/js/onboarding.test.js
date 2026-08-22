// The component is state plus two POSTs; the crypto it calls is already
// pinned by the vector suites. What matters here is that no step can be
// skipped and that the strength floor counts what the norm says it counts.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function component(extra = {}) {
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/onboarding.js', {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    document: { cookie: '', createElement: () => ({ click() {} }) },
    fetch: async () => ({ ok: true, status: 201, json: async () => ({}) }),
    ...extra,
  });
  return ctx.vaultOnboarding();
}

test('the flow starts on the first step', () => {
  assert.equal(component().step, 1);
});

test('the kit step cannot be left until the box is ticked', () => {
  const app = component();
  app.step = 3;
  assert.equal(app.canFinish(), false);
  app.acknowledged = true;
  assert.equal(app.canFinish(), true);
});

test('a password under twelve code points is refused', () => {
  const app = component();
  app.password = 'short';
  assert.equal(app.passwordLongEnough(), false);
  app.password = 'a'.repeat(12);
  assert.equal(app.passwordLongEnough(), true);
});

test('length is counted in code points, not UTF-16 units', () => {
  const app = component();
  // Six characters to a human, twelve UTF-16 units to String.length: a floor
  // counting units would let half a password through.
  app.password = '\u{1F600}'.repeat(6);
  assert.equal(app.password.length, 12);
  assert.equal(app.passwordLongEnough(), false);
});

test('length is counted after NFC normalization', () => {
  const app = component();
  // Twelve base letters plus twelve combining accents, which compose into
  // twelve code points and must not be counted as twenty-four.
  app.password = 'é'.repeat(12);
  assert.equal(app.passwordLongEnough(), true);
  app.password = 'é'.repeat(6);
  assert.equal(app.passwordLongEnough(), false);
});

test('a weak but long password is still refused', () => {
  const app = component();
  app.password = 'a'.repeat(20);
  app.confirmation = app.password;
  app.score = 1;
  assert.equal(app.passwordAcceptable(), false);
  app.score = 3;
  assert.equal(app.passwordAcceptable(), true);
});

test('a mismatched confirmation blocks the step', () => {
  const app = component();
  app.password = 'correct horse battery';
  app.confirmation = 'correct horse batteru';
  app.score = 4;
  assert.equal(app.passwordAcceptable(), false);
});

test('a breach lookup that could not run warns without blocking', () => {
  const app = component();
  app.breachStatus = 'unavailable';
  assert.equal(app.passwordBlocked(), false);
  app.breachStatus = 'found';
  assert.equal(app.passwordBlocked(), true);
});

test('the secret is grouped so it can be copied by hand', () => {
  const app = component();
  app.secretText = 'ABCDEFGHIJ';
  assert.equal(app.groupedSecret(), 'ABCD-EFGH-IJ');
});
