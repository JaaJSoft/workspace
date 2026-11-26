const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const ENVELOPE = {
  uuid: 'acct', kdf_salt: 'c2FsdA', kdf_algo: 'argon2id',
  kdf_params: { v: '1.3', m: 19456, t: 2, p: 1 },
  wrapped_kex_priv: 'old-kex', wrapped_sig_priv: 'old-sig',
};
const WITH_PREVIOUS = {
  ...ENVELOPE,
  previous_kdf_algo: 'argon2id',
  previous_kdf_params: { v: '1.3', m: 19456, t: 2, p: 1 },
  previous_wrapped_kex_priv: 'prev-kex',
  previous_wrapped_sig_priv: 'prev-sig',
};
const CURRENT = { algo: 'argon2id', params: { v: '1.3', m: 65536, t: 3, p: 2 } };
const OLD_KEY = 'A'.repeat(53);
const NEW_KEY = 'NEWKEY0123';
const NEW_KEY_GROUPED = 'NEWK-EY01-23';

// The envelope helper and the crypto are stubbed - their own behaviour is
// pinned by account_envelope.test.js and the vector suites - and the rotation
// sequence runs for real, as in vault_password.test.js. What is under test is
// the dialog around it: what it shows, and what it sends when.
function harness(overrides = {}) {
  const posts = [];
  const derived = [];
  const decoded = [];
  const buffers = [];
  const order = [];
  const windowEvents = [];
  const kits = [];
  const session = { locks: 0, remembered: overrides.remembered || null, envelope: overrides.envelope || ENVELOPE };
  const fresh = (n) => { const b = Uint8Array.from({ length: 4 }, () => n); buffers.push(b); return b; };
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/envelope_rotation.js',
      'workspace/vault/ui/static/vault/ui/js/emergency_kit.js',
      'workspace/vault/ui/static/vault/ui/js/vault_recovery_key.js',
    ],
    {
      vaultCrypto: {
        CURRENT_SUITE: { kdf: CURRENT },
        randomBytes: () => fresh(5),
        crockfordEncode: () => NEW_KEY,
        crockfordDecode: overrides.crockfordDecode || ((text) => { decoded.push(text); return fresh(9); }),
        fromBase64Url: () => new Uint8Array(32),
      },
      vaultEnvelope: {
        deriveUnwrapKey: overrides.deriveUnwrapKey
          ? async (fields, password, secret) => { derived.push([fields.kdf_params, password, secret]); return overrides.deriveUnwrapKey(fresh); }
          : async (fields, password, secret) => { derived.push([fields.kdf_params, password, secret]); return fresh(7); },
        openKeys: overrides.openKeys || (async () => ({ kexPriv: fresh(1), sigSeed: fresh(2) })),
        // The contract of the real helper, pinned by account_envelope.test.js.
        openWithFallback: overrides.openWithFallback || async function (envelope, password, secret) {
          const unwrapKey = await this.deriveUnwrapKey(envelope, password, secret);
          try {
            return { opened: 'current', unwrapKey, keys: await this.openKeys(unwrapKey, envelope) };
          } catch (err) {
            if (err && err.name === 'UnsupportedAlgorithmError') throw err;
            const wrong = new Error('wrong'); wrong.name = 'VaultEnvelopeWrongPassword'; throw wrong;
          }
        },
        sealAndReread: overrides.sealAndReread || (async () => ({ wrapped_kex_priv: 'new-kex', wrapped_sig_priv: 'new-sig' })),
        rotateBody: (sealed, expected) => ({ ...sealed, expected_wrapped_kex_priv: expected }),
        previousOf: (envelope) => (envelope.previous_wrapped_kex_priv ? { wrapped_kex_priv: envelope.previous_wrapped_kex_priv } : null),
      },
      vaultApi: {
        fetchEnvelope: overrides.fetchEnvelope || (async () => {
          if (session.offline) throw new TypeError('network error');
          return session.envelope;
        }),
        confirmEnvelope: overrides.confirmEnvelope || (async (expected) => { order.push(['confirm', expected]); }),
        rotateEnvelope: overrides.rotateEnvelope || (async (body) => {
          order.push(['rotate']);
          posts.push(body);
          return null;
        }),
      },
      vaultSession: {
        rememberedSecret: () => session.remembered,
        rememberDevice: (text) => { order.push(['remember', text]); session.remembered = text; },
        forgetDevice: () => { order.push(['forget']); session.remembered = null; },
        envelopeWrites: overrides.envelopeWrites || (() => null),
        lock: () => { order.push(['lock']); session.locks += 1; },
      },
      // Already on the page, so the loader resolves without a script tag.
      vaultOnboardingTools: overrides.vaultOnboardingTools === undefined
        ? { buildEmergencyKitPdf: (kit) => { kits.push(kit); return { pdf: true }; } }
        : overrides.vaultOnboardingTools,
      document: overrides.document,
      downloadBlob: (blob, filename) => { kits.push({ blob, filename }); },
      userTzDayKey: () => '2026-11-26',
      location: { origin: 'https://workspace.example' },
      CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
      dispatchEvent: (event) => {
        assert.equal(event.type, 'vault-recovery-key-outcome');
        order.push(['told', event.detail.outcome]);
        windowEvents.push([event.detail.outcome, event.detail.sealed]);
      },
    }
  );
  const component = ctx.vaultRecoveryKeyChange();
  const events = [];
  component.$dispatch = (name) => events.push(name);
  component.$root = { dataset: { email: 'owner@example.com', kitBundle: '/static/kit.js' } };
  component.init();
  return { ctx, component, posts, derived, decoded, buffers, session, events, windowEvents, order, kits };
}

