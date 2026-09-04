'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// The mixin is spread into the component, so its methods run with `this`
// bound to an object carrying `form`. Building that directly keeps the test
// on buildRecurrenceRule rather than on Alpine.
function makeMixin(form) {
  const ctx = loadScript(
    'workspace/calendar/ui/static/calendar/ui/js/calendar_recurrence.js',
    {
      wallClockToIso: (wallClock, tz) => {
        assert.equal(tz, 'Europe/Paris');
        return `${wallClock}+02:00`;
      },
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
