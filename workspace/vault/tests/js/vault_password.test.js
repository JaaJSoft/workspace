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
  const zeroAtSend = [];
  const order = [];
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
        crockfordDecode: overrides.crockfordDecode || (() => fresh(9)),
        fromBase64Url: () => new Uint8Array(32),
      },
      vaultEnvelope: {
        deriveUnwrapKey: overrides.deriveUnwrapKey
          ? async (fields, password) => { derived.push([fields.kdf_params, password]); return overrides.deriveUnwrapKey(fresh); }
          : async (fields, password) => { derived.push([fields.kdf_params, password]); return fresh(7); },
        openKeys: overrides.openKeys || (async () => ({ kexPriv: fresh(1), sigSeed: fresh(2) })),
        // The contract of the real helper, pinned by account_envelope.test.js:
        // a failed open is a VaultEnvelopeWrongPassword, an unreadable
        // algorithm passes through untouched.
        openWithFallback: overrides.openWithFallback || async function (envelope, password) {
          const unwrapKey = await this.deriveUnwrapKey(envelope, password);
          try {
            return { opened: "current", unwrapKey, keys: await this.openKeys(unwrapKey, envelope) };
          } catch (err) {
            if (err && err.name === "UnsupportedAlgorithmError") throw err;
            const wrong = new Error("wrong"); wrong.name = "VaultEnvelopeWrongPassword"; throw wrong;
          }
        },
        sealAndReread: overrides.sealAndReread || (async () => ({ wrapped_kex_priv: 'new-kex', wrapped_sig_priv: 'new-sig' })),
        rotateBody: (sealed, expected) => ({ ...sealed, expected_wrapped_kex_priv: expected }),
        previousOf: (envelope) => (envelope.previous_wrapped_kex_priv ? {
          kdf_algo: envelope.previous_kdf_algo,
          kdf_params: envelope.previous_kdf_params,
          wrapped_kex_priv: envelope.previous_wrapped_kex_priv,
          wrapped_sig_priv: envelope.previous_wrapped_sig_priv,
        } : null),
      },
      vaultApi: {
        fetchEnvelope: async () => overrides.envelope || ENVELOPE,
        confirmEnvelope: overrides.confirmEnvelope || (async (expected) => { order.push(["confirm", expected]); }),
        rotateEnvelope: overrides.rotateEnvelope || (async (body) => {
          order.push(["rotate"]);
          posts.push(body);
          zeroAtSend.push(buffers.every((b) => b.every((byte) => byte === 0)));
          return null;
        }),
      },
      vaultSession: {
        rememberedSecret: () => session.remembered,
        envelopeWrites: overrides.envelopeWrites || (() => null),
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
  return { ctx, component, posts, derived, buffers, session, events, zeroAtSend, order };
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
  assert.equal(h.component.error, 'Current password or recovery key is incorrect.');
  assert.deepStrictEqual(h.posts, []);
  assert.equal(h.session.locks, 0);
});

test('with a remembered key, a failed open can only blame the password', async () => {
  const h = harness({ remembered: 'B'.repeat(53), openKeys: async () => { throw new Error('tag mismatch'); } });
  h.component.init();
  ready(h.component, { secretText: h.component.secretText });
  await h.component.submit();
  assert.equal(h.component.error, 'Current password is incorrect.');
  assert.deepStrictEqual(h.posts, []);
});