function typed(component, overrides = {}) {
  Object.assign(component, { currentPassword: 'the password', secretText: OLD_KEY, ...overrides });
}

async function shown(overrides = {}) {
  const h = harness(overrides);
  typed(h.component, h.session.remembered ? { secretText: h.component.secretText } : {});
  await h.component.prepare();
  assert.equal(h.component.step, 'kit', h.component.error);
  return h;
}

const gone = (status) => { const err = new Error('refused'); err.status = status; return err; };
const tick = () => new Promise((resolve) => setImmediate(resolve));

// --- the first step: nothing leaves, and a key appears only if the account opened

test('without a password the first step stays closed', async () => {
  const h = harness();
  typed(h.component, { currentPassword: '' });
  assert.equal(h.component.canPrepare(), false);
  await h.component.prepare();
  assert.deepStrictEqual(h.derived, []);
  assert.equal(h.component.step, 'credentials');
});

test('a wrong password or key draws no new key and sends nothing', async () => {
  const h = harness({ openKeys: async () => { throw new Error('tag mismatch'); } });
  typed(h.component);
  await h.component.prepare();
  assert.equal(h.component.error, 'Master password or recovery key is incorrect.');
  assert.equal(h.component.step, 'credentials');
  assert.equal(h.component.nextSecretText, '');
  assert.deepStrictEqual(h.events, []);
  assert.deepStrictEqual(h.order, []);
});

test('with a remembered key, a failed open can only blame the password', async () => {
  const h = harness({ remembered: OLD_KEY, openKeys: async () => { throw new Error('tag mismatch'); } });
  typed(h.component, { secretText: h.component.secretText });
  await h.component.prepare();
  assert.equal(h.component.error, 'Master password is incorrect.');
});

test('a current key that does not decode says so and asks for it again', async () => {
  const h = harness({ remembered: OLD_KEY, crockfordDecode: () => { throw new Error('bad symbol'); } });
  typed(h.component, { secretText: h.component.secretText });
  await h.component.prepare();
  assert.match(h.component.error, /could not be read/);
  assert.deepStrictEqual(h.derived, []);
  assert.equal(h.component.secretRemembered, false);
  assert.equal(h.component.secretText, '');
});

test('an envelope this build cannot read asks for a reload, not for the password again', async () => {
  const unsupported = new Error('unknown aead'); unsupported.name = 'UnsupportedAlgorithmError';
  const h = harness({ openKeys: async () => { throw unsupported; } });
  typed(h.component);
  await h.component.prepare();
  assert.equal(h.component.error, 'This vault was saved by a newer version of the app. Reload the page to update it.');
  assert.equal(h.component.step, 'credentials');
});

test('a reseal that does not reread shows no key', async () => {
  const h = harness({
    sealAndReread: async () => { const e = new Error('x'); e.name = 'VaultEnvelopeMismatch'; throw e; },
  });
  typed(h.component);
  await h.component.prepare();
  assert.equal(h.component.error, 'The new recovery key could not be applied.');
  assert.equal(h.component.step, 'credentials');
  assert.equal(h.component.nextSecretText, '');
});

