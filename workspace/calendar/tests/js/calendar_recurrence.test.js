'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// The mixin is spread into the component, so its methods run with `this`
// bound to an object carrying `form`. Building that directly keeps the test
// on buildRecurrenceRule rather than on Alpine.
function makeMixin(form, extraGlobals = {}) {
  const ctx = loadScript(
    'workspace/calendar/ui/static/calendar/ui/js/calendar_recurrence.js',
    {
      wallClockToIso: (wallClock, tz) => {
        assert.equal(tz, 'Europe/Paris');
        return `${wallClock}+02:00`;
      },
      ...extraGlobals,
    },
  );
  const mixin = ctx.calendarRecurrenceMixin();
  mixin.form = form;
  return mixin;
}

const SIMPLE = {
  recurrence_rule: '',
  recurrence_simple: { frequency: 'weekly', interval: 1, until: null },
  recurrence_frequency: 'weekly',
  recurrence_interval: 1,
  recurrence_end: '',
  all_day: false,
};

test('a timed series ends on a UTC date-time UNTIL', () => {
  const mixin = makeMixin({ ...SIMPLE, recurrence_end: '2026-03-01' });
  const rule = mixin.buildRecurrenceRule('Europe/Paris');
  assert.match(rule, /^RRULE:FREQ=WEEKLY;UNTIL=\d{8}T\d{6}Z$/);
});

test('an all-day series ends on a date-only UNTIL, matching its DATE DTSTART', () => {
  // RFC 5545 3.3.10: UNTIL must match DTSTART's value type. A date-time UNTIL
  // on an all-day series is what CalDAV clients reject.
  const mixin = makeMixin({ ...SIMPLE, recurrence_end: '2026-03-01', all_day: true });
  assert.equal(mixin.buildRecurrenceRule('Europe/Paris'), 'RRULE:FREQ=WEEKLY;UNTIL=20260301');
});

test('no end date emits no UNTIL at all', () => {
  const mixin = makeMixin({ ...SIMPLE, all_day: true });
  assert.equal(mixin.buildRecurrenceRule('Europe/Paris'), 'RRULE:FREQ=WEEKLY');
});

test('a rule the picker cannot express is returned untouched', () => {
  const mixin = makeMixin({
    ...SIMPLE,
    recurrence_rule: 'RRULE:FREQ=MONTHLY;BYDAY=2TU',
    recurrence_simple: null,
    recurrence_end: '2026-03-01',
    all_day: true,
  });
  assert.equal(mixin.buildRecurrenceRule('Europe/Paris'), 'RRULE:FREQ=MONTHLY;BYDAY=2TU');
});

test('the interval unit follows the plural form of the interval', () => {
  const mixin = makeMixin({ ...SIMPLE, recurrence_interval: 1 });
  assert.equal(mixin.recurrenceUnitLabel('weekly'), 'week');
  mixin.form.recurrence_interval = 3;
  assert.equal(mixin.recurrenceUnitLabel('weekly'), 'weeks');
  assert.equal(mixin.recurrenceUnitLabel('yearly'), 'years');
});

test('the interval unit is the translated plural form of the interval', () => {
  const fr = { week: 'semaine', weeks: 'semaines' };
  const ngettext = (singular, plural, count) => fr[count === 1 ? singular : plural];
  const mixin = makeMixin({ ...SIMPLE, recurrence_interval: 1 }, { ngettext });
  assert.equal(mixin.recurrenceUnitLabel('weekly'), 'semaine');
  mixin.form.recurrence_interval = 3;
  assert.equal(mixin.recurrenceUnitLabel('weekly'), 'semaines');
});

test('the "Every" label is one message per frequency and count, so it can agree with the unit', () => {
  const npgettext = (context, singular, plural, count) =>
    `${context}|${count === 1 ? 'one' : 'many'}`;
  const mixin = makeMixin({ ...SIMPLE }, { npgettext });
  const expected = {
    daily: 'recurrence interval in days',
    weekly: 'recurrence interval in weeks',
    monthly: 'recurrence interval in months',
    yearly: 'recurrence interval in years',
  };
  for (const [frequency, context] of Object.entries(expected)) {
    mixin.form.recurrence_frequency = frequency;
    mixin.form.recurrence_interval = 1;
    assert.equal(mixin.recurrenceEveryLabel(), `${context}|one`);
    mixin.form.recurrence_interval = 2;
    assert.equal(mixin.recurrenceEveryLabel(), `${context}|many`);
  }
});
