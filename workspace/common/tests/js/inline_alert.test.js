'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

// Minimal DOM stand-in: just enough surface for the <inline-alert> element.
// Doubles as the HTMLElement base class the element extends.
class FakeNode {
  constructor(tag = 'inline-alert') {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.childNodes = [];
    this.attributes = {};
    this.listeners = {};
    this.className = '';
    this.textContent = '';
    this.parentNode = null;
    const self = this;
    this.classList = {
      add(...classes) {
        const current = new Set(self.className.split(/\s+/).filter(Boolean));
        classes.forEach((c) => current.add(c));
        self.className = [...current].join(' ');
      },
    };
  }

  get children() {
    return this.childNodes.filter((n) => n.nodeType === 1);
  }

  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  hasAttribute(name) { return name in this.attributes; }
  removeAttribute(name) { delete this.attributes[name]; }

  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.childNodes = child.parentNode.childNodes.filter((c) => c !== child);
    }
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  append(...nodes) { nodes.forEach((n) => this.appendChild(n)); }

  replaceChildren(...nodes) {
    this.childNodes.forEach((c) => { c.parentNode = null; });
    this.childNodes = [];
    nodes.forEach((n) => this.appendChild(n));
  }

  addEventListener(evt, fn) { (this.listeners[evt] ||= []).push(fn); }
  click() { (this.listeners.click || []).forEach((fn) => fn()); }

  remove() {
    if (this.parentNode) {
      this.parentNode.childNodes = this.parentNode.childNodes.filter((c) => c !== this);
    }
    this.parentNode = null;
    this.removed = true;
  }
}

function fakeText(text) {
  return { nodeType: 3, textContent: text, parentNode: null };
}

function load() {
  const defined = {};
  const ctx = loadScript('workspace/common/static/ui/js/inline_alert.js', {
    HTMLElement: FakeNode,
    customElements: { get: () => undefined, define: (name, cls) => { defined[name] = cls; } },
    document: { createElement: (tag) => new FakeNode(tag) },
  });
  return { ctx, InlineAlertElement: defined['inline-alert'] };
}

// Build, attribute, populate, then connect - the order real callers follow.
function makeAlert(attrs = {}, children = []) {
  const { InlineAlertElement } = load();
  const el = new InlineAlertElement();
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value);
  children.forEach((c) => el.appendChild(c));
  el.connectedCallback();
  return el;
}

// Depth-first collector, since buttons nest inside the actions row.
function collect(el, pred, out = []) {
  if (pred(el)) out.push(el);
  for (const child of el.childNodes) {
    if (child.nodeType === 1) collect(child, pred, out);
  }
  return out;
}

const isButton = (el) => el.tagName === 'BUTTON';

test('registers the element and exposes the per-type style table', () => {
  const { InlineAlertElement, ctx } = load();
  assert.ok(InlineAlertElement);
  assert.deepEqual(Object.keys(ctx.INLINE_ALERT_TYPES).sort(), ['error', 'info', 'success', 'warning']);
});

test('applies role, container classes and the per-type border', () => {
  for (const [type, border] of [
    ['success', 'border-success/30'],
    ['error', 'border-error/30'],
    ['warning', 'border-warning/30'],
    ['info', 'border-info/30'],
  ]) {
    const el = makeAlert({ type, message: 'm' });
    assert.equal(el.getAttribute('role'), 'alert');
    const classes = el.className.split(/\s+/);
    for (const c of ['flex', 'items-start', 'gap-3', 'rounded-lg', 'border', 'bg-base-200/50', 'px-4', 'py-3', border]) {
      assert.ok(classes.includes(c), `${type} alert missing ${c}`);
    }
  }
});

test('falls back to info styling when type is absent or unknown', () => {
  for (const attrs of [{ message: 'm' }, { type: 'catastrophe', message: 'm' }]) {
    const el = makeAlert(attrs);
    assert.ok(el.className.includes('border-info/30'));
    assert.equal(el.children[0].getAttribute('data-lucide'), 'info');
  }
});

test('renders the default icon and color for each type', () => {
  for (const [type, icon, color] of [
    ['success', 'circle-check', 'text-success'],
    ['error', 'circle-x', 'text-error'],
    ['warning', 'triangle-alert', 'text-warning'],
    ['info', 'info', 'text-info'],
  ]) {
    const iconEl = makeAlert({ type, message: 'm' }).children[0];
    assert.equal(iconEl.tagName, 'I');
    assert.equal(iconEl.getAttribute('data-lucide'), icon);
    assert.ok(iconEl.className.includes(color));
  }
});

