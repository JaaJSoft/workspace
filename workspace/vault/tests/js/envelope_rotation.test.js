// The sequence a password change and a recovery key replacement share, on its
// own: what it reads, in which order, what it hands back and what it never
// leaves behind. The dialogs' suites run it too, each around its own screen.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const ENVELOPE = {
  uuid: 'acct', kdf_salt: 'c2FsdA', kdf_algo: 'argon2id',
  kdf_params: { v: '1.3', m: 19456, t: 2, p: 1 },
  wrapped_kex_priv: 'old-kex', wrapped_sig_priv: 'old-sig',
};
const WITH_PREVIOUS = { ...ENVELOPE, previous_wrapped_kex_priv: 'prev-kex' };
const CURRENT = { algo: 'argon2id', params: { v: '1.3', m: 65536, t: 3, p: 2 } };
const OLD = { password: 'old password', secretText: 'OLD-KEY' };

function harness(overrides = {}) {
  const calls = [];
  const buffers = [];
  const fresh = (n) => { const b = Uint8Array.from({ length: 4 }, () => n); buffers.push(b); return b; };
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/envelope_rotation.js', {
    vaultCrypto: {
      CURRENT_SUITE: { kdf: CURRENT },
      crockfordDecode: overrides.crockfordDecode || ((text) => { calls.push(['decode', text]); return fresh(9); }),
      fromBase64Url: () => new Uint8Array(32),
    },
    vaultEnvelope: {
      openWithFallback: overrides.openWithFallback || (async (envelope, password, secret) => {
        calls.push(['open', password, secret]);
        return { opened: 'current', unwrapKey: fresh(7), keys: { kexPriv: fresh(1), sigSeed: fresh(2) } };
      }),
      deriveUnwrapKey: overrides.deriveUnwrapKey || (async (fields, password, secret) => {
        calls.push(['derive', fields.kdf_params, password, secret]);
        return fresh(8);
      }),
      sealAndReread: overrides.sealAndReread || (async (key, keys, uuid) => {
        calls.push(['seal', uuid]);
        return { wrapped_kex_priv: 'new-kex', wrapped_sig_priv: 'new-sig' };
      }),
      rotateBody: (sealed, expected) => ({ ...sealed, expected_wrapped_kex_priv: expected }),
      previousOf: (envelope) => (envelope.previous_wrapped_kex_priv ? {} : null),
    },
    vaultApi: {
      fetchEnvelope: overrides.fetchEnvelope || (async () => { calls.push(['fetch']); return overrides.envelope || ENVELOPE; }),
      confirmEnvelope: overrides.confirmEnvelope || (async (expected) => { calls.push(['confirm', expected]); }),
      rotateEnvelope: overrides.rotateEnvelope || (async (body) => { calls.push(['rotate', body]); return null; }),
    },
    vaultSession: {
      envelopeWrites: overrides.envelopeWrites || (async () => { calls.push(['writes']); }),
    },
  });
  return { R: ctx.vaultEnvelopeRotation, calls, buffers };
}

const never = () => false;
const names = (calls) => calls.map((call) => call[0]);
const refused = (status) => { const err = new Error('refused'); err.status = status; return err; };

// --- prepare ----------------------------------------------------------------

test('the envelope is read once the unlock has written it back, then opened, derived and sealed', async () => {
  const h = harness();
  await h.R.prepare(OLD, { password: 'new password', secretText: OLD.secretText }, never);
  assert.deepStrictEqual(names(h.calls), ['writes', 'fetch', 'decode', 'open', 'derive', 'seal']);
});

test('a new password is sealed under the same key, decoded once', async () => {
  const h = harness();
  await h.R.prepare(OLD, { password: 'new password', secretText: OLD.secretText }, never);
  const open = h.calls.find((call) => call[0] === 'open');
  const derive = h.calls.find((call) => call[0] === 'derive');
  assert.equal(open[1], 'old password');
  assert.equal(derive[2], 'new password');
  assert.strictEqual(derive[3], open[2], 'the one key was decoded into two buffers');
  assert.equal(h.calls.filter((call) => call[0] === 'decode').length, 1);
});

