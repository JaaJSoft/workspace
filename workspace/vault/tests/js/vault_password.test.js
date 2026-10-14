const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const ENVELOPE = {
  uuid: 'acct', kdf_salt: 'c2FsdA', kdf_algo: 'argon2id',
  kdf_params: { v: '1.3', m: 65536, t: 3, p: 2 },
  wrapped_kex_priv: 'old-kex', wrapped_sig_priv: 'old-sig',
};
const CURRENT = { algo: 'argon2id', params: { v: '1.3', m: 65536, t: 3, p: 2 } };

// The envelope helper is stubbed - its own behaviour is pinned by
// account_envelope.test.js. What is under test is the dialog's order of steps
// and what it sends.
function harness(overrides = {}) {
  const posts = [];
  const derived = [];
  const buffers = [];
  const session = { locks: 0, remembered: overrides.remembered || null };
  const fresh = (n) => { const b = Uint8Array.from({ length: 4 }, () => n); buffers.push(b); return b; };
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/vault_password_floor.js',
      'workspace/vault/ui/static/vault/ui/js/vault_password.js',
    ],
    {
      vaultCrypto: {
        CURRENT_SUITE: { kdf: CURRENT },
        crockfordDecode: () => fresh(9),
        fromBase64Url: () => new Uint8Array(32),
      },
      vaultEnvelope: {
        deriveUnwrapKey: async (fields, password) => { derived.push([fields.kdf_params, password]); return fresh(7); },
        openKeys: overrides.openKeys || (async () => ({ kexPriv: fresh(1), sigSeed: fresh(2) })),
        sealAndReread: overrides.sealAndReread || (async () => ({ wrapped_kex_priv: 'new-kex', wrapped_sig_priv: 'new-sig' })),
        rotateBody: (sealed, expected) => ({ ...sealed, expected_wrapped_kex_priv: expected }),
      },
      vaultApi: {
        fetchEnvelope: async () => ENVELOPE,
        rotateEnvelope: overrides.rotateEnvelope || (async (body) => { posts.push(body); return null; }),
      },
      vaultSession: {
        rememberedSecret: () => session.remembered,
        lock: () => { session.locks += 1; },
      },
      fetch: async () => ({ ok: true, text: async () => '' }),
      crypto: globalThis.crypto,
      TextEncoder: globalThis.TextEncoder,
    }
  );
  const component = ctx.vaultPasswordChange();
  const events = [];
  component.$dispatch = (name) => events.push(name);
  return { ctx, component, posts, derived, buffers, session, events };
}

// Opens the floor without a network round trip: the floor's own behaviour is
// pinned by vault_password_floor.test.js.
function ready(component, overrides = {}) {
  Object.assign(component, {
    currentPassword: 'old password',
    secretText: 'A'.repeat(53),
    password: 'orbit-lantern-pebble-quartz-71',
    confirmation: 'orbit-lantern-pebble-quartz-71',
    score: 4,
    breachStatus: 'clean',
    ...overrides,
  });
}

test('a floor not met keeps submit closed', async () => {
  const h = harness();
  ready(h.component, { score: 2 });
  assert.equal(h.component.canSubmit(), false);
  await h.component.submit();
  assert.deepStrictEqual(h.posts, []);
});

test('a wrong current password sends nothing', async () => {
  const h = harness({ openKeys: async () => { throw new Error('tag mismatch'); } });
  ready(h.component);
  await h.component.submit();
  assert.equal(h.component.error, 'Current password is incorrect.');
  assert.deepStrictEqual(h.posts, []);
  assert.equal(h.session.locks, 0);
});

test('a reseal that does not reread sends nothing', async () => {
  const h = harness({
    sealAndReread: async () => { const e = new Error('x'); e.name = 'VaultEnvelopeMismatch'; throw e; },
  });
  ready(h.component);
  await h.component.submit();
  assert.equal(h.component.error, 'The new password could not be applied.');
  assert.deepStrictEqual(h.posts, []);
});

test('the derivations use the current envelope params, then the current suite', async () => {
  const h = harness();
  ready(h.component);
  await h.component.submit();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(h.derived)), [
    [ENVELOPE.kdf_params, 'old password'],
    [CURRENT.params, 'orbit-lantern-pebble-quartz-71'],
  ]);
});

test('a success sends against the envelope read, tells the page, then locks', async () => {
  const h = harness();
  ready(h.component);
  await h.component.submit();
  assert.equal(h.posts.length, 1);
  assert.equal(h.posts[0].expected_wrapped_kex_priv, 'old-kex');
  assert.equal(h.posts[0].wrapped_kex_priv, 'new-kex');
  assert.deepStrictEqual(h.events, ['vault-password-changed']);
  assert.equal(h.session.locks, 1);
});

test('a 409 says another tab changed the account and keeps the session open', async () => {
  const h = harness({ rotateEnvelope: async () => { const e = new Error('x'); e.status = 409; throw e; } });
  ready(h.component);
  await h.component.submit();
  assert.equal(h.component.error, 'Your account changed in another tab. Reload and try again.');
  assert.equal(h.session.locks, 0);
  assert.deepStrictEqual(h.events, []);
});

test('the raw keys and derived keys are zeroed on success and on failure', async () => {
  for (const overrides of [{}, { rotateEnvelope: async () => { throw new Error('down'); } }]) {
    const h = harness(overrides);
    ready(h.component);
    await h.component.submit();
    assert.ok(h.buffers.length >= 5);
    for (const buffer of h.buffers) assert.deepStrictEqual(Array.from(buffer), [0, 0, 0, 0]);
  }
});

test('the secret is prefilled from the device when remembered', () => {
  const h = harness({ remembered: 'B'.repeat(53) });
  h.component.init();
  assert.equal(h.component.secretText, 'B'.repeat(53));
});

test('a submit that resolves after the dialog went away neither reports nor locks', async () => {
  let release;
  const h = harness({ rotateEnvelope: () => new Promise((resolve) => { release = resolve; }) });
  ready(h.component);
  const pending = h.component.submit();
  await new Promise((resolve) => setImmediate(resolve));
  h.component.destroy();
  release(null);
  await pending;
  assert.deepStrictEqual(h.events, []);
  assert.equal(h.session.locks, 0);
  assert.equal(h.component.password, '');
  assert.equal(h.component.currentPassword, '');
});

test('a generated password fills both fields and is followed until the user types', () => {
  const h = harness();
  const c = h.component;
  c.applyGeneratedPassword('x y z');
  assert.equal(c.password, 'x y z');
  assert.equal(c.confirmation, 'x y z');
  assert.equal(c.followsGenerator, true);
  c.trackGeneratedPassword('a b c');
  assert.equal(c.password, 'a b c');
  assert.equal(c.confirmation, 'a b c');
  c.followsGenerator = false;
  c.trackGeneratedPassword('d e f');
  assert.equal(c.password, 'a b c');
  assert.equal(c.confirmation, 'a b c');
});

test('the root mixin closes the dialog on lock and shows the notice after a change', () => {
  const h = harness();
  const root = h.ctx.vaultPasswordDialogMixin();
  root.openPasswordDialog();
  assert.equal(root.passwordDialogOpen, true);
  root.passwordChangeSucceeded();
  assert.equal(root.passwordDialogOpen, false);
  assert.equal(root.passwordChangedNotice, true);
  root.openPasswordDialog();
  assert.equal(root.passwordChangedNotice, false);
  root.clearPasswordDialog();
  assert.equal(root.passwordDialogOpen, false);
});
