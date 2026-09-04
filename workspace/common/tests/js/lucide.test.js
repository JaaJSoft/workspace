'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

// Stand-in for the svg elements Lucide leaves behind after hydration. Passed
// into the vm context as the SVGElement global so the script's instanceof
// checks match instances created here.
class FakeSVGElement {
  constructor(attrs = {}, parentElement = null) {
    this.nodeType = 1;
    this.attributes = { ...attrs };
    this.parentElement = parentElement;
    this.removedClasses = [];
    this.classList = { remove: (cls) => this.removedClasses.push(cls) };
  }

  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  hasAttribute(name) { return name in this.attributes; }
}

// Stand-in for a not-yet-hydrated `<i data-lucide>` placeholder.
class FakeIconPlaceholder {
  constructor(attrs = {}, parentElement = null) {
    this.nodeType = 1;
    this.attributes = { ...attrs };
    this.parentElement = parentElement;
  }

  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  hasAttribute(name) { return name in this.attributes; }
  querySelector() { return null; }
}

function makeContainer() {
  return { nodeType: 1, isConnected: true };
}

function setup(extraGlobals = {}) {
  const createIconsCalls = [];
  const frames = [];
  let observerCallback = null;
  let observeArgs = null;
  let disconnected = false;

  const ctx = loadScript('workspace/common/static/ui/js/lucide.js', {
    lucide: { createIcons: (opts) => createIconsCalls.push(opts ?? {}) },
    ...extraGlobals,
    SVGElement: FakeSVGElement,
    MutationObserver: class {
      constructor(callback) { observerCallback = callback; }
      observe(target, options) { observeArgs = { target, options }; }
      disconnect() { disconnected = true; }
    },
    requestAnimationFrame: (fn) => frames.push(fn),
  });

  const root = { nodeType: 1 };
  const stop = ctx.observeLucideIcons(root);
  return {
    createIconsCalls,
    observeArgs,
    stop,
    isDisconnected: () => disconnected,
    emit: (records) => observerCallback(records),
    flushFrames: () => {
      while (frames.length) frames.shift()();
    },
  };
}

test('observes data-lucide attribute changes with old values, plus childList', () => {
  const { observeArgs } = setup();
  assert.equal(observeArgs.options.childList, true);
  assert.equal(observeArgs.options.subtree, true);
  assert.equal(observeArgs.options.attributes, true);
  assert.deepEqual(Array.from(observeArgs.options.attributeFilter), ['data-lucide']);
  assert.equal(observeArgs.options.attributeOldValue, true);
});

test('re-renders the container when a data-lucide value changes in place on a hydrated svg', () => {
  const { createIconsCalls, emit, flushFrames } = setup();
  const container = makeContainer();
  const svg = new FakeSVGElement(
    { 'data-lucide': 'chevron-right', class: 'lucide lucide-chevron-down' },
    container,
  );
  emit([{ type: 'attributes', target: svg, oldValue: 'chevron-down' }]);
  flushFrames();
  assert.equal(createIconsCalls.length, 1);
  assert.equal(createIconsCalls[0].root, container);
  // The replacement svg merges the stale svg's classes - the old icon-name
  // class must be dropped or it piles up across re-renders.
  assert.deepEqual(svg.removedClasses, ['lucide-chevron-down']);
});

test('ignores same-value attribute writes (re-hydration loop guard)', () => {
  const { createIconsCalls, emit, flushFrames } = setup();
  const svg = new FakeSVGElement({ 'data-lucide': 'bell' }, makeContainer());
  emit([{ type: 'attributes', target: svg, oldValue: 'bell' }]);
  flushFrames();
  assert.equal(createIconsCalls.length, 0);
  assert.deepEqual(svg.removedClasses, []);
});

test('ignores a removed data-lucide attribute', () => {
  const { createIconsCalls, emit, flushFrames } = setup();
  const svg = new FakeSVGElement({}, makeContainer());
  emit([{ type: 'attributes', target: svg, oldValue: 'bell' }]);
  flushFrames();
  assert.equal(createIconsCalls.length, 0);
});

test('renders a data-lucide set where none existed (binding evaluated after insertion)', () => {
  const { createIconsCalls, emit, flushFrames } = setup();
  const container = makeContainer();
  const placeholder = new FakeIconPlaceholder({ 'data-lucide': 'bell-ring' }, container);
  emit([{ type: 'attributes', target: placeholder, oldValue: null }]);
  flushFrames();
  assert.equal(createIconsCalls.length, 1);
  assert.equal(createIconsCalls[0].root, container);
});

