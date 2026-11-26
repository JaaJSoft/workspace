// The kit is the only recovery path there will ever be. These tests pin what
// it carries - the norm's §7.2 list - and that producing it touches nothing
// outside this page.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

// No fetch and no XMLHttpRequest in the context: a builder that reached the
// network would throw here rather than quietly work in production.
const ctx = loadScript(
  'workspace/vault/ui/static/vault/ui/js/vendor/vault-onboarding.js',
  {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    btoa: globalThis.btoa,
    atob: globalThis.atob,
    Blob: globalThis.Blob,
    // jsPDF sniffs the environment as it loads; without these it throws
    // before a single line of our code runs.
    navigator: { userAgent: 'node', language: 'en-US' },
    document: {
      createElement: () => ({ style: {}, getContext: () => null }),
      createElementNS: () => ({ style: {} }),
      documentElement: { style: {} },
    },
  }
);

const KIT = {
  email: 'demo@example.com',
  serverUrl: 'https://workspace.example.com',
  secretText: 'ABCD-EFGH-JKMN',
  createdAt: '2026-08-22',
};

test('the kit carries every field the norm requires', () => {
  const printed = ctx.vaultOnboardingTools.emergencyKitFields(KIT).join('\n');
  for (const value of Object.values(KIT)) {
    assert.ok(printed.includes(value), `missing ${value}`);
  }
});

test('the kit says out loud that nobody can recover the vault', () => {
  const printed = ctx.vaultOnboardingTools.emergencyKitFields(KIT).join('\n');
  assert.match(printed, /recover/i);
  assert.match(printed, /nobody|no one/i);
});

test('the pdf is produced offline, and it is a pdf', () => {
  const blob = ctx.vaultOnboardingTools.buildEmergencyKitPdf(KIT);
  assert.ok(blob.size > 0);
  assert.equal(blob.type, 'application/pdf');
});

test('the recovery key actually reaches the pdf', async () => {
  // Asserting the contents through emergencyKitFields is only honest if the
  // builder really prints them. Two kits differing by one field must not
  // produce the same bytes.
  const other = { ...KIT, secretText: 'ZZZZ-ZZZZ-ZZZZ' };
  const bytes = async (kit) =>
    Buffer.from(await ctx.vaultOnboardingTools.buildEmergencyKitPdf(kit).arrayBuffer());
  // Same length either way, so the sizes match: it is the content that has
  // to differ.
  assert.notDeepEqual(await bytes(KIT), await bytes(other));
});

// --- the helper around the bundle: emergency_kit.js -------------------------

function helper(globals = {}) {
  const appended = [];
  const built = [];
  const helperCtx = loadScript('workspace/vault/ui/static/vault/ui/js/emergency_kit.js', {
    document: {
      createElement: () => ({}),
      head: { appendChild: (script) => appended.push(script) },
    },
    location: { origin: 'https://workspace.example.com' },
    downloadBlob: (blob, filename) => built.push({ blob, filename }),
    userTzDayKey: () => '2026-08-22',
    ...globals,
  });
  return { kit: helperCtx.vaultEmergencyKit, helperCtx, appended, built };
}

test('the key is grouped in blocks of four, the short last one included', () => {
  const { kit } = helper();
  assert.equal(kit.grouped('ABCDEFGHIJ'), 'ABCD-EFGH-IJ');
  assert.equal(kit.grouped(''), '');
});

test('a kit is dated by its owner\'s day, not by the day it is in UTC', () => {
  // The date is what tells two kits of one account apart. At 23:30 in Los
  // Angeles the UTC date is already tomorrow's.
  const seen = [];
  const { kit, built } = helper({
    vaultOnboardingTools: { buildEmergencyKitPdf: (fields) => { seen.push(fields); return 'pdf'; } },
    // Not instanceof: the date is built in the script's realm.
    userTzDayKey: (date) => { assert.equal(typeof date.getTime(), 'number'); return '2026-08-21'; },
  });
  kit.download({ email: 'demo@example.com', secretText: 'ABCDEFGHJKMN' });
  assert.deepStrictEqual({ ...seen[0] }, {
    email: 'demo@example.com',
    serverUrl: 'https://workspace.example.com',
    secretText: 'ABCD-EFGH-JKMN',
    createdAt: '2026-08-21',
  });
  assert.deepStrictEqual(built, [{ blob: 'pdf', filename: 'vault-emergency-kit.pdf' }]);
});

test('a page that already carries the bundle loads nothing', async () => {
  const { kit, appended } = helper({ vaultOnboardingTools: {} });
  await kit.load('/static/kit.js');
  assert.equal(appended.length, 0);
});

test('the bundle is fetched once, however many callers wait for it', async () => {
  const { kit, helperCtx, appended } = helper();
  const first = kit.load('/static/kit.js');
  const second = kit.load('/static/kit.js');
  assert.equal(appended.length, 1);
  assert.equal(appended[0].src, '/static/kit.js');
  helperCtx.vaultOnboardingTools = {};
  appended[0].onload();
  await Promise.all([first, second]);
});

test('a bundle that failed to load is tried again on the next call', async () => {
  const { kit, helperCtx, appended } = helper();
  const failed = kit.load('/static/kit.js');
  appended[0].onerror();
  await assert.rejects(failed, /failed to load/);
  const retried = kit.load('/static/kit.js');
  assert.equal(appended.length, 2);
  helperCtx.vaultOnboardingTools = {};
  appended[1].onload();
  await retried;
});

test('a script that loads without registering the builder is a failure', async () => {
  const { kit, appended } = helper();
  const pending = kit.load('/static/kit.js');
  appended[0].onload();
  await assert.rejects(pending, /did not register/);
});