test('icon attribute overrides the lucide name but keeps the type color', () => {
  const iconEl = makeAlert({ type: 'warning', icon: 'globe', message: 'm' }).children[0];
  assert.equal(iconEl.getAttribute('data-lucide'), 'globe');
  assert.ok(iconEl.className.includes('text-warning'));
});

test('icon="none" renders no icon', () => {
  const el = makeAlert({ icon: 'none', message: 'm' });
  assert.equal(collect(el, (n) => n.tagName === 'I').length, 0);
});

test('message renders as a flex-1 span with the text set safely', () => {
  const el = makeAlert({ message: '<b>owned</b>' });
  const span = el.children[1];
  assert.equal(span.tagName, 'SPAN');
  assert.ok(span.className.includes('flex-1'));
  // textContent assignment, so markup in the message stays inert text.
  assert.equal(span.textContent, '<b>owned</b>');
});

test('title renders a heading above the message and stops being a tooltip', () => {
  const el = makeAlert({ type: 'error', title: 'Failed', message: 'Try again.' });
  const wrap = el.children[1];
  assert.equal(wrap.children.length, 2);
  assert.ok(wrap.children[0].className.includes('font-semibold'));
  assert.equal(wrap.children[0].textContent, 'Failed');
  assert.equal(wrap.children[1].textContent, 'Try again.');
  // Consumed on render so the browser doesn't show a native tooltip.
  assert.equal(el.hasAttribute('title'), false);
});

test('child content becomes the body, preserved by reference, over message', () => {
  const slotted = new FakeNode('span');
  slotted.textContent = 'custom';
  const el = makeAlert({ type: 'error', message: 'ignored' }, [slotted]);
  const wrap = el.children[1];
  assert.ok(wrap.className.includes('flex-1'));
  assert.equal(wrap.childNodes[0], slotted);
  assert.equal(collect(el, (n) => n.textContent === 'ignored').length, 0);
});

test('whitespace-only text children do not count as slot content', () => {
  const el = makeAlert({ message: 'm' }, [fakeText('\n  ')]);
  assert.equal(el.children[1].textContent, 'm');
});

test('slot="actions" children move into a trailing actions row', () => {
  const button = new FakeNode('button');
  button.setAttribute('slot', 'actions');
  button.className = 'btn btn-xs btn-primary';
  const el = makeAlert({ message: 'm' }, [button]);
  const row = el.children[2];
  assert.ok(row.className.includes('gap-2'));
  assert.equal(row.children[0], button);
  // The message body still renders: action children are not slot content.
  assert.equal(el.children[1].textContent, 'm');
});

test('an action with data-dismiss removes the alert on click', () => {
  const keep = new FakeNode('button');
  keep.setAttribute('slot', 'actions');
  const bye = new FakeNode('button');
  bye.setAttribute('slot', 'actions');
  bye.setAttribute('data-dismiss', '');
  const container = new FakeNode('div');
  const el = makeAlert({ message: 'm' }, [keep, bye]);
  container.appendChild(el);

  keep.click();
  assert.equal(el.removed, undefined);
  bye.click();
  assert.equal(el.removed, true);
  assert.equal(container.childNodes.length, 0);
});

test('dismissible renders a labelled close button that removes the alert', () => {
  const container = new FakeNode('div');
  const el = makeAlert({ message: 'm', dismissible: '' });
  container.appendChild(el);
  const buttons = collect(el, isButton);
  assert.equal(buttons.length, 1);
  assert.equal(buttons[0].getAttribute('aria-label'), 'Dismiss');
  buttons[0].click();
  assert.equal(el.removed, true);
  assert.equal(container.childNodes.length, 0);
});

test('renders no buttons without actions or dismissible', () => {
  const el = makeAlert({ message: 'm' });
  assert.equal(collect(el, isButton).length, 0);
});

test("the author's own classes survive rendering", () => {
  const { InlineAlertElement } = load();
  const el = new InlineAlertElement();
  el.className = 'mb-4';
  el.setAttribute('message', 'm');
  el.connectedCallback();
  const classes = el.className.split(/\s+/);
  assert.ok(classes.includes('mb-4'));
  assert.ok(classes.includes('flex'));
});

test('reconnecting does not render twice', () => {
  const el = makeAlert({ message: 'm' });
  const count = el.childNodes.length;
  el.connectedCallback();
  assert.equal(el.childNodes.length, count);
});
