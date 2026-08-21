'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const mixinStub = () => ({});

function makeApp(userTz) {
  const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail.js', {
    document: { getElementById: () => null },
    // Domain mixins live in separate files; the formatters under test are
    // defined in mail.js itself, so empty stubs are enough.
    attachmentInputMixin: mixinStub,
    mailAccountsMixin: mixinStub,
    mailFoldersMixin: mixinStub,
    mailMessagesMixin: mixinStub,
    mailComposeMixin: mixinStub,
    mailLabelsMixin: mixinStub,
    mailAiMixin: mixinStub,
    mailRulesMixin: mixinStub,
    mailRulesFormMixin: mixinStub,
  });
  ctx.getUserTimeZone = () => userTz;
  ctx.userTzDayKey = (d) =>
    new Intl.DateTimeFormat('en-CA', { timeZone: userTz }).format(d);
  return ctx.mailApp();
}

test('formatDate crosses the day boundary in the user timezone', () => {
  const app = makeApp('Asia/Tokyo');
  // 20:00 UTC on Jan 31 is already Feb 1 in Tokyo.
  assert.match(app.formatDate('2026-01-31T20:00:00Z'), /Feb 1|1 févr/);
});

test('formatDate renders a time for a same-day message', () => {
  const app = makeApp('Asia/Tokyo');
  const label = app.formatDate(new Date().toISOString());
  assert.match(label, /\d{1,2}:\d{2}/);
});

test('formatFullDate crosses the day boundary in the user timezone', () => {
  const app = makeApp('Asia/Tokyo');
  assert.match(app.formatFullDate('2026-01-31T20:00:00Z'), /February 1|1 février/);
});
