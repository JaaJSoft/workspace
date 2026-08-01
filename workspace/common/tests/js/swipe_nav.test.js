const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('./loader');

function fakeElement() {
  const listeners = {};
  return {
    listeners,
    addEventListener(type, fn) {
      (listeners[type] = listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      listeners[type] = (listeners[type] || []).filter((f) => f !== fn);
    },
    fire(type, event) {
      (listeners[type] || []).forEach((fn) => fn(event));
    },
  };
}

function touch(x, y) {
  return { clientX: x, clientY: y };
}

function swipe(el, from, to) {
  el.fire('touchstart', { touches: [touch(from[0], from[1])] });
  el.fire('touchend', { changedTouches: [touch(to[0], to[1])] });
}

function setup(opts) {
  const ctx = loadScript('workspace/common/static/ui/js/swipe_nav.js');
  const el = fakeElement();
  const calls = { prev: 0, next: 0 };
  const detach = ctx.attachSwipeNavigation(el, {
    onPrev: () => calls.prev++,
    onNext: () => calls.next++,
    ...opts,
  });
  return { el, calls, detach };
}

test('swipe left triggers next', () => {
  const { el, calls } = setup();
  swipe(el, [200, 100], [100, 110]);
  assert.deepStrictEqual(calls, { prev: 0, next: 1 });
});

test('swipe right triggers prev', () => {
  const { el, calls } = setup();
  swipe(el, [100, 100], [220, 90]);
  assert.deepStrictEqual(calls, { prev: 1, next: 0 });
});

test('short swipe below threshold is ignored', () => {
  const { el, calls } = setup();
  swipe(el, [100, 100], [140, 100]);
  assert.deepStrictEqual(calls, { prev: 0, next: 0 });
});

test('custom threshold is honoured', () => {
  const { el, calls } = setup({ threshold: 30 });
  swipe(el, [100, 100], [140, 100]);
  assert.deepStrictEqual(calls, { prev: 1, next: 0 });
});

test('vertical-dominant gesture (scroll) is ignored', () => {
  const { el, calls } = setup();
  swipe(el, [100, 100], [180, 300]);
  assert.deepStrictEqual(calls, { prev: 0, next: 0 });
});

test('multi-touch start is ignored', () => {
  const { el, calls } = setup();
  el.fire('touchstart', { touches: [touch(200, 100), touch(210, 100)] });
  el.fire('touchend', { changedTouches: [touch(100, 100)] });
  assert.deepStrictEqual(calls, { prev: 0, next: 0 });
});

test('touchend without touchstart is ignored', () => {
  const { el, calls } = setup();
  el.fire('touchend', { changedTouches: [touch(100, 100)] });
  assert.deepStrictEqual(calls, { prev: 0, next: 0 });
});

test('touchcancel resets the gesture', () => {
  const { el, calls } = setup();
  el.fire('touchstart', { touches: [touch(200, 100)] });
  el.fire('touchcancel', {});
  el.fire('touchend', { changedTouches: [touch(100, 100)] });
  assert.deepStrictEqual(calls, { prev: 0, next: 0 });
});

test('detach removes all listeners', () => {
  const { el, calls, detach } = setup();
  detach();
  swipe(el, [200, 100], [100, 100]);
  assert.deepStrictEqual(calls, { prev: 0, next: 0 });
  assert.deepStrictEqual(
    Object.values(el.listeners).map((l) => l.length),
    [0, 0, 0]
  );
});