test('the new key is shown before anything is sent', async () => {
  const h = await shown();
  assert.equal(h.component.groupedSecret(), NEW_KEY_GROUPED);
  assert.deepStrictEqual(h.events, ['vault-recovery-key-shown']);
  assert.deepStrictEqual(h.order, [], 'a request or a storage write left before the key was saved');
  assert.equal(h.session.locks, 0);
});

test('the same password seals under the new key, spelled as the kit prints it', async () => {
  const h = await shown();
  assert.deepStrictEqual(h.decoded, [OLD_KEY, NEW_KEY_GROUPED]);
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(h.derived.map(([params, password]) => [params, password]))),
    [[ENVELOPE.kdf_params, 'the password'], [CURRENT.params, 'the password']]
  );
  // Two different secrets went into the two derivations.
  assert.notStrictEqual(h.derived[0][2], h.derived[1][2]);
});

test('the password and every raw buffer are gone once the key is on screen', async () => {
  const h = await shown();
  assert.equal(h.component.currentPassword, '');
  assert.equal(h.component.secretText, '');
  assert.ok(h.buffers.length >= 6);
  for (const buffer of h.buffers) assert.deepStrictEqual(Array.from(buffer), [0, 0, 0, 0]);
});

test('the raw buffers are zeroed on a failure too', async () => {
  const h = harness({ sealAndReread: async () => { throw new Error('x'); } });
  typed(h.component);
  await h.component.prepare();
  for (const buffer of h.buffers) assert.deepStrictEqual(Array.from(buffer), [0, 0, 0, 0]);
});

test('the envelope is read only once the unlock has written it back', async () => {
  let release;
  const writes = new Promise((resolve) => { release = resolve; });
  let fetched = 0;
  const h = harness({ envelopeWrites: () => writes, fetchEnvelope: async () => { fetched += 1; return ENVELOPE; } });
  typed(h.component);
  const pending = h.component.prepare();
  await tick();
  assert.equal(fetched, 0, 'the envelope was read under the unlock\'s writes');
  release();
  await pending;
  assert.equal(fetched, 1);
});

test('a dialog that went away during the derivation shows nothing', async () => {
  let release;
  let calls = 0;
  const h = harness({
    deriveUnwrapKey: (fresh) => {
      calls += 1;
      if (calls > 1) return fresh(7);
      return new Promise((resolve) => { release = () => resolve(fresh(7)); });
    },
  });
  typed(h.component);
  const pending = h.component.prepare();
  await tick();
  h.component.destroy();
  release();
  await pending;
  assert.deepStrictEqual(h.events, []);
  assert.equal(h.component.nextSecretText, '');
  for (const [, password] of h.derived) assert.notEqual(password, '');
  for (const buffer of h.buffers) assert.deepStrictEqual(Array.from(buffer), [0, 0, 0, 0]);
});

// --- the kit

test('the kit carries the new key and the account it belongs to', async () => {
  const h = await shown();
  await h.component.downloadKit();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(h.kits[0])), {
    email: 'owner@example.com',
    serverUrl: 'https://workspace.example',
    secretText: NEW_KEY_GROUPED,
    createdAt: '2026-11-26',
  });
  assert.equal(h.kits[1].filename, 'vault-emergency-kit.pdf');
  assert.deepStrictEqual(h.order, [], 'saving the kit sent something');
});

test('a kit that cannot be built says to write the key down', async () => {
  // No bundle on the page and a script tag that fails to load.
  const h = harness({
    vaultOnboardingTools: null,
    document: {
      createElement: () => ({}),
      head: { appendChild: (script) => { setImmediate(() => script.onerror()); } },
    },
  });
  typed(h.component);
  await h.component.prepare();
  await h.component.downloadKit();
  assert.equal(h.component.error, 'The emergency kit could not be produced. Write the key down instead, or try again.');
  assert.equal(h.component.step, 'kit', 'the key left the screen with the failure');
});

// --- the second step: the rotation, and only once the key is acknowledged