test('a recovery key that does not decode says so, sends nothing and asks for the key', async () => {
  const h = harness({
    remembered: 'B'.repeat(53),
    crockfordDecode: () => { throw new Error('bad symbol'); },
  });
  h.component.init();
  ready(h.component, { secretText: h.component.secretText });
  await h.component.submit();
  assert.equal(
    h.component.error,
    'Your recovery key could not be read. Dashes and case do not matter - check it against your emergency kit.'
  );
  assert.deepStrictEqual(h.derived, []);
  assert.deepStrictEqual(h.posts, []);
  assert.equal(h.component.secretRemembered, false);
  assert.equal(h.component.secretText, '');
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

test('the raw and derived keys are already zero when the rotation leaves', async () => {
  const h = harness();
  ready(h.component);
  await h.component.submit();
  assert.deepStrictEqual(h.zeroAtSend, [true]);
});

test('a dialog that went away during the first derivation sends nothing', async () => {
  // destroy() empties the fields: a flow that read them after an await would
  // go on to seal the keys under an empty password and send that.
  let release;
  let calls = 0;
  const h = harness({
    deriveUnwrapKey: (fresh) => {
      calls += 1;
      if (calls > 1) return fresh(7);
      return new Promise((resolve) => { release = () => resolve(fresh(7)); });
    },
  });
  ready(h.component);
  const pending = h.component.submit();
  await new Promise((resolve) => setImmediate(resolve));
  h.component.destroy();
  release();
  await pending;
  assert.deepStrictEqual(h.posts, []);
  assert.deepStrictEqual(h.events, []);
  assert.equal(h.session.locks, 0);
  for (const [, password] of h.derived) assert.notEqual(password, '');
  for (const buffer of h.buffers) assert.deepStrictEqual(Array.from(buffer), [0, 0, 0, 0]);
});

test('a dialog that went away during the reseal sends nothing', async () => {
  let release;
  const h = harness({
    sealAndReread: () => new Promise((resolve) => {
      release = () => resolve({ wrapped_kex_priv: 'new-kex', wrapped_sig_priv: 'new-sig' });
    }),
  });
  ready(h.component);
  const pending = h.component.submit();
  while (!release) await new Promise((resolve) => setImmediate(resolve));
  h.component.destroy();
  release();
  await pending;
  assert.deepStrictEqual(h.posts, []);
  assert.deepStrictEqual(h.events, []);
  assert.equal(h.session.locks, 0);
});

test('a remembered secret is used without being asked for', () => {
  const h = harness({ remembered: 'B'.repeat(53) });
  h.component.init();
  assert.equal(h.component.secretText, 'B'.repeat(53));
  assert.equal(h.component.secretRemembered, true);
});

test('without a remembered secret the field is asked for, empty', () => {
  const h = harness();
  h.component.init();
  assert.equal(h.component.secretText, '');
  assert.equal(h.component.secretRemembered, false);
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
  const root = h.ctx.vaultResealDialogMixin();
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

test('a copied master password is named and outlives an entry password', async () => {
  const clip = { value: '' };
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/clipboard.js',
      'workspace/vault/ui/static/vault/ui/js/vault_password_floor.js',
      'workspace/vault/ui/static/vault/ui/js/vault_password.js',
    ],
    {
      navigator: { clipboard: { writeText: async (text) => { clip.value = text; } } },
      setInterval: () => 1,
      clearInterval: () => {},
    }
  );
  const policy = ctx.vaultPasswordChange().masterPasswordClipboardPolicy();

  await ctx.vaultClipboard.copy('Password', 'entry', { transient: true });
  const entryWindow = ctx.vaultClipboard.state().secondsLeft;
  await ctx.vaultClipboard.copy(policy.label, 'master', { transient: true, seconds: policy.seconds });

  assert.ok(
    ctx.vaultClipboard.state().secondsLeft > entryWindow,
    `the master password gets ${ctx.vaultClipboard.state().secondsLeft}s, an entry password ${entryWindow}s`
  );
  assert.equal(ctx.vaultClipboard.state().label, 'Master password');
});

// --- opening the envelope the dialog replaces --------------------------------

const WITH_PREVIOUS = {
  ...ENVELOPE,
  previous_kdf_algo: 'argon2id',
  previous_kdf_params: { v: '1.3', m: 19456, t: 2, p: 1 },
  previous_wrapped_kex_priv: 'prev-kex',
  previous_wrapped_sig_priv: 'prev-sig',
};

const unsupported = () => { const err = new Error('unknown aead'); err.name = 'UnsupportedAlgorithmError'; return err; };

test('an envelope this build cannot read asks for a reload, not for the password again', async () => {
  const h = harness({ openKeys: async () => { throw unsupported(); } });
  ready(h.component);
  await h.component.submit();
  assert.equal(h.component.error, 'This vault was saved by a newer version of the app. Reload the page to update it.');
  assert.deepStrictEqual(h.posts, []);
});

test('a derivation that broke is not blamed on the password', async () => {
  const h = harness({ deriveUnwrapKey: () => { throw new Error('kdf params out of bounds'); } });
  ready(h.component);
  await h.component.submit();
  assert.equal(h.component.error, 'The password could not be changed. Try again.');
  assert.deepStrictEqual(h.posts, []);
});

test('the older password still changes it, against the current envelope it replaces', async () => {
  const h = harness({
    envelope: WITH_PREVIOUS,
    openWithFallback: async () => ({
      opened: 'previous', unwrapKey: Uint8Array.of(7), keys: { kexPriv: Uint8Array.of(1), sigSeed: Uint8Array.of(2) },
    }),
  });
  ready(h.component);
  await h.component.submit();
  assert.equal(h.component.error, '');
  assert.deepStrictEqual(h.order, [['rotate']], 'the current envelope the previous one stood in for was confirmed');
  assert.equal(h.posts[0].expected_wrapped_kex_priv, 'old-kex');
  assert.equal(h.session.locks, 1);
});

test('a current envelope the dialog opened is confirmed before the rotation', async () => {
  const h = harness({ envelope: WITH_PREVIOUS });
  ready(h.component);
  await h.component.submit();
  assert.deepStrictEqual(h.order, [['confirm', 'old-kex'], ['rotate']]);
});

test('without a previous envelope there is nothing to confirm', async () => {
  const h = harness();
  ready(h.component);
  await h.component.submit();
  assert.deepStrictEqual(h.order, [['rotate']]);
});

test('a refused confirm still lets the rotation through', async () => {
  const h = harness({
    envelope: WITH_PREVIOUS,
    confirmEnvelope: async () => { const err = new Error('throttled'); err.status = 429; throw err; },
  });
  ready(h.component);
  await h.component.submit();
  assert.equal(h.posts.length, 1);
  assert.equal(h.session.locks, 1);
});

test('the envelope is read only once the unlock has written it back', async () => {
  let release;
  const writes = new Promise((resolve) => { release = resolve; });
  let fetched = 0;
  const h = harness({ envelopeWrites: () => writes });
  h.ctx.vaultApi.fetchEnvelope = async () => { fetched += 1; return ENVELOPE; };
  ready(h.component);
  const pending = h.component.submit();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(fetched, 0, 'the envelope was read under the unlock\'s writes');
  release();
  await pending;
  assert.equal(fetched, 1);
  assert.equal(h.posts.length, 1);
});
