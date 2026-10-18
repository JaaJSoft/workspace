// The floor a vault password must clear, tested on the mixin alone: onboarding
// and the password change both spread it, so nothing here may depend on either.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

function component(extra = {}) {
  const ctx = loadScripts(['workspace/vault/ui/static/vault/ui/js/vault_password_floor.js'], {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    fetch: async () => ({ ok: true, status: 200, text: async () => '' }),
    ...extra,
  });
  return ctx.vaultPasswordFloorMixin();
}

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
  app.breachStatus = 'clean';
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

test('a password nobody checked against the corpus cannot pass', () => {
  // The floor has three criteria and the corpus is one of them. Treating
  // "not checked yet" as "not found" silently drops it for anyone who never
  // blurs the field.
  const app = component();
  app.password = 'correct horse battery staple';
  app.confirmation = app.password;
  app.score = 4;
  assert.equal(app.breachStatus, 'unchecked');
  assert.equal(app.passwordAcceptable(), false);
  app.breachStatus = 'clean';
  assert.equal(app.passwordAcceptable(), true);
});

test('a corpus that could not be reached still lets the user through', () => {
  const app = component();
  app.password = 'correct horse battery staple';
  app.confirmation = app.password;
  app.score = 4;
  app.breachStatus = 'unavailable';
  assert.equal(app.passwordAcceptable(), true);
});

test('a corpus answer about a replaced password is discarded', async () => {
  // Two lookups in flight, the debounce being shorter than the round trip. If
  // the older one is allowed to land last, "clean" overwrites "found" and a
  // breached password walks through the floor.
  const sha1 = require('node:crypto');
  const digest = (value) =>
    sha1.createHash('sha1').update(value).digest('hex').toUpperCase();
  // Keyed by the range each lookup asked for, not by call order: the two
  // digests run on the thread pool and either may reach fetch first.
  const resolvers = new Map();
  const app = component({
    fetch: (url) => new Promise((resolve) => resolvers.set(url.slice(-5), resolve)),
  });

  app.password = 'a-password-nobody-has-used';
  const stale = app.checkBreachCorpus();
  app.generation++;
  app.password = 'password1';
  const current = app.checkBreachCorpus();
  // Let both hash and reach the stubbed fetch.
  while (resolvers.size < 2) await new Promise((r) => setTimeout(r, 1));

  resolvers.get(digest('password1').slice(0, 5))({
    ok: true,
    text: async () => digest('password1').slice(5) + ':42',
  });
  await current;
  assert.equal(app.breachStatus, 'found');

  resolvers.get(digest('a-password-nobody-has-used').slice(0, 5))({
    ok: true,
    text: async () => 'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:1',
  });
  await stale;
  assert.equal(app.breachStatus, 'found');
});

test('editing the password drops the previous verdict at once', () => {
  // x-model writes the field synchronously, the corpus lookup waits 400 ms.
  // Between the two the floor must not still be answering about the old value.
  const app = component();
  app.password = 'a-strong-one-nobody-has-used';
  app.score = 4;
  app.breachStatus = 'clean';
  app.confirmation = app.password;
  assert.equal(app.passwordAcceptable(), true);

  app.password = 'password1';
  app.confirmation = 'password1';
  app.passwordEdited();
  assert.equal(app.passwordAcceptable(), false, 'the old verdict survived');
  assert.equal(app.breachStatus, 'unchecked');
});

test('an input event alone never clears the score', () => {
  // The score belongs to the meter, which speaks from an Alpine effect - and
  // an effect does not re-run when a property is assigned the value it already
  // holds. A password manager re-filling the field with what is already there
  // is exactly that, so a score cleared on input would never come back and the
  // step would stay shut on a password the bar calls very strong.
  // tests/e2e/test_onboarding_browser.py walks that one in a browser; here the
  // point is simply that nothing on this side touches the value.
  const app = component();
  app.password = 'a-strong-one-nobody-has-used';
  app.confirmation = app.password;
  app.strengthChanged({ status: 'ready', score: 4 });

  app.passwordEdited();
  assert.equal(app.score, 4, 'the floor threw away a verdict it cannot ask for again');
});

test('the score comes from the meter, and only from a landed estimate', () => {
  const app = component();
  app.strengthChanged({ status: 'ready', score: 4 });
  assert.equal(app.score, 4);
  assert.equal(app.passwordStrongEnough(), true);

  // Every status short of a landed estimate is an unmeasured password, and an
  // unmeasured password is not a strong one.
  for (const status of ['checking', 'idle']) {
    app.strengthChanged({ status, score: null });
    assert.equal(app.score, null, status + ' left a score behind');
    assert.equal(app.passwordStrongEnough(), false);
  }
});

test('an estimator that could not run keeps the floor closed', () => {
  // The button is gated on the score. The meter says "could not be checked" on
  // screen; what must not happen here is the floor opening because nothing
  // came back.
  const app = component();
  app.strengthChanged({ status: 'ready', score: 4 });
  app.strengthChanged({ status: 'unavailable', score: null });
  assert.equal(app.score, null);
  assert.equal(app.passwordStrongEnough(), false);
});

test('a score below the norm is refused even when the meter reports one', () => {
  const app = component();
  app.password = 'a'.repeat(20);
  app.confirmation = app.password;
  app.breachStatus = 'clean';
  app.strengthChanged({ status: 'ready', score: 2 });
  assert.equal(app.passwordAcceptable(), false);
  app.strengthChanged({ status: 'ready', score: 3 });
  assert.equal(app.passwordAcceptable(), true);
});

test('the mixin never reaches for the estimator itself', () => {
  // The meter under the field owns the estimate. A second caller here would
  // put the two on different debounces and let the floor answer about a
  // password the bar is no longer describing.
  const source = require('node:fs').readFileSync(
    'workspace/vault/ui/static/vault/ui/js/vault_password_floor.js', 'utf8'
  );
  assert.doesNotMatch(source, /estimateStrength/);
  assert.doesNotMatch(source, /passwordStrengthTools/);
});
