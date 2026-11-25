'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('./loader');

// A clock the test advances by hand, and just enough DOM for the engine to
// build, fill, place and hide its popover.
function fakeClock() {
  let now = 0;
  let nextId = 1;
  const timers = new Map();
  return {
    setTimeout(fn, ms) { const id = nextId++; timers.set(id, { at: now + ms, fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
    tick(ms) {
      const until = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fn();
      }
      now = until;
    },
  };
}

function fakeElement(tag) {
  const node = {
    tagName: tag, style: {}, className: '', dataset: {}, children: [], listeners: {}, parentNode: null,
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    removeChild(child) { this.children = this.children.filter((c) => c !== child); child.parentNode = null; },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 100, top: 100, right: 140, bottom: 140, width: 40, height: 40 }; },
    get offsetHeight() { return 0; },
    set textContent(value) { this.children = []; },
    get html() { return this.children.map((c) => c.html || '').join(''); },
  };
  if (tag === 'template') {
    Object.defineProperty(node, 'innerHTML', { set(value) { node.content = { html: value }; } });
  }
  return node;
}

function setup() {
  const clock = fakeClock();
  const body = fakeElement('body');
  const fetched = [];
  const ctx = loadScripts(
    ['workspace/common/static/ui/js/popover.js', 'workspace/common/static/ui/js/card_popover.js'],
    {
      innerWidth: 1280,
      innerHeight: 800,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
      document: { body, createElement: fakeElement },
      fetch(url) {
        fetched.push(url);
        return Promise.resolve({ ok: true, text: () => Promise.resolve('<p>' + url + '</p>') });
      },
    },
  );
  const card = ctx.createHoverCard();
  const popoverOf = () => body.children[body.children.length - 1];
  const isOpen = (popover) => !!popover && popover.style.display !== 'none' && popover.style.opacity === '1';
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  return { ctx, card, clock, body, fetched, popoverOf, isOpen, settle };
}

test('the card opens after the hover delay, filled from its URL', async () => {
  const { card, clock, fetched, popoverOf, isOpen, settle } = setup();
  const anchor = fakeElement('div');

  card.show(anchor, '/cards/1');
  clock.tick(499);
  assert.equal(popoverOf(), undefined, 'the card opened before the delay');

  clock.tick(1);
  await settle();
  assert.ok(isOpen(popoverOf()));
  assert.equal(popoverOf().html, '<p>/cards/1</p>');
  assert.deepEqual([...fetched], ['/cards/1']);
});

test('leaving before the delay opens nothing', () => {
  const { card, clock, popoverOf } = setup();
  const anchor = fakeElement('div');

  card.show(anchor, '/cards/1');
  clock.tick(300);
  card.scheduleHide(anchor);
  clock.tick(2000);
  assert.equal(popoverOf(), undefined);
});

test('leaving hides the card, coming back in time keeps it', async () => {
  const { card, clock, popoverOf, isOpen, settle } = setup();
  const anchor = fakeElement('div');
  card.show(anchor, '/cards/1');
  clock.tick(500);
  await settle();

  card.scheduleHide(anchor);
  clock.tick(150);
  card.show(anchor, '/cards/1');
  clock.tick(1000);
  assert.ok(isOpen(popoverOf()), 'coming back within the hide delay closed the card');

  card.scheduleHide(anchor);
  clock.tick(200 + 150);
  assert.equal(popoverOf().style.display, 'none');
});

test('pointing at the card itself keeps it open', async () => {
  const { card, clock, popoverOf, isOpen, settle } = setup();
  const anchor = fakeElement('div');
  card.show(anchor, '/cards/1');
  clock.tick(500);
  await settle();

  card.scheduleHide(anchor);
  popoverOf().listeners.mouseenter[0]();
  clock.tick(1000);
  assert.ok(isOpen(popoverOf()));

  popoverOf().listeners.mouseleave[0]();
  clock.tick(1000);
  assert.equal(popoverOf().style.display, 'none');
});

