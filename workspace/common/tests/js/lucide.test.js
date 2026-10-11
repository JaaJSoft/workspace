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
    this.isConnected = true;
    this.attributes = { ...attrs };
    this.parentElement = parentElement;
    this.removedClasses = [];
    this.classList = { remove: (cls) => this.removedClasses.push(cls) };
  }

  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  hasAttribute(name) { return name in this.attributes; }
  matches(selector) { return selector === '[data-lucide]' && this.hasAttribute('data-lucide'); }
  querySelectorAll() { return []; }
}

// Stand-in for a not-yet-hydrated `<i data-lucide>` placeholder.
class FakeIconPlaceholder {
  constructor(attrs = {}, parentElement = null) {
    this.nodeType = 1;
    this.isConnected = true;
    this.attributes = { ...attrs };
    this.parentElement = parentElement;
  }

  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  hasAttribute(name) { return name in this.attributes; }
  matches(selector) { return selector === '[data-lucide]' && this.hasAttribute('data-lucide'); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

function makeContainer(parentElement = null) {
  return { nodeType: 1, isConnected: true, parentElement };
}

// An element holding icons, such as a row an x-for just inserted.
function makeSubtree(parentElement = null, icons = []) {
  return {
    nodeType: 1,
    isConnected: true,
    parentElement,
    hasAttribute: () => false,
    matches: () => false,
    querySelector: () => icons[0] || {},
    querySelectorAll: () => icons,
  };
}

// The render passes are recorded at drawLucideIcons, whose output is checked
// against Lucide's own in a browser (common/tests/e2e/test_lucide_icons.py).
function setup(extraGlobals = {}, draw = () => []) {
  const drawCalls = [];
  const frames = [];
  let observerCallback = null;
  let observeArgs = null;
  let disconnected = false;

  const ctx = loadScript('workspace/common/static/ui/js/lucide.js', {
    lucide: { icons: {}, createElement: () => null },
    ...extraGlobals,
    SVGElement: FakeSVGElement,
    MutationObserver: class {
      constructor(callback) { observerCallback = callback; }
      observe(target, options) { observeArgs = { target, options }; }
      disconnect() { disconnected = true; }
    },
    requestAnimationFrame: (fn) => frames.push(fn),
  });
  ctx.drawLucideIcons = (root) => {
    drawCalls.push({ root });
    return draw(root);
  };

  const root = { nodeType: 1 };
  const stop = ctx.observeLucideIcons(root);
  return {
    drawCalls,
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

test('redraws the svg whose data-lucide value changed in place, not its container', () => {
  const { drawCalls, emit, flushFrames } = setup();
  const container = makeContainer();
  const svg = new FakeSVGElement(
    { 'data-lucide': 'chevron-right', class: 'lucide lucide-chevron-down' },
    container,
  );
  emit([{ type: 'attributes', target: svg, oldValue: 'chevron-down' }]);
  flushFrames();
  assert.equal(drawCalls.length, 1);
  assert.equal(drawCalls[0].root, svg);
  // The replacement svg merges the stale svg's classes - the old icon-name
  // class must be dropped or it piles up across re-renders.
  assert.deepEqual(svg.removedClasses, ['lucide-chevron-down']);
});

test('ignores same-value attribute writes (re-hydration loop guard)', () => {
  const { drawCalls, emit, flushFrames } = setup();
  const svg = new FakeSVGElement({ 'data-lucide': 'bell' }, makeContainer());
  emit([{ type: 'attributes', target: svg, oldValue: 'bell' }]);
  flushFrames();
  assert.equal(drawCalls.length, 0);
  assert.deepEqual(svg.removedClasses, []);
});

test('ignores a removed data-lucide attribute', () => {
  const { drawCalls, emit, flushFrames } = setup();
  const svg = new FakeSVGElement({}, makeContainer());
  emit([{ type: 'attributes', target: svg, oldValue: 'bell' }]);
  flushFrames();
  assert.equal(drawCalls.length, 0);
});

test('renders a data-lucide set where none existed (binding evaluated after insertion)', () => {
  const { drawCalls, emit, flushFrames } = setup();
  const container = makeContainer();
  const placeholder = new FakeIconPlaceholder({ 'data-lucide': 'bell-ring' }, container);
  emit([{ type: 'attributes', target: placeholder, oldValue: null }]);
  flushFrames();
  assert.equal(drawCalls.length, 1);
  assert.equal(drawCalls[0].root, placeholder);
});

test('draws a newly added icon placeholder on its own, not its parent', () => {
  const { drawCalls, emit, flushFrames } = setup();
  const container = makeContainer();
  const placeholder = new FakeIconPlaceholder({ 'data-lucide': 'inbox' }, container);
  emit([{ type: 'childList', addedNodes: [placeholder] }]);
  flushFrames();
  assert.equal(drawCalls.length, 1);
  assert.equal(drawCalls[0].root, placeholder);
});

test('draws a newly added subtree on its own, leaving its siblings alone', () => {
  const { drawCalls, emit, flushFrames } = setup();
  const list = makeContainer();
  const row = makeSubtree(list, [new FakeIconPlaceholder({ 'data-lucide': 'inbox' })]);
  emit([{ type: 'childList', addedNodes: [row] }]);
  flushFrames();
  assert.deepEqual(drawCalls.map((c) => c.root), [row]);
});

test('skips added svg nodes and unrelated childList churn', () => {
  const { drawCalls, emit, flushFrames } = setup();
  const svg = new FakeSVGElement({ 'data-lucide': 'inbox' }, makeContainer());
  const plain = new FakeIconPlaceholder({}, makeContainer());
  emit([{ type: 'childList', addedNodes: [svg, plain, { nodeType: 3 }] }]);
  flushFrames();
  assert.equal(drawCalls.length, 0);
});

test('batches an added row and the icon it binds into one render pass', () => {
  // An x-for row is inserted, then its `:data-lucide` binding sets the
  // attribute on the icon inside: both are queued, the row draws both.
  const { drawCalls, emit, flushFrames } = setup();
  const row = makeSubtree(makeContainer());
  const placeholder = new FakeIconPlaceholder({ 'data-lucide': 'inbox' }, row);
  emit([{ type: 'childList', addedNodes: [row] }]);
  emit([{ type: 'attributes', target: placeholder, oldValue: null }]);
  flushFrames();
  assert.deepEqual(drawCalls.map((c) => c.root), [row]);
});

test('skips icons detached before the render pass runs', () => {
  const { drawCalls, emit, flushFrames } = setup();
  const svg = new FakeSVGElement({ 'data-lucide': 'check' }, makeContainer());
  svg.isConnected = false;
  emit([{ type: 'attributes', target: svg, oldValue: 'circle' }]);
  flushFrames();
  assert.equal(drawCalls.length, 0);
});

test('swaps icons behind Alpine\'s back and re-binds only the svgs carrying directives', () => {
  // Alpine's MutationObserver compares every removed initialised node with
  // every added one, which is quadratic on a listing-sized render pass. The
  // swap must run under mutateDom, with the observer's two duties - release
  // the outgoing nodes, bind the incoming svgs that need it - done by hand.
  const log = [];
  const placeholder = new FakeIconPlaceholder({ 'data-lucide': 'inbox' });
  const staleSvg = new FakeSVGElement({ 'data-lucide': 'bell' });
  const boundSvg = new FakeSVGElement({ 'data-lucide': 'bell', 'x-show': 'open' });
  const row = makeSubtree(makeContainer(), [placeholder, staleSvg]);
  const { emit, flushFrames } = setup(
    {
      Alpine: {
        mutateDom: (fn) => {
          log.push('mutateDom:start');
          fn();
          log.push('mutateDom:end');
        },
        destroyTree: (el) => log.push(['destroyTree', el]),
        initTree: (el) => log.push(['initTree', el]),
      },
    },
    (root) => {
      log.push(['draw', root]);
      return [boundSvg];
    },
  );
  emit([{ type: 'childList', addedNodes: [row] }]);
  flushFrames();
  assert.deepEqual(log, [
    ['destroyTree', placeholder],
    ['destroyTree', staleSvg],
    'mutateDom:start',
    ['draw', row],
    'mutateDom:end',
    ['initTree', boundSvg],
  ]);
});

test('draws every row of a batch under one mutateDom', () => {
  const log = [];
  const list = makeContainer();
  const rows = [1, 2, 3].map(() => makeSubtree(list, [new FakeIconPlaceholder({ 'data-lucide': 'inbox' })]));
  const { emit, flushFrames } = setup(
    {
      Alpine: {
        mutateDom: (fn) => {
          log.push('mutateDom');
          fn();
        },
        destroyTree: () => {},
        initTree: () => {},
      },
    },
    (root) => {
      log.push(root);
      return [];
    },
  );
  emit([{ type: 'childList', addedNodes: rows }]);
  flushFrames();
  assert.deepEqual(log, ['mutateDom', ...rows]);
});

test('renders plainly when Alpine is not on the page', () => {
  const { drawCalls, emit, flushFrames } = setup();
  const placeholder = new FakeIconPlaceholder({ 'data-lucide': 'inbox' }, makeContainer());
  emit([{ type: 'childList', addedNodes: [placeholder] }]);
  flushFrames();
  assert.deepEqual(drawCalls.map((c) => ({ ...c })), [{ root: placeholder }]);
});

test('draws an element once when another one of the batch holds it', () => {
  // A swapped listing whose table then re-sorts its rows queues both.
  const { drawCalls, emit, flushFrames } = setup();
  const listing = makeSubtree(makeContainer());
  const row = makeSubtree(makeContainer(makeContainer(listing)));
  const sibling = makeSubtree(makeContainer());
  emit([
    { type: 'childList', addedNodes: [listing] },
    { type: 'childList', addedNodes: [row] },
    { type: 'childList', addedNodes: [sibling] },
  ]);
  flushFrames();
  assert.deepEqual(drawCalls.map((c) => c.root), [listing, sibling]);
});

test('returns a disconnect function', () => {
  const { stop, isDisconnected } = setup();
  stop();
  assert.equal(isDisconnected(), true);
});
