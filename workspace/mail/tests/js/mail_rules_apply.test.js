'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

// Build a mailApp-like instance from the rules mixin with just enough state
// and stubbed globals (fetch / getCSRFToken / AppDialog) for the apply flow.
// `fetchImpl` lets each test decide what the apply endpoint returns.
function makeApp(fetchImpl) {
  const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_rules.js', {
    fetch: fetchImpl,
    getCSRFToken: () => 'csrf',
    AppDialog: { confirm: async () => true },
  });
  const app = ctx.mailRulesMixin();
  Object.assign(app, {
    rulesApplying: { uuid: 'rule-1', name: 'My rule' },
    rulesApplyFolderId: 'folder-1',
    rulesApplyResult: null,
    rulesApplyBusy: false,
    rulesAccount: { uuid: 'acc-1' },
    loadFolders: async () => {},
    loadMessages: async () => {},
  });
  return app;
}

test('rulesRunApply clears a stale preview even when the apply returns nothing', async () => {
  // Endpoint fails -> _rulesApplyRequest returns null -> rulesRunApply early
  // returns. The previously requested dry-run preview must already be gone,
  // otherwise the panel keeps showing stale "X of Y match" numbers.
  const app = makeApp(async () => ({ ok: false }));
  app.rulesApplyResult = { matched: 7, scanned: 100, applied_run: false };

  await app.rulesRunApply();

  assert.equal(app.rulesApplyResult, null);
});

test('the busy flag records which action is running (preview vs run)', async () => {
  // Capture the flag value while the request is in flight so each button can
  // render its own spinner off a single shared field.
  let seen = null;
  const app = makeApp(async () => {
    seen = app.rulesApplyBusy;
    return { ok: true, json: async () => ({ matched: 0, scanned: 0, applied: 0 }) };
  });

  await app.rulesPreviewApply();
  assert.equal(seen, 'preview');
  assert.equal(app.rulesApplyBusy, false); // reset once done

  await app.rulesRunApply();
  assert.equal(seen, 'run');
  assert.equal(app.rulesApplyBusy, false);
});