test('a new key is decoded from its text and sealed under the same password', async () => {
  const h = harness();
  await h.R.prepare(OLD, { password: OLD.password, secretText: 'NEW-KEY' }, never);
  assert.deepStrictEqual(
    h.calls.filter((call) => call[0] === 'decode').map((call) => call[1]),
    ['OLD-KEY', 'NEW-KEY']
  );
  const open = h.calls.find((call) => call[0] === 'open');
  const derive = h.calls.find((call) => call[0] === 'derive');
  assert.equal(derive[2], 'old password');
  assert.notStrictEqual(derive[3], open[2], 'the new envelope was derived from the old key');
});

test('the new envelope is always derived under the current suite', async () => {
  const h = harness();
  await h.R.prepare(OLD, { password: 'new password', secretText: OLD.secretText }, never);
  const derive = h.calls.find((call) => call[0] === 'derive');
  assert.deepStrictEqual({ ...derive[1] }, CURRENT.params);
});

test('what comes back is ciphertext and the envelope it replaces', async () => {
  const h = harness();
  const rotation = await h.R.prepare(OLD, { password: 'new password', secretText: OLD.secretText }, never);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(rotation)), {
    sealed: { wrapped_kex_priv: 'new-kex', wrapped_sig_priv: 'new-sig' },
    expected: 'old-kex',
    confirmFirst: false,
  });
});

test('a current envelope that opened is confirmed first only while a previous one waits', async () => {
  const opened = (which) => async () => ({
    opened: which, unwrapKey: Uint8Array.of(7), keys: { kexPriv: Uint8Array.of(1), sigSeed: Uint8Array.of(2) },
  });
  const sealing = { password: 'new password', secretText: OLD.secretText };
  for (const [envelope, which, expected] of [
    [ENVELOPE, 'current', false],
    [WITH_PREVIOUS, 'current', true],
    // Rewriting from the previous envelope must not confirm the current one:
    // nothing proved it.
    [WITH_PREVIOUS, 'previous', false],
  ]) {
    const h = harness({ envelope, openWithFallback: opened(which) });
    const rotation = await h.R.prepare(OLD, sealing, never);
    assert.equal(rotation.confirmFirst, expected, `${which}, previous ${!!envelope.previous_wrapped_kex_priv}`);
  }
});

test('every raw buffer is zeroed, on success and on each failure', async () => {
  const failing = (name) => async () => { const err = new Error(name); err.name = name; throw err; };
  for (const overrides of [
    {},
    { deriveUnwrapKey: failing('Error') },
    { sealAndReread: failing('VaultEnvelopeMismatch') },
  ]) {
    const h = harness(overrides);
    await h.R.prepare(OLD, { password: OLD.password, secretText: 'NEW-KEY' }, never).catch(() => {});
    assert.ok(h.buffers.length >= 5, `${h.buffers.length} buffers tracked`);
    for (const buffer of h.buffers) assert.deepStrictEqual(Array.from(buffer), [0, 0, 0, 0]);
  }
});

test('a key that does not decode is named, before anything is derived', async () => {
  const h = harness({ crockfordDecode: () => { throw new Error('bad symbol'); } });
  await assert.rejects(
    h.R.prepare(OLD, { password: 'new password', secretText: OLD.secretText }, never),
    (err) => err.name === 'VaultRecoveryKeyUnreadable'
  );
  assert.deepStrictEqual(names(h.calls), ['writes', 'fetch']);
});

test('a failed open passes through untouched, so the caller can tell a wrong password from an old build', async () => {
  for (const name of ['VaultEnvelopeWrongPassword', 'UnsupportedAlgorithmError']) {
    const thrown = new Error(name);
    thrown.name = name;
    const h = harness({ openWithFallback: async () => { throw thrown; } });
    await assert.rejects(
      h.R.prepare(OLD, { password: 'new password', secretText: OLD.secretText }, never),
      (err) => err === thrown
    );
  }
});

