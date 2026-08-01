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

test('convertLocaltimes formats times in the user timezone', () => {
  const ctx = load('Asia/Tokyo');
  const el = {
    attrs: { datetime: '2026-01-31T20:00:00Z' },
    dataset: { localtime: 'time' },
    getAttribute(n) { return this.attrs[n]; },
    textContent: '',
  };
  ctx.convertLocaltimes({ querySelectorAll: () => [el] });
  assert.match(el.textContent, /05:00/); // 20:00Z = 05:00 in Tokyo
});
