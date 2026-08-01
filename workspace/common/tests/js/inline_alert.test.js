'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

// Minimal DOM stand-in: just enough surface for InlineAlert.create().
function fakeElement(tag) {
  return {
    tagName: tag.toUpperCase(),
    children: [],
    attributes: {},
    listeners: {},
    className: '',
    textContent: '',
    parentNode: null,
    setAttribute(k, v) { this.attributes[k] = v; },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    addEventListener(evt, fn) { (this.listeners[evt] ||= []).push(fn); },
    click() { (this.listeners.click || []).forEach((fn) => fn()); },
    remove() {
      if (this.parentNode) {
        this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
      }
      this.parentNode = null;
      this.removed = true;
    },
  };
}

function make() {
  const ctx = loadScript('workspace/common/static/ui/js/inline_alert.js', {
    document: { createElement: fakeElement },
  });
  return ctx.InlineAlert;
}

// Depth-first collector, since buttons nest inside the actions row.
function collect(el, pred, out = []) {
  if (pred(el)) out.push(el);
  for (const child of el.children) collect(child, pred, out);
  return out;
}

const isButton = (el) => el.tagName === 'BUTTON';

test('renders one button per action with label and style class', () => {
  const alert = make().create({
    message: 'm',
    actions: [
      { label: 'Update', style: 'primary' },
      { label: 'Ignore' },
    ],
  });
  const buttons = collect(alert, isButton);
  assert.equal(buttons.length, 2);
  assert.equal(buttons[0].textContent, 'Update');
  assert.ok(buttons[0].className.includes('btn-primary'));
  assert.equal(buttons[1].textContent, 'Ignore');
  assert.ok(buttons[1].className.includes('btn-ghost'));
});

test('invokes onClick when an action button is clicked', () => {
  let clicked = 0;
  const alert = make().create({
    message: 'm',
    actions: [{ label: 'Go', onClick: () => { clicked += 1; } }],
  });
  collect(alert, isButton)[0].click();
  assert.equal(clicked, 1);
});

test('dismiss: true removes the alert after onClick', () => {
  const order = [];
  const InlineAlert = make();
  const container = fakeElement('div');
  const alert = InlineAlert.create({
    message: 'm',
    actions: [{ label: 'Bye', dismiss: true, onClick: () => order.push('cb') }],
  });
  container.appendChild(alert);
  collect(alert, isButton)[0].click();
  assert.deepEqual(order, ['cb']);
  assert.equal(alert.removed, true);
  assert.equal(container.children.length, 0);
});

test('renders no actions row when the option is absent', () => {
  const alert = make().create({ message: 'm' });
  assert.equal(collect(alert, isButton).length, 0);
});

test('slot node replaces the message content', () => {
  const InlineAlert = make();
  const slot = fakeElement('span');
  slot.textContent = 'custom';
  const alert = InlineAlert.create({ slot, message: 'ignored' });
  const spans = collect(alert, (el) => el === slot);
  assert.equal(spans.length, 1);
  const texts = collect(alert, (el) => el.textContent === 'ignored');
  assert.equal(texts.length, 0);
});
