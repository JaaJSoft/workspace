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
