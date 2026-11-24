// What a date cell shows.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_format.js');

test('a timestamp becomes a short date', () => {
  assert.ok(ctx.vaultFormat.shortDate('2026-08-28T10:00:00Z').length > 3);
});

test('an unreadable value shows a dash rather than "Invalid Date"', () => {
  for (const bad of ['', null, undefined, 'not a date']) {
    assert.equal(ctx.vaultFormat.shortDate(bad), '-');
  }
});

test('a date follows the interface language, not the browser default', () => {
  const japanese = loadScript('workspace/vault/ui/static/vault/ui/js/vault_format.js', {
    getUserLocale: () => 'ja',
  });
  assert.match(japanese.vaultFormat.shortDate('2026-08-28T10:00:00Z'), /年/);
});
