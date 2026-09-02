'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('./loader');

function docStub(tzAttr) {
  return {
    documentElement: {
      getAttribute: (n) => (n === 'data-timezone' ? tzAttr : null),
    },
    body: {},
    querySelectorAll: () => [],
  };
}

class ObserverStub {
  observe() {}
}

function load(tzAttr) {
  return loadScript('workspace/common/static/ui/js/localtime.js', {
    document: docStub(tzAttr),
    MutationObserver: ObserverStub,
  });
}

test('getUserTimeZone reads the html attribute', () => {
  assert.equal(load('Asia/Tokyo').getUserTimeZone(), 'Asia/Tokyo');
});

test('getUserTimeZone is undefined without the attribute', () => {
  assert.equal(load(null).getUserTimeZone(), undefined);
});

test('userTzDayKey resolves the day in the user timezone', () => {
  const ctx = load('Asia/Tokyo');
  // 20:00 UTC on Jan 31 is already Feb 1 in Tokyo.
  assert.equal(ctx.userTzDayKey(new Date('2026-01-31T20:00:00Z')), '2026-02-01');
});

function mkEl(iso, mode) {
  return {
    attrs: { datetime: iso },
    dataset: { localtime: mode },
    getAttribute(n) { return this.attrs[n]; },
    textContent: '',
  };
}

function convert(ctx, el) {
  ctx.convertLocaltimes({ querySelectorAll: () => [el] });
  return el.textContent;
}

test('convertLocaltimes formats times in the user timezone', () => {
  const ctx = load('Asia/Tokyo');
  // 20:00Z = 05:00 in Tokyo
  assert.match(convert(ctx, mkEl('2026-01-31T20:00:00Z', 'time')), /05:00/);
});

test('date mode crosses the day boundary in the user timezone', () => {
  const ctx = load('Asia/Tokyo');
  // 20:00 UTC on Jan 31 is already Feb 1 in Tokyo.
  assert.match(convert(ctx, mkEl('2026-01-31T20:00:00Z', 'date')), /Feb 1|1 févr/);
});

test('date mode labels the current day Today and the previous day Yesterday', () => {
  const ctx = load('Asia/Tokyo');
  const now = new Date();
  assert.equal(convert(ctx, mkEl(now.toISOString(), 'date')), 'Today');
  const yesterday = new Date(now.getTime() - 86400000);
  assert.equal(convert(ctx, mkEl(yesterday.toISOString(), 'date')), 'Yesterday');
});

test('date labels build one day-key formatter per zone, not one per element', () => {
  const ctx = load('Asia/Tokyo');
  let built = 0;
  // Shadows the context's own Intl: the script looks the global up on
  // every call, so the count sees each constructor invocation.
  ctx.Intl = {
    DateTimeFormat: function (...args) {
      built++;
      return new Intl.DateTimeFormat(...args);
    },
  };
  const els = Array.from({ length: 50 }, (_, i) => mkEl(`2026-01-${String(i % 28 + 1).padStart(2, '0')}T20:00:00Z`, 'date'));
  ctx.convertLocaltimes({ querySelectorAll: () => els });
  assert.equal(built, 1);
  // The zone is part of the key: another zone gets its own formatter.
  assert.equal(ctx.userTzDayKey(new Date('2026-01-31T20:00:00Z')), '2026-02-01');
  assert.equal(built, 1);
});

test('smart mode falls back to a dated label across day boundaries', () => {
  const ctx = load('Asia/Tokyo');
  assert.match(convert(ctx, mkEl('2026-01-31T20:00:00Z', 'smart')), /Feb 1|1 févr/);
});

test('full mode renders date and time in the user timezone', () => {
  const ctx = load('Asia/Tokyo');
  const label = convert(ctx, mkEl('2026-01-31T20:00:00Z', 'full'));
  assert.match(label, /Feb 1, 2026|1 févr\. 2026/);
  assert.match(label, /05:00/);
});

test('wallClockToIso resolves wall-clock times in the given zone', () => {
  const ctx = load('Europe/Paris');
  assert.equal(ctx.wallClockToIso('2026-08-05T10:00', 'Europe/Paris'), '2026-08-05T08:00:00.000Z');
  assert.equal(ctx.wallClockToIso('2026-01-05T10:00', 'Europe/Paris'), '2026-01-05T09:00:00.000Z');
});

test('wallClockToIso resolves the DST spring-forward gap forward', () => {
  const ctx = load('Europe/Paris');
  // 02:30 local does not exist on 2026-03-29; it resolves to summer time.
  assert.equal(ctx.wallClockToIso('2026-03-29T02:30', 'Europe/Paris'), '2026-03-29T01:30:00.000Z');
});

test('wallClockToIso date-only means midnight in the zone', () => {
  const ctx = load('Europe/Paris');
  assert.equal(ctx.wallClockToIso('2026-08-05', 'Europe/Paris'), '2026-08-04T22:00:00.000Z');
});

test('isoToWallClock renders the wall clock of the given zone', () => {
  const ctx = load('Asia/Tokyo');
  assert.equal(ctx.isoToWallClock('2026-08-05T08:00:00Z', 'Europe/Paris'), '2026-08-05T10:00');
});

test('wall-clock round-trip is stable across the fall-back day', () => {
  const ctx = load('Europe/Paris');
  const iso = ctx.wallClockToIso('2026-10-25T05:30', 'Europe/Paris');
  assert.equal(ctx.isoToWallClock(iso, 'Europe/Paris'), '2026-10-25T05:30');
});