test('nothing is sent until the key is acknowledged as saved', async () => {
  const h = await shown();
  assert.equal(h.component.canReplace(), false);
  await h.component.replace();
  assert.deepStrictEqual(h.order, []);
  h.component.acknowledged = true;
  assert.equal(h.component.canReplace(), true);
});

test('a success rotates against the envelope read, hands the device the new key, tells the page, then locks', async () => {
  const h = await shown({ remembered: OLD_KEY });
  assert.equal(h.component.remember, true, 'a device that held the old key did not default to holding the new one');
  h.component.acknowledged = true;
  await h.component.replace();
  assert.equal(h.posts.length, 1);
  assert.equal(h.posts[0].expected_wrapped_kex_priv, 'old-kex');
  assert.equal(h.posts[0].wrapped_kex_priv, 'new-kex');
  assert.deepStrictEqual(h.order, [
    ['rotate'],
    ['remember', NEW_KEY_GROUPED],
    ['told', 'landed'],
    ['lock'],
  ]);
});

test('a device that is not to remember the key forgets the old one', async () => {
  const h = await shown({ remembered: OLD_KEY });
  h.component.acknowledged = true;
  h.component.remember = false;
  await h.component.replace();
  assert.deepStrictEqual(h.order, [['rotate'], ['forget'], ['told', 'landed'], ['lock']]);
  assert.equal(h.session.remembered, null);
});

test('a device that did not remember the old key is not handed the new one unasked', async () => {
  const h = await shown();
  assert.equal(h.component.remember, false);
});

test('a current envelope the dialog opened is confirmed before the rotation', async () => {
  const h = await shown({ envelope: WITH_PREVIOUS });
  h.component.acknowledged = true;
  await h.component.replace();
  assert.deepStrictEqual(h.order.slice(0, 2), [['confirm', 'old-kex'], ['rotate']]);
});

test('an account the previous envelope opened is rotated without a confirm', async () => {
  const h = await shown({
    envelope: WITH_PREVIOUS,
    openWithFallback: async () => ({
      opened: 'previous', unwrapKey: Uint8Array.of(7), keys: { kexPriv: Uint8Array.of(1), sigSeed: Uint8Array.of(2) },
    }),
  });
  h.component.acknowledged = true;
  await h.component.replace();
  assert.deepStrictEqual(h.order[0], ['rotate']);
  assert.equal(h.posts[0].expected_wrapped_kex_priv, 'old-kex');
});

test('a refused confirm stops before the rotation and keeps the key on screen', async () => {
  // Throttled or failed, the confirm is reported as the rotation would be.
  for (const [status, wording] of [[429, /Too many changes to this account/], [503, /could not be replaced\. Try again\./]]) {
    const h = await shown({ envelope: WITH_PREVIOUS, confirmEnvelope: async () => { throw gone(status); } });
    h.component.acknowledged = true;
    await h.component.replace();
    assert.deepStrictEqual(h.posts, [], `status ${status}`);
    assert.equal(h.session.locks, 0);
    assert.match(h.component.error, wording);
    assert.equal(h.component.step, 'kit');
  }
});

test('a 409 says the old key still works, withdraws the new one and keeps the session open', async () => {
  const h = await shown({ remembered: OLD_KEY, rotateEnvelope: async () => { throw gone(409); } });
  h.component.acknowledged = true;
  await h.component.replace();
  assert.match(h.component.error, /this attempt replaced nothing and left your recovery key as it was/);
  // The conflict is evidence that something changed elsewhere, and that may
  // be the key itself: the message must not vouch for the old one.
  assert.doesNotMatch(h.component.error, /still works/);
  assert.equal(h.component.step, 'credentials');
  assert.equal(h.component.nextSecretText, '');
  assert.equal(h.component.canReplace(), false);
  assert.equal(h.session.remembered, OLD_KEY);
  assert.equal(h.session.locks, 0);
  assert.deepStrictEqual(h.windowEvents, [['absent', 'new-kex']]);
});

test('a throttled rotation keeps the key on screen for a later attempt', async () => {
  let refuse = true;
  const h = await shown({
    rotateEnvelope: async (body) => { if (refuse) throw gone(429); h.posts.push(body); return null; },
  });
  h.component.acknowledged = true;
  await h.component.replace();
  assert.match(h.component.error, /Too many changes to this account/);
  assert.equal(h.component.step, 'kit');
  assert.equal(h.component.groupedSecret(), NEW_KEY_GROUPED);
  refuse = false;
  await h.component.replace();
  assert.equal(h.posts.length, 1);
  assert.equal(h.session.locks, 1);
});