test('keys that do not seal, or do not read back, are one refusal', async () => {
  for (const name of ['VaultEnvelopeMismatch', 'OperationError']) {
    const cause = new Error(name);
    cause.name = name;
    const h = harness({ sealAndReread: async () => { throw cause; } });
    await assert.rejects(
      h.R.prepare(OLD, { password: 'new password', secretText: OLD.secretText }, never),
      (err) => err.name === 'VaultEnvelopeResealFailed' && err.cause === cause
    );
  }
});

test('a caller that went away is not served past the step it left at', async () => {
  // The whole sequence is five awaits long; each one is a place a lock can
  // land, and what follows it must not run with the keys still in hand.
  const steps = ['writes', 'fetch', 'open', 'derive'];
  for (let stop = 0; stop < steps.length; stop += 1) {
    const h = harness();
    const cancelled = () => h.calls.some((call) => call[0] === steps[stop]);
    const rotation = await h.R.prepare(OLD, { password: 'new password', secretText: OLD.secretText }, cancelled);
    assert.equal(rotation, null, `left after ${steps[stop]}`);
    const reached = names(h.calls).filter((name) => name !== 'decode');
    assert.deepStrictEqual(reached, steps.slice(0, stop + 1), `left after ${steps[stop]}`);
    for (const buffer of h.buffers) assert.deepStrictEqual(Array.from(buffer), [0, 0, 0, 0]);
  }
});

// --- commit -----------------------------------------------------------------

const ROTATION = {
  sealed: { wrapped_kex_priv: 'new-kex', wrapped_sig_priv: 'new-sig' },
  expected: 'old-kex',
  confirmFirst: false,
};

test('a rotation names the envelope it replaces', async () => {
  const h = harness();
  assert.equal(await h.R.commit(ROTATION, never), true);
  assert.deepStrictEqual(names(h.calls), ['rotate']);
  assert.deepStrictEqual({ ...h.calls[0][1] }, {
    wrapped_kex_priv: 'new-kex', wrapped_sig_priv: 'new-sig', expected_wrapped_kex_priv: 'old-kex',
  });
});

test('the confirm goes before the rotation', async () => {
  const h = harness();
  await h.R.commit({ ...ROTATION, confirmFirst: true }, never);
  assert.deepStrictEqual(names(h.calls), ['confirm', 'rotate']);
  assert.equal(h.calls[0][1], 'old-kex');
});

test('a refused confirm sends no rotation', async () => {
  const h = harness({ confirmEnvelope: async () => { throw refused(429); } });
  await assert.rejects(h.R.commit({ ...ROTATION, confirmFirst: true }, never), (err) => err.status === 429);
  assert.deepStrictEqual(h.calls, []);
});

test('a caller that went away during the confirm sends no rotation', async () => {
  const h = harness();
  const cancelled = () => h.calls.some((call) => call[0] === 'confirm');
  assert.equal(await h.R.commit({ ...ROTATION, confirmFirst: true }, cancelled), false);
  assert.deepStrictEqual(names(h.calls), ['confirm']);
});

test('a refused rotation rejects with its status', async () => {
  const h = harness({ rotateEnvelope: async () => { throw refused(409); } });
  await assert.rejects(h.R.commit(ROTATION, never), (err) => err.status === 409);
});

test('an error says whether the rotation itself had left', async () => {
  // A caller reporting what became of it has to tell a confirm that failed
  // with nothing sent from a rotation that may have been written.
  const afterConfirm = harness({ confirmEnvelope: async () => { throw refused(429); } });
  await assert.rejects(
    afterConfirm.R.commit({ ...ROTATION, confirmFirst: true }, never),
    (err) => err.rotationSent === undefined
  );
  const afterRotate = harness({ rotateEnvelope: async () => { throw new TypeError('network error'); } });
  await assert.rejects(afterRotate.R.commit(ROTATION, never), (err) => err.rotationSent === true);
});
