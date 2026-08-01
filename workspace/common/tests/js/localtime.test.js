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