test('a rotation whose answer was lost, and landed, is a replacement', async () => {
  for (const status of [undefined, 409]) {
    const h = await shown({ remembered: OLD_KEY, rotateEnvelope: async () => { throw gone(status); } });
    // What the server holds once the lost request has been applied.
    h.session.envelope = { ...ENVELOPE, wrapped_kex_priv: 'new-kex' };
    h.component.acknowledged = true;
    await h.component.replace();
    assert.equal(h.component.error, '', `status ${status}`);
    assert.deepStrictEqual(h.order, [
      ['remember', NEW_KEY_GROUPED],
      ['told', 'landed'],
      ['lock'],
    ]);
  }
});

test('a failed rotation the server never saw replaces nothing', async () => {
  const h = await shown({ remembered: OLD_KEY, rotateEnvelope: async () => { throw new Error('down'); } });
  h.component.acknowledged = true;
  await h.component.replace();
  assert.equal(h.component.error, 'Your recovery key could not be replaced. Try again.');
  assert.equal(h.component.step, 'kit');
  assert.equal(h.session.remembered, OLD_KEY);
  assert.deepStrictEqual(h.order, [['told', 'absent']]);
});

test('a rotation nobody answered, with the server out of reach, is not called a failure', async () => {
  // The request died on the wire and so does the question that follows it:
  // the key on screen may be the one in place, or may open nothing.
  const h = await shown({ remembered: OLD_KEY, rotateEnvelope: async () => { throw new TypeError('network error'); } });
  h.session.offline = true;
  h.component.acknowledged = true;
  await h.component.replace();
  assert.match(h.component.error, /could not check whether your recovery key was replaced/);
  assert.doesNotMatch(h.component.error, /could not be replaced|opens nothing/);
  assert.equal(h.component.step, 'kit');
  assert.equal(h.component.groupedSecret(), NEW_KEY_GROUPED);
  assert.equal(h.component.canReplace(), true, 'the key cannot be tried again');
  assert.equal(h.session.remembered, OLD_KEY);
  assert.equal(h.session.locks, 0);
  assert.deepStrictEqual(h.windowEvents, [['unknown', 'new-kex']]);
});

test('trying again settles a rotation nobody answered, whichever way it went', async () => {
  for (const landed of [true, false]) {
    let calls = 0;
    const h = await shown({
      remembered: OLD_KEY,
      rotateEnvelope: async (body) => {
        calls += 1;
        if (calls === 1) throw new TypeError('network error');
        // The second request finds what the first left: a conflict when it
        // had landed, an envelope still to replace when it had not.
        if (landed) throw gone(409);
        h.posts.push(body);
        return null;
      },
    });
    h.session.offline = true;
    h.component.acknowledged = true;
    await h.component.replace();
    h.session.offline = false;
    if (landed) h.session.envelope = { ...ENVELOPE, wrapped_kex_priv: 'new-kex' };
    await h.component.replace();
    assert.equal(h.component.error, '', `landed ${landed}`);
    assert.equal(h.session.remembered, NEW_KEY_GROUPED);
    assert.equal(h.session.locks, 1);
    assert.deepStrictEqual(h.windowEvents.map(([outcome]) => outcome), ['unknown', 'landed']);
  }
});

test('an unanswered rotation is reported to the page even after a lock took the dialog', async () => {
  // The lock could only assume the key was never sent. Left at that, the page
  // would tell its owner to throw away a key that may open the account.
  let release;
  const h = await shown({
    remembered: OLD_KEY,
    rotateEnvelope: () => new Promise((resolve, reject) => { release = () => reject(new TypeError('network error')); }),
  });
  h.session.offline = true;
  h.component.acknowledged = true;
  const pending = h.component.replace();
  await tick();
  h.component.destroy();
  release();
  await pending;
  assert.deepStrictEqual(h.windowEvents, [['unknown', 'new-kex']]);
  assert.equal(h.session.remembered, OLD_KEY);
});

