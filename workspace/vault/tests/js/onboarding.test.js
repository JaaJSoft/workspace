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
    // base.html loads ui/js/csrf.js on every page; the component reads it.
    getCSRFToken: () => 'test-csrf-token',
    fetch: async () => ({ ok: true, status: 201, json: async () => ({}) }),
    setTimeout: (fn) => fn(),
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

test('the secret is grouped so it can be copied by hand', () => {
  const app = component();
  app.secretText = 'ABCDEFGHIJ';
  assert.equal(app.groupedSecret(), 'ABCD-EFGH-IJ');
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

test('the password and the secret bytes are gone once the kit is shown', () => {
  // The norm wipes the vault password and the recovery secret from memory as
  // soon as the kit has been shown; only the text on screen survives.
  const app = component();
  app.password = 'correct horse battery staple';
  app.confirmation = app.password;
  app.secretBytes = new Uint8Array([1, 2, 3]);
  app.secretText = 'ABCD-EFGH';
  app.forgetSecrets();
  assert.equal(app.password, '');
  assert.equal(app.confirmation, '');
  assert.deepEqual(Array.from(app.secretBytes), [0, 0, 0]);
  assert.equal(app.secretText, 'ABCD-EFGH');
});

// --- sealing --------------------------------------------------------------
//
// The crypto is pinned by the vector suites; what these stubs stand in for is
// the shape of the calls, so the tests can exercise what the component does
// around them - which is where a lost response turns into a vault nobody can
// open.
const CRYPTO_STUB = {
  KDF_HKDF_SHA256: 1,
  ARGON2_PARAMS: {},
  PUBKEY_ALG_X25519: 1,
  PUBKEY_ALG_ED25519: 2,
  AD: {
    unwrapInfo: () => 'unwrap',
    kexPrivAd: () => 'kex',
    sigPrivAd: () => 'sig',
    kexPubPayload: () => 'payload',
  },
  randomBytes: (() => {
    let draw = 0;
    return (n) => new Uint8Array(n).fill(++draw);
  })(),
  crockfordEncode: (bytes) => 'SECRET' + bytes[0],
  fromBase64Url: () => new Uint8Array(16),
  toBase64Url: () => 'b64',
  deriveAmk: async () => new Uint8Array(32),
  hkdf: async () => new Uint8Array(32),
  seal: async () => new Uint8Array(8),
  signBytes: async () => new Uint8Array(64),
  encodePublicKey: () => new Uint8Array(33),
};

const SUBTLE_STUB = {
  generateKey: async () => ({ privateKey: {}, publicKey: {} }),
  exportKey: async () => new Uint8Array(48),
};

function sealing({ responses, ...extra } = {}) {
  const calls = [];
  const app = component({
    VaultCrypto: CRYPTO_STUB,
    crypto: { subtle: SUBTLE_STUB, getRandomValues: (a) => a },
    fetch: async (url) => {
      calls.push(url);
      const reply = responses[url];
      if (typeof reply === 'function') return reply(calls);
      return reply;
    },
    ...extra,
  });
  app.password = 'correct-horse-battery-staple';
  return { app, calls };
}

const INIT_OK = {
  ok: true,
  status: 201,
  json: async () => ({ account_uuid: 'uuid', kdf_salt: 'salt' }),
};

test('a sealed account reaches the kit step', async () => {
  const { app } = sealing({
    responses: {
      '/api/v1/vault/account/init': INIT_OK,
      '/api/v1/vault/account/finalize': { ok: true, status: 201 },
    },
  });
  await app.generateAndSeal();
  assert.equal(app.step, 3);
  assert.equal(app.error, '');
  assert.ok(app.secretText);
});

test('an init the server refuses does not reach the kit step', async () => {
  // Without the status check the body of a 409 parses as an empty object and
  // the flow carries on, deriving keys against an undefined salt.
  const { app, calls } = sealing({
    responses: {
      '/api/v1/vault/account/init': { ok: false, status: 500, json: async () => ({}) },
    },
  });
  await app.generateAndSeal();
  assert.equal(app.step, 1);
  assert.match(app.error, /could not be created/);
  // The point of the check: nothing derived from an undefined salt is offered
  // to the server.
  assert.deepEqual(calls, ['/api/v1/vault/account/init']);
});

test('a retry after a lost finalize shows the key rather than stranding it', async () => {
  // The worst failure this flow has. finalize commits, its reply is lost, and
  // the probe cannot reach the server either - so the user clicks again. init
  // now answers 409, because the identity it would create is already active:
  // sealed by this very page, with the secret only this page still holds.
  // Reading that as a refusal leaves an account nobody can ever open.
  let attempt = 0;
  let online = false;
  const { app } = sealing({
    responses: {
      '/api/v1/vault/account/init': () => {
        attempt += 1;
        return attempt === 1 ? INIT_OK : { ok: false, status: 409 };
      },
      '/api/v1/vault/account/finalize': () => {
        throw new TypeError('network error');
      },
      '/api/v1/vault/account/envelope': () => {
        if (!online) throw new TypeError('network error');
        return { ok: true, json: async () => ({ state: 'active', kex_public: 'b64' }) };
      },
    },
  });

  await app.generateAndSeal();
  assert.equal(app.step, 1, 'the first attempt cannot know yet');
  assert.match(app.error, /Nothing was saved/);
  const secret = app.secretText;

  online = true;
  await app.generateAndSeal();
  assert.equal(app.step, 3);
  assert.equal(app.secretText, secret, 'the key on screen must be the sealed one');
  assert.equal(app.error, '');
});

test('an envelope sealed by another tab never shows this page key', async () => {
  // Two tabs, one identity. The other one won: the key on the server unwraps
  // with its secret, not ours. Showing ours would have the user write down a
  // key that opens nothing.
  const { app } = sealing({
    responses: {
      '/api/v1/vault/account/init': INIT_OK,
      '/api/v1/vault/account/finalize': { ok: false, status: 409 },
      '/api/v1/vault/account/envelope': {
        ok: true,
        json: async () => ({ state: 'active', kex_public: 'a-key-from-another-tab' }),
      },
    },
  });
  await app.generateAndSeal();
  assert.equal(app.step, 1);
  assert.match(app.error, /somewhere else/);
});

test('an account already set up before this page started says so', async () => {
  const { app } = sealing({
    responses: { '/api/v1/vault/account/init': { ok: false, status: 409 } },
  });
  await app.generateAndSeal();
  assert.equal(app.step, 1);
  assert.match(app.error, /already been set up/);
});

test('a lost finalize response still shows the key when the account is active', async () => {
  // The worst failure this flow has: the server committed, so the identity can
  // only ever be opened with the secret this page is holding, and the browser
  // never saw the reply. Telling the user nothing was saved would send them
  // away from the only screen that can still show it - and onboarding refuses
  // to run twice, so there is no second chance.
  const { app } = sealing({
    responses: {
      '/api/v1/vault/account/init': INIT_OK,
      '/api/v1/vault/account/finalize': () => {
        throw new TypeError('network error');
      },
      '/api/v1/vault/account/envelope': {
        ok: true,
        json: async () => ({ state: 'active', kex_public: 'b64' }),
      },
    },
  });
  await app.generateAndSeal();
  assert.equal(app.step, 3);
  assert.equal(app.error, '');
});

test('a lost finalize response with nothing committed says so', async () => {
  const { app } = sealing({
    responses: {
      '/api/v1/vault/account/init': INIT_OK,
      '/api/v1/vault/account/finalize': () => {
        throw new TypeError('network error');
      },
      '/api/v1/vault/account/envelope': {
        ok: true,
        json: async () => ({ state: 'pending', kex_public: '' }),
      },
    },
  });
  await app.generateAndSeal();
  assert.equal(app.step, 1);
  assert.match(app.error, /Nothing was saved/);
});

test('a retry keeps the secret the first attempt may already have sealed', async () => {
  // init is idempotent while the identity is pending, so the retry derives the
  // same keys - from the same secret. A fresh draw would put a key on screen
  // that does not open the vault.
  let attempts = 0;
  const { app } = sealing({
    responses: {
      '/api/v1/vault/account/init': INIT_OK,
      '/api/v1/vault/account/finalize': () => {
        attempts += 1;
        if (attempts === 1) throw new TypeError('network error');
        return { ok: true, status: 201 };
      },
      '/api/v1/vault/account/envelope': { ok: true, json: async () => ({ state: 'pending', kex_public: '' }) },
    },
  });
  await app.generateAndSeal();
  const first = app.secretText;
  await app.generateAndSeal();
  assert.equal(app.step, 3);
  assert.equal(app.secretText, first);
});

test('a corpus answer about a replaced password is discarded', async () => {
  // Two lookups in flight, the debounce being shorter than the round trip. If
  // the older one is allowed to land last, "clean" overwrites "found" and a
  // breached password walks through the floor.
  const sha1 = require('node:crypto');
  const digest = (value) =>
    sha1.createHash('sha1').update(value).digest('hex').toUpperCase();
  const resolvers = [];
  const app = component({
    fetch: () => new Promise((resolve) => resolvers.push(resolve)),
  });

  app.password = 'a-password-nobody-has-used';
  const stale = app.checkBreachCorpus();
  app.generation++;
  app.password = 'password1';
  const current = app.checkBreachCorpus();
  // Let both hash and reach the stubbed fetch.
  while (resolvers.length < 2) await new Promise((r) => setTimeout(r, 1));

  resolvers[1]({ ok: true, text: async () => digest('password1').slice(5) + ':42' });
  await current;
  assert.equal(app.breachStatus, 'found');

  resolvers[0]({ ok: true, text: async () => 'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:1' });
  await stale;
  assert.equal(app.breachStatus, 'found');
});

test('the download anchor is inserted before it is clicked', () => {
  // Firefox ignores a click on a detached anchor: the kit never downloads, no
  // error is raised, and the secret is only on screen.
  const events = [];
  const link = {
    click: () => events.push('click'),
    remove: () => events.push('remove'),
  };
  const app = component({
    document: {
      cookie: '',
      createElement: () => link,
      body: { appendChild: () => events.push('append') },
    },
    URL: { createObjectURL: () => 'blob:kit', revokeObjectURL: () => {} },
    location: { origin: 'https://workspace.example' },
    VaultOnboarding: { buildEmergencyKitPdf: () => new Uint8Array(1) },
  });
  app.$root = { dataset: { email: 'owner@example.com' } };
  app.secretText = 'SECRET';
  app.downloadKit();
  assert.deepEqual(events, ['append', 'click', 'remove']);
});

test('the csrf token comes from the shared helper', () => {
  // The component used to parse document.cookie itself with an unanchored
  // pattern, which matches a cookie merely ending in "csrftoken" and sends
  // the wrong value - a 403 the UI reports as "could not be created".
  let sent = null;
  const app = component({
    fetch: async (url, options) => {
      sent = options.headers['X-CSRFToken'];
      return { ok: true, status: 201 };
    },
    getCSRFToken: () => 'the-real-token',
    document: { cookie: 'mycsrftoken=decoy; csrftoken=the-real-token' },
  });
  app.post('/api/v1/vault/account/init');
  assert.equal(sent, 'the-real-token');
});

test('editing the password drops the previous verdict at once', () => {
  // x-model writes the field synchronously, the lookups wait 400 ms. Between
  // the two the floor must not still be answering about the old value.
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
  assert.equal(app.score, null);
  assert.equal(app.breachStatus, 'unchecked');
});

test('a strength estimate that throws says so instead of hanging', async () => {
  // The button is gated on the score, so a rejected estimate left it disabled
  // for good with nothing on screen to explain it.
  const app = component({
    VaultOnboarding: {
      estimateStrength: async () => {
        throw new Error('zxcvbn dictionaries missing');
      },
    },
  });
  app.password = 'whatever-the-user-typed';
  await app.evaluateStrength();
  assert.equal(app.score, null);
  assert.match(app.feedback, /could not be checked/);
  assert.equal(app.passwordStrongEnough(), false);
});

test('the kit download outlives the click that starts it', () => {
  // Revoking the blob URL in the same task can cancel the download, the same
  // silent way a detached anchor does.
  const events = [];
  const link = { click: () => events.push('click'), remove: () => events.push('remove') };
  const app = component({
    document: {
      cookie: '',
      createElement: () => link,
      body: { appendChild: () => events.push('append') },
    },
    URL: {
      createObjectURL: () => 'blob:kit',
      revokeObjectURL: () => events.push('revoke'),
    },
    location: { origin: 'https://workspace.example' },
    VaultOnboarding: { buildEmergencyKitPdf: () => new Uint8Array(1) },
    setTimeout: (fn) => {
      events.push('deferred');
      fn();
    },
  });
  app.$root = { dataset: { email: 'owner@example.com' } };
  app.secretText = 'SECRET';
  app.downloadKit();
  // 'revoke' only ever after 'deferred': never in the task that clicked.
  assert.deepEqual(events, ['append', 'click', 'remove', 'deferred', 'revoke']);
});
