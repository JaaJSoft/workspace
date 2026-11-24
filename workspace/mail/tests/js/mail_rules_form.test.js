'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

// What the page embeds from services/rules/choices.py, trimmed to one field
// of each kind.
const text = { value: 'from', label: 'From', ops: [
  { value: 'contains', label: 'contains', takes_value: true },
  { value: 'equals', label: 'equals', takes_value: true },
  { value: 'starts_with', label: 'starts with', takes_value: true },
  { value: 'ends_with', label: 'ends with', takes_value: true },
  { value: 'matches_regex', label: 'matches regex', takes_value: true },
  { value: 'in_list', label: 'in list', takes_value: true },
] };
const bool = { value: 'is_starred', label: 'Is starred', ops: [
  { value: 'is_true', label: 'is true', takes_value: false },
  { value: 'is_false', label: 'is false', takes_value: false },
] };
const date = { value: 'date', label: 'Date', ops: [
  { value: 'greater_than', label: 'greater than', takes_value: true },
  { value: 'less_than', label: 'less than', takes_value: true },
] };

function makeApp() {
  const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_rules_form.js', {
    gettext: (s) => s,
  });
  const app = ctx.mailRulesFormMixin();
  app.rulesConditionFields = [text, bool, date];
  app.rulesForm = { simpleCondition: { field: 'from', op: 'contains', value: 'x' } };
  return app;
}

test('each field offers the operators of its kind, in order', () => {
  const app = makeApp();
  assert.deepEqual(Array.from(app.rulesCompatibleOps('from')), text.ops.map(o => o.value));
  assert.deepEqual(Array.from(app.rulesCompatibleOps('is_starred')), ['is_true', 'is_false']);
  assert.deepEqual(Array.from(app.rulesCompatibleOps('date')), ['greater_than', 'less_than']);
  assert.deepEqual(Array.from(app.rulesCompatibleOps('nope')), []);
});

test('operators are labelled and say whether they take a value', () => {
  const app = makeApp();
  assert.equal(app.rulesOpLabel('starts_with'), 'starts with');
  assert.equal(app.rulesOpLabel('greater_than'), 'greater than');
  assert.equal(app.rulesNeedsValue('contains'), true);
  assert.equal(app.rulesNeedsValue('is_true'), false);
});

test('switching to a boolean field picks its first operator and drops the value', () => {
  const app = makeApp();
  app.rulesForm.simpleCondition.field = 'is_starred';

  app.rulesOnFieldChange();

  assert.equal(app.rulesForm.simpleCondition.op, 'is_true');
  assert.equal(app.rulesForm.simpleCondition.value, '');
});

test('switching between text fields keeps a compatible operator', () => {
  const app = makeApp();
  app.rulesConditionFields = [text, { ...text, value: 'subject', label: 'Subject' }];
  app.rulesForm.simpleCondition = { field: 'subject', op: 'ends_with', value: 'x' };

  app.rulesOnFieldChange();

  assert.equal(app.rulesForm.simpleCondition.op, 'ends_with');
  assert.equal(app.rulesForm.simpleCondition.value, 'x');
});