test('still renders newly added icon placeholders, scoped to their parent', () => {
  const { createIconsCalls, emit, flushFrames } = setup();
  const container = makeContainer();
  const placeholder = new FakeIconPlaceholder({ 'data-lucide': 'inbox' }, container);
  emit([{ type: 'childList', addedNodes: [placeholder] }]);
  flushFrames();
  assert.equal(createIconsCalls.length, 1);
  assert.equal(createIconsCalls[0].root, container);
});

test('skips added svg nodes and unrelated childList churn', () => {
  const { createIconsCalls, emit, flushFrames } = setup();
  const svg = new FakeSVGElement({ 'data-lucide': 'inbox' }, makeContainer());
  const plain = new FakeIconPlaceholder({}, makeContainer());
  emit([{ type: 'childList', addedNodes: [svg, plain, { nodeType: 3 }] }]);
  flushFrames();
  assert.equal(createIconsCalls.length, 0);
});

test('batches mutations sharing a container into one render pass', () => {
  const { createIconsCalls, emit, flushFrames } = setup();
  const container = makeContainer();
  const svg = new FakeSVGElement({ 'data-lucide': 'mail-open' }, container);
  const placeholder = new FakeIconPlaceholder({ 'data-lucide': 'inbox' }, container);
  emit([{ type: 'attributes', target: svg, oldValue: 'mail' }]);
  emit([{ type: 'childList', addedNodes: [placeholder] }]);
  flushFrames();
  assert.equal(createIconsCalls.length, 1);
  assert.equal(createIconsCalls[0].root, container);
});

test('skips containers detached before the render pass runs', () => {
  const { createIconsCalls, emit, flushFrames } = setup();
  const container = { nodeType: 1, isConnected: false };
  const svg = new FakeSVGElement({ 'data-lucide': 'check' }, container);
  emit([{ type: 'attributes', target: svg, oldValue: 'circle' }]);
  flushFrames();
  assert.equal(createIconsCalls.length, 0);
});

test('swaps icons behind Alpine\'s back and re-binds only the svgs it created', () => {
  // Alpine's MutationObserver compares every removed initialised node with
  // every added one, which is quadratic on a listing-sized render pass. The
  // swap must run under mutateDom, with the observer's two duties - release
  // the outgoing nodes, bind the incoming svgs - done by hand around it.
  const log = [];
  const container = makeContainer();
  const placeholder = new FakeIconPlaceholder({ 'data-lucide': 'inbox' }, container);
  const staleSvg = new FakeSVGElement({ 'data-lucide': 'bell' }, container);
  const freshSvgs = [new FakeSVGElement({ 'data-lucide': 'inbox' }), new FakeSVGElement({ 'data-lucide': 'bell' })];
  let swapped = false;
  container.querySelectorAll = (selector) => {
    if (selector === 'svg[data-lucide]') return swapped ? freshSvgs : [];
    return [placeholder, staleSvg];
  };
  const { emit, flushFrames } = setup({
    lucide: {
      createIcons: (opts) => {
        log.push(['createIcons', opts.root]);
        swapped = true;
      },
    },
    Alpine: {
      mutateDom: (fn) => {
        log.push('mutateDom:start');
        fn();
        log.push('mutateDom:end');
      },
      destroyTree: (el) => log.push(['destroyTree', el]),
      initTree: (el) => log.push(['initTree', el]),
    },
  });
  emit([{ type: 'childList', addedNodes: [placeholder] }]);
  flushFrames();
  assert.deepEqual(log, [
    ['destroyTree', placeholder],
    ['destroyTree', staleSvg],
    'mutateDom:start',
    ['createIcons', container],
    'mutateDom:end',
    ['initTree', freshSvgs[0]],
    ['initTree', freshSvgs[1]],
  ]);
});

test('renders plainly when Alpine is not on the page', () => {
  const { createIconsCalls, emit, flushFrames } = setup();
  const container = makeContainer();
  emit([{ type: 'childList', addedNodes: [new FakeIconPlaceholder({ 'data-lucide': 'inbox' }, container)] }]);
  flushFrames();
  assert.deepEqual(createIconsCalls.map((c) => ({ ...c })), [{ root: container }]);
});

test('returns a disconnect function', () => {
  const { stop, isDisconnected } = setup();
  stop();
  assert.equal(isDisconnected(), true);
});