test('a rotation that lands after a lock took the dialog still reaches the device and the page', async () => {
  // The account opens with the new key from that moment on, dialog or not: a
  // device left holding the old one would go on unlocking with it, and the
  // page would go on saying nothing was replaced.
  let release;
  const h = await shown({
    remembered: OLD_KEY,
    rotateEnvelope: () => new Promise((resolve) => { release = () => resolve(null); }),
  });
  h.component.acknowledged = true;
  const pending = h.component.replace();
  await tick();
  h.component.destroy();
  release();
  await pending;
  assert.equal(h.session.remembered, NEW_KEY_GROUPED);
  assert.deepStrictEqual(h.windowEvents, [['landed', 'new-kex']]);
});

test('a rotation that fails after a lock took the dialog touches nothing', async () => {
  let release;
  const h = await shown({
    remembered: OLD_KEY,
    rotateEnvelope: () => new Promise((resolve, reject) => { release = () => reject(gone(409)); }),
  });
  h.component.acknowledged = true;
  const pending = h.component.replace();
  await tick();
  h.component.destroy();
  release();
  await pending;
  assert.equal(h.session.remembered, OLD_KEY);
  // The lock that took the dialog assumed nothing was sent; the page is told
  // what the server said, so its notice is about that.
  assert.deepStrictEqual(h.windowEvents, [['absent', 'new-kex']]);
  assert.equal(h.session.locks, 0);
});

test('closing the dialog lets go of the password and of both keys', async () => {
  const h = await shown();
  h.component.currentPassword = 'typed again';
  h.component.destroy();
  assert.equal(h.component.currentPassword, '');
  assert.equal(h.component.secretText, '');
  assert.equal(h.component.nextSecretText, '');
  assert.equal(h.component.pending, null);
});

// --- the page around the dialog

function root(h, { openedWithPrevious = false } = {}) {
  const mixin = h.ctx.vaultRecoveryKeyDialogMixin();
  const reads = [];
  mixin.readRememberedSecret = () => reads.push('read');
  h.ctx.vaultSession.openedWithPrevious = () => openedWithPrevious;
  return { mixin, reads };
}

test('closing before any key was shown leaves no notice', () => {
  const { mixin } = root(harness());
  mixin.openRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyDialogOpen, true);
  mixin.closeRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyDialogOpen, false);
  assert.equal(mixin.recoveryKeyNotice, '');
  assert.equal(mixin.recoveryKeyNoticeText(), '');
});

test('closing once a key was shown says that key opens nothing', () => {
  const { mixin } = root(harness());
  mixin.openRecoveryKeyDialog();
  mixin.recoveryKeyWasShown();
  mixin.closeRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyNotice, 'discarded');
  assert.match(mixin.recoveryKeyNoticeText(), /was not replaced/);
  // A second close - the lock that follows a cancel - changes nothing, and
  // opening the dialog again clears the notice.
  mixin.closeRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyNotice, 'discarded');
  mixin.openRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyNotice, '');
});

test('a replacement closes the dialog, says so, and has the gate read the device again', () => {
  const { mixin, reads } = root(harness());
  mixin.openRecoveryKeyDialog();
  mixin.recoveryKeyWasShown();
  mixin.recoveryKeyOutcome('landed', 'new-kex');
  assert.equal(mixin.recoveryKeyDialogOpen, false);
  assert.equal(mixin.recoveryKeyNotice, 'replaced');
  assert.deepStrictEqual(reads, ['read']);
  // The lock that follows closes the dialog again: the key was sent, so the
  // notice must not turn into the one for a key that was not.
  mixin.closeRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyNotice, 'replaced');
});

test('a replacement heard after a lock took the dialog overrides the notice that lock left', () => {
  const { mixin } = root(harness());
  mixin.openRecoveryKeyDialog();
  mixin.recoveryKeyWasShown();
  mixin.closeRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyNotice, 'discarded');
  mixin.recoveryKeyOutcome('landed', 'new-kex');
  assert.equal(mixin.recoveryKeyNotice, 'replaced');
});

test('closing on a rotation nobody answered does not say the key opens nothing', () => {
  const { mixin } = root(harness());
  mixin.openRecoveryKeyDialog();
  mixin.recoveryKeyWasShown();
  mixin.recoveryKeyOutcome('unknown', 'new-kex');
  // The dialog is still up and says it itself.
  assert.equal(mixin.recoveryKeyNotice, '');
  mixin.closeRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyNotice, 'unsettled');
  assert.match(mixin.recoveryKeyNoticeText(), /could not check/);
  assert.doesNotMatch(mixin.recoveryKeyNoticeText(), /opens nothing|not replaced/);
});