test('a card shown again within its lifetime is not fetched again', async () => {
  const { card, clock, fetched, popoverOf, settle } = setup();
  const anchor = fakeElement('div');
  card.show(anchor, '/cards/1');
  clock.tick(500);
  await settle();
  card.scheduleHide(anchor);
  clock.tick(1000);

  card.show(anchor, '/cards/1');
  clock.tick(500);
  await settle();
  assert.deepEqual([...fetched], ['/cards/1']);
  assert.equal(popoverOf().html, '<p>/cards/1</p>');
});

test('two anchors on the same card share one fetch', async () => {
  const { card, clock, fetched, settle } = setup();
  card.show(fakeElement('div'), '/cards/1');
  card.show(fakeElement('div'), '/cards/1');
  clock.tick(500);
  await settle();
  assert.deepEqual([...fetched], ['/cards/1']);
});

test('a card that failed to load is fetched again on the next hover', async () => {
  const { ctx, clock, body, settle } = setup();
  let calls = 0;
  ctx.fetch = () => { calls += 1; return Promise.resolve({ ok: false, text: () => Promise.resolve('') }); };
  const card = ctx.createHoverCard();
  const anchor = fakeElement('div');
  card.show(anchor, '/cards/1');
  clock.tick(500);
  await settle();
  card.scheduleHide(anchor);
  clock.tick(1000);
  card.show(anchor, '/cards/1');
  clock.tick(500);
  await settle();
  assert.equal(calls, 2);
  assert.equal(body.children.length, 1);
});

test('a shared card follows the pointer from one anchor to the next', async () => {
  const { ctx, clock, body, fetched, isOpen, settle } = setup();
  const card = ctx.createHoverCard({ shared: true });
  const point = { getBoundingClientRect: () => ({ left: 10, top: 10, right: 20, bottom: 20, width: 10, height: 10 }) };

  card.show(point, '/cards/1');
  clock.tick(500);
  card.show(fakeElement('a'), '/cards/2');
  clock.tick(500);
  await settle();

  assert.equal(body.children.length, 1, 'a shared card built a second popover');
  assert.ok(isOpen(body.children[0]));
  assert.equal(body.children[0].html, '<p>/cards/2</p>', 'the first card landed over the second');
  assert.deepEqual([...fetched], ['/cards/1', '/cards/2']);

  card.scheduleHide();
  clock.tick(1000);
  assert.equal(body.children[0].style.display, 'none');
});

test('detaching an anchor cancels its pending card and removes its popover', async () => {
  const { card, clock, body, settle } = setup();
  const pending = fakeElement('div');
  card.show(pending, '/cards/1');
  card.detach(pending);
  clock.tick(1000);
  assert.equal(body.children.length, 0, 'a card opened for an anchor already gone');

  const shown = fakeElement('div');
  card.show(shown, '/cards/1');
  clock.tick(500);
  await settle();
  card.detach(shown);
  assert.equal(body.children.length, 0);
});

test('refresh drops the cache and reloads the card on screen', async () => {
  const { card, clock, fetched, settle } = setup();
  const anchor = fakeElement('div');
  card.show(anchor, '/cards/1');
  clock.tick(500);
  await settle();
  await card.refresh(anchor);
  assert.deepEqual([...fetched], ['/cards/1', '/cards/1']);
});

test('the click handler gets the anchor the card belongs to', async () => {
  const { ctx, clock, popoverOf, settle } = setup();
  const clicks = [];
  const card = ctx.createHoverCard({ onClick: (event, anchor) => clicks.push([event, anchor]) });
  const anchor = fakeElement('div');
  card.show(anchor, '/cards/1');
  clock.tick(500);
  await settle();
  popoverOf().listeners.click[0]('click-event');
  assert.equal(clicks.length, 1);
  assert.equal(clicks[0][0], 'click-event');
  assert.equal(clicks[0][1], anchor);
});
