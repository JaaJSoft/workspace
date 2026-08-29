// The component is state plus two POSTs; the crypto it calls is already
// pinned by the vector suites. What matters here is that no step can be
// skipped and that the strength floor counts what the norm says it counts.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

function component(extra = {}) {
  // session.js defines VAULT_SECRET_STORAGE_KEY, the constant
  // rememberOnThisDevice() writes and reads - both pages load it in that
  // order in the browser, so the test does too.
  const ctx = loadScripts([
    'workspace/vault/ui/static/vault/ui/js/session.js',
    'workspace/vault/ui/static/vault/ui/js/onboarding.js',
  ], {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    document: { cookie: '', createElement: () => ({ click() {} }) },
    // base.html loads ui/js/csrf.js on every page; the component reads it.
    getCSRFToken: () => 'test-csrf-token',
    fetch: async () => ({ ok: true, status: 201, json: async () => ({}) }),
    setTimeout: (fn) => fn(),
    addEventListener: () => {},
    localStorage: { setItem: () => {}, removeItem: () => {} },
    // finish() mints the first vault's UUID itself, so every test that
    // reaches it needs this much of the crypto module even when it stubs the
    // builder that would otherwise have used it.
    vaultCrypto: { uuidV7: () => 'first-vault-uuid' },
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
  uuidV7: () => 'first-vault-uuid',
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
  importSigner: async () => ({ sign: async () => new Uint8Array(64) }),
  decodePublicKey: () => new Uint8Array(32),
};

const SUBTLE_STUB = {
  generateKey: async () => ({ privateKey: {}, publicKey: {} }),
  exportKey: async () => new Uint8Array(48),
};

function sealing({ responses, ...extra } = {}) {
  const calls = [];
  const app = component({
    vaultCrypto: CRYPTO_STUB,
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
  // The first vault is created on this same page, right after the kit - it
  // needs a signer and the account's own key-exchange public key.
  assert.ok(app.vaultSigner);
  assert.ok(app.accountKexPublic);
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
  // The second attempt never generated new keys - it got a 409 from init
  // before reaching that point - so there is nothing here to build a signer
  // from, even though the envelope this call confirms is the first
  // attempt's. finish() is the one that must cope with that gap.
  assert.equal(app.vaultSigner, null);
  assert.equal(app.accountKexPublic, null);
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
  assert.match(app.error, /already set up elsewhere/);
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
  // This recovery still ran inside the same attempt that drew the keys, so
  // unlike the cross-attempt retry above, the signer is there to build.
  assert.ok(app.vaultSigner);
  assert.ok(app.accountKexPublic);
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

test('a retry that finds the account active never claims nothing was saved', async () => {
  // A 409 from init proves an ACTIVE identity exists; a non-empty
  // sentKexPublic proves this page is the one that sent an envelope. When the
  // probe that would tell those two apart cannot answer - a throttled or
  // unreachable /envelope - the one thing certainly false is "nothing was
  // saved", and it is the sentence that sends the user away from the only
  // screen that can still show them the recovery key.
  var started = 0;
  const { app } = sealing({
    responses: {
      '/api/v1/vault/account/init': () => {
        started += 1;
        return started === 1 ? INIT_OK : { ok: false, status: 409 };
      },
      '/api/v1/vault/account/finalize': () => {
        throw new TypeError('network error');
      },
      '/api/v1/vault/account/envelope': () => {
        throw new TypeError('network error');
      },
    },
  });
  await app.generateAndSeal();
  assert.ok(app.sentKexPublic);

  await app.generateAndSeal();
  assert.equal(app.step, 1);
  assert.doesNotMatch(app.error, /Nothing was saved/);
  assert.match(app.error, /do not close this page/i);
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
    vaultOnboardingTools: { buildEmergencyKitPdf: () => new Uint8Array(1) },
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
    vaultOnboardingTools: {
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
    vaultOnboardingTools: { buildEmergencyKitPdf: () => new Uint8Array(1) },
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

test('leaving the page is guarded until the key is acknowledged', async () => {
  // The key is on this page and nowhere else - not on the server, not in
  // storage. A reload or a closed tab takes it, and only the browser's own
  // prompt reaches those.
  const listeners = {};
  const { app } = sealing({
    responses: {
      '/api/v1/vault/account/init': INIT_OK,
      '/api/v1/vault/account/finalize': { ok: true, status: 201 },
    },
    addEventListener: (type, fn) => {
      listeners[type] = fn;
    },
  });

  assert.equal(listeners.beforeunload, undefined, 'nothing to lose yet');
  await app.generateAndSeal();
  assert.equal(typeof listeners.beforeunload, 'function');

  const prevented = () => {
    let stopped = false;
    listeners.beforeunload({
      preventDefault: () => {
        stopped = true;
      },
    });
    return stopped;
  };
  assert.equal(prevented(), true, 'the key is on screen and unacknowledged');

  app.acknowledged = true;
  assert.equal(prevented(), false, 'the user says they have saved it');
});

// --- finishing onboarding --------------------------------------------------
//
// finish() hands off to the vault screen by navigating there. $root and
// location are stubbed here so a finish() that never calls
// window.location.assign fails these tests instead of disappearing into
// finish()'s own catch - which every test below did, silently, before this
// file stubbed either.
function finishing(extra = {}) {
  const navigated = [];
  const app = component({
    location: { assign: (url) => navigated.push(url) },
    ...extra,
  });
  app.$root = { dataset: { vaultUrl: '/vault/' } };
  // Stands in for what generateAndSeal's success path leaves behind - these
  // tests exercise finish() on its own, without going through it.
  app.vaultSigner = { sign: async () => new Uint8Array(1) };
  app.accountKexPublic = new Uint8Array(32);
  return { app, navigated };
}

test('acknowledging the kit creates the first vault', async () => {
  const created = [];
  const { app, navigated } = finishing({
    buildVaultCreateRequest: async () => ({ uuid: 'v1' }),
    vaultApi: { createVault: async (body) => { created.push(body); return body; } },
  });
  app.step = 3;
  app.acknowledged = true;
  await app.finish();
  assert.equal(created.length, 1);
  assert.deepEqual(navigated, ['/vault/']);
});

test('the first vault is called Personal', async () => {
  // The draft, not a bare string: the builder takes everything the create
  // form offers, and a stub that swallowed the shape would hide a caller
  // left behind by the change.
  let draft = null;
  const { app } = finishing({
    buildVaultCreateRequest: async (session, given) => { draft = given; return { uuid: 'v1' }; },
    vaultApi: { createVault: async (body) => body },
  });
  app.step = 3;
  app.acknowledged = true;
  await app.finish();
  assert.equal(typeof draft, 'object');
  assert.equal(draft.name, 'Personal');
});

test('a failed first vault keeps the user on the kit screen', async () => {
  const { app, navigated } = finishing({
    buildVaultCreateRequest: async () => ({ uuid: 'v1' }),
    vaultApi: { createVault: async () => { throw new Error('refused'); } },
  });
  app.step = 3;
  app.acknowledged = true;
  await app.finish();
  assert.equal(app.step, 3);
  assert.match(app.error, /vault could not be created/i);
  // A refused creation must not send the user anywhere: the kit screen is
  // the only place that can still show the recovery key.
  assert.deepEqual(navigated, []);
});

test('retrying the first vault never touches the account endpoints again', async () => {
  const posted = [];
  const { app } = finishing({
    buildVaultCreateRequest: async () => ({ uuid: 'v1' }),
    vaultApi: { createVault: async () => { throw new Error('refused'); } },
    fetch: async (url) => { posted.push(url); return { ok: true, status: 201, json: async () => ({}) }; },
  });
  app.step = 3;
  app.acknowledged = true;
  await app.finish();
  await app.finish();
  // The identity is already active: a retry that re-ran init or finalize
  // would be refused by the server and would read as a lost account.
  assert.deepEqual(posted, []);
  assert.equal(app.step, 3);
});

test('a retried first vault carries the UUID the first attempt sent', async () => {
  // The server turns a lost answer into a conflict by matching the UUID the
  // client minted. A fresh one on the retry describes a different vault, so
  // the account that lost one answer ends up with two "Personal" vaults.
  let minted = 0;
  const seen = [];
  const { app } = finishing({
    buildVaultCreateRequest: async (session, name, uuid) => { seen.push(uuid); return { uuid }; },
    vaultApi: { createVault: async () => { throw new Error('refused'); } },
    vaultCrypto: { uuidV7: () => 'first-vault-' + ++minted },
  });
  app.step = 3;
  app.acknowledged = true;
  await app.finish();
  await app.finish();
  assert.equal(seen.length, 2);
  assert.ok(seen[0], 'the UUID has to be minted here, not inside the builder');
  assert.equal(seen[0], seen[1]);
});

test('a conflict on the first vault means it is already there', async () => {
  const { app, navigated } = finishing({
    buildVaultCreateRequest: async (session, name, uuid) => ({ uuid }),
    vaultApi: {
      createVault: async () => {
        const err = new Error('conflict');
        err.status = 409;
        throw err;
      },
    },
    vaultCrypto: { uuidV7: () => 'first-vault' },
  });
  app.step = 3;
  app.acknowledged = true;
  await app.finish();
  assert.equal(app.error, '');
  assert.deepEqual(navigated, ['/vault/']);
});

test('the recovery key is not stored unless the box is ticked', async () => {
  const stored = new Map();
  const { app } = finishing({
    localStorage: { setItem: (k, v) => stored.set(k, v), removeItem: (k) => stored.delete(k) },
    buildVaultCreateRequest: async () => ({ uuid: 'v1' }),
    vaultApi: { createVault: async () => ({}) },
  });
  app.step = 3;
  app.acknowledged = true;
  app.secretText = 'A'.repeat(53);
  await app.finish();
  assert.equal(stored.size, 0);
});

test('ticking the box stores the key in the spelling the sheet shows', async () => {
  const stored = new Map();
  const { app } = finishing({
    localStorage: { setItem: (k, v) => stored.set(k, v), removeItem: (k) => stored.delete(k) },
    buildVaultCreateRequest: async () => ({ uuid: 'v1' }),
    vaultApi: { createVault: async () => ({}) },
  });
  app.step = 3;
  app.acknowledged = true;
  app.remember = true;
  app.secretText = 'A'.repeat(53);
  await app.finish();
  assert.equal(stored.get('vault.secret-key'), app.groupedSecret());
});

test('with no signer for this identity, finish sends the user to the vault without attempting to create one', async () => {
  // The lost-response recovery path in generateAndSeal can land here with
  // vaultSigner still null - a retry that found the account active without
  // generating new keys of its own. The account is real either way; only
  // the vault creation this step would normally do is unavailable, and a
  // retry that can never succeed must not trap the user on this screen.
  let attempted = false;
  const { app, navigated } = finishing({
    buildVaultCreateRequest: async () => { attempted = true; return { uuid: 'v1' }; },
    vaultApi: { createVault: async () => { attempted = true; return {}; } },
  });
  app.vaultSigner = null;
  app.accountKexPublic = null;
  app.step = 3;
  app.acknowledged = true;
  await app.finish();
  assert.equal(attempted, false);
  assert.deepEqual(navigated, ['/vault/']);
  assert.equal(app.error, '');
});

test('with no signer for this identity, the recovery key is still remembered if ticked', async () => {
  const stored = new Map();
  const { app } = finishing({
    localStorage: { setItem: (k, v) => stored.set(k, v), removeItem: (k) => stored.delete(k) },
  });
  app.vaultSigner = null;
  app.accountKexPublic = null;
  app.step = 3;
  app.acknowledged = true;
  app.remember = true;
  app.secretText = 'A'.repeat(53);
  await app.finish();
  assert.equal(stored.get('vault.secret-key'), app.groupedSecret());
});

// A throwing localStorage (Safari private mode, a storage policy, an
// extension) must never be mistaken for a failed vault creation, and must
// never block the navigation that follows - on either branch of finish().
const THROWING_STORAGE = {
  setItem: () => { throw new Error('quota exceeded'); },
  removeItem: () => { throw new Error('quota exceeded'); },
};

test('a storage failure does not turn a successful creation into a reported failure', async () => {
  const created = [];
  const { app, navigated } = finishing({
    localStorage: THROWING_STORAGE,
    buildVaultCreateRequest: async () => ({ uuid: 'v1' }),
    vaultApi: { createVault: async (body) => { created.push(body); return body; } },
  });
  app.step = 3;
  app.acknowledged = true;
  await app.finish();
  assert.equal(created.length, 1);
  assert.deepEqual(navigated, ['/vault/']);
  assert.equal(app.error, '');
});

test('a storage failure does not block the no-signer path from reaching the vault', async () => {
  let attempted = false;
  const { app, navigated } = finishing({
    localStorage: THROWING_STORAGE,
    buildVaultCreateRequest: async () => { attempted = true; return { uuid: 'v1' }; },
    vaultApi: { createVault: async () => { attempted = true; return {}; } },
  });
  app.vaultSigner = null;
  app.accountKexPublic = null;
  app.step = 3;
  app.acknowledged = true;
  await app.finish();
  assert.equal(attempted, false);
  assert.deepEqual(navigated, ['/vault/']);
  assert.equal(app.error, '');
});