test('an unanswered rotation heard after a lock replaces the notice that lock left', () => {
  const { mixin } = root(harness());
  mixin.openRecoveryKeyDialog();
  mixin.recoveryKeyWasShown();
  mixin.closeRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyNotice, 'discarded');
  mixin.recoveryKeyOutcome('unknown', 'new-kex');
  assert.equal(mixin.recoveryKeyNotice, 'unsettled');
});

test('a later attempt the server refused settles the earlier one it could not answer for', () => {
  const { mixin } = root(harness());
  mixin.openRecoveryKeyDialog();
  mixin.recoveryKeyWasShown();
  mixin.recoveryKeyOutcome('unknown', 'new-kex');
  mixin.recoveryKeyOutcome('absent', 'new-kex');
  mixin.closeRecoveryKeyDialog();
  assert.equal(mixin.recoveryKeyNotice, 'discarded');
});

// The next unlock is the first moment the server is known to be in reach.
async function unlockedAfterAnUnansweredRotation(options) {
  const h = harness({ remembered: OLD_KEY });
  h.session.envelope = options.envelope;
  h.session.offline = !!options.offline;
  const { mixin, reads } = root(h, options);
  mixin.openRecoveryKeyDialog();
  mixin.recoveryKeyWasShown();
  mixin.recoveryKeyOutcome('unknown', 'new-kex');
  mixin.closeRecoveryKeyDialog();
  mixin.recoveryKeyUnlocked();
  await tick();
  return { h, mixin, reads };
}

test('an unlock finds that the unanswered rotation had landed, and stops the device offering the old key', async () => {
  const { h, mixin, reads } = await unlockedAfterAnUnansweredRotation({
    envelope: { ...ENVELOPE, wrapped_kex_priv: 'new-kex' },
    openedWithPrevious: true,
  });
  assert.equal(mixin.recoveryKeyNotice, 'landed');
  assert.match(mixin.recoveryKeyNoticeText(), /was replaced after all/);
  assert.equal(h.session.remembered, null, 'the device still offers the key that was replaced');
  assert.deepStrictEqual(reads, ['read']);
  assert.equal(mixin.recoveryKeyUnsettled, null);
});

test('an unlock the new key itself opened leaves the device as that unlock set it', async () => {
  // Typed from the kit and remembered by that unlock: forgetting it here
  // would undo what the user just asked for.
  const { h, mixin } = await unlockedAfterAnUnansweredRotation({
    envelope: { ...ENVELOPE, wrapped_kex_priv: 'new-kex' },
    openedWithPrevious: false,
  });
  assert.equal(mixin.recoveryKeyNotice, 'landed');
  assert.equal(h.session.remembered, OLD_KEY);
  assert.deepStrictEqual(h.order.filter(([name]) => name === 'forget'), []);
});

test('an unlock finds that the unanswered rotation never landed', async () => {
  const { h, mixin } = await unlockedAfterAnUnansweredRotation({ envelope: ENVELOPE });
  assert.equal(mixin.recoveryKeyNotice, 'discarded');
  assert.equal(h.session.remembered, OLD_KEY);
  assert.equal(mixin.recoveryKeyUnsettled, null);
});

test('an unlock that still cannot ask keeps the question open', async () => {
  const { mixin } = await unlockedAfterAnUnansweredRotation({ envelope: ENVELOPE, offline: true });
  assert.equal(mixin.recoveryKeyNotice, 'unsettled');
  assert.equal(mixin.recoveryKeyUnsettled, 'new-kex');
});

test('an unlock with nothing left open only clears the notices', async () => {
  const h = harness();
  let fetched = 0;
  h.ctx.vaultApi.fetchEnvelope = async () => { fetched += 1; return ENVELOPE; };
  const { mixin } = root(h);
  mixin.openRecoveryKeyDialog();
  mixin.recoveryKeyWasShown();
  mixin.closeRecoveryKeyDialog();
  mixin.recoveryKeyUnlocked();
  await tick();
  assert.equal(mixin.recoveryKeyNotice, '');
  assert.equal(fetched, 0, 'every unlock would cost a read of the envelope');
});
