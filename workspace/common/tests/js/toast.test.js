'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('./loader');

// Minimal DOM stand-in shared by the toast container, the toasts themselves
// and the <inline-alert> element class (loaded alongside toast.js, as
// base.html does). Doubles as the HTMLElement base class.
class FakeNode {
  constructor(tag = 'div') {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.childNodes = [];
    this.attributes = {};
    this.listeners = {};
    this.className = '';
    this.textContent = '';
    this.innerHTML = '';
    this.style = {};
    this.dataset = {};
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

  get firstChild() {
    return this.childNodes[0] || null;
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
    // Mirror the browser's custom-element upgrade on insertion.
    if (typeof child.connectedCallback === 'function') child.connectedCallback();
    return child;
  }

  removeChild(child) {
    this.childNodes = this.childNodes.filter((c) => c !== child);
    child.parentNode = null;
    return child;
  }

  replaceChildren(...nodes) {
    this.childNodes.forEach((c) => { c.parentNode = null; });
    this.childNodes = [];
    nodes.forEach((n) => this.appendChild(n));
  }

  querySelector() { return null; }

  addEventListener(evt, fn) { (this.listeners[evt] ||= []).push(fn); }
  dispatch(evt) { (this.listeners[evt] || []).forEach((fn) => fn()); }
  click() { this.dispatch('click'); }

  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
    this.removed = true;
  }
}

/**
 * Load inline_alert.js + toast.js in one context, with a fake document
 * holding the #app-alerts-container (and optionally #django-messages-data)
 * and a capturing setTimeout so durations are assertable.
 */
function makeEnv({ djangoMessages = null, readyState = 'complete' } = {}) {
  const defined = {};
  const timers = [];
  const byId = {};

  const container = new FakeNode('div');
  container.className = 'fixed bottom-4 right-4 flex flex-col gap-2 max-w-md';
  byId['app-alerts-container'] = container;

  let messagesScript = null;
  if (djangoMessages) {
    messagesScript = new FakeNode('script');
    messagesScript.textContent = JSON.stringify(djangoMessages);
    byId['django-messages-data'] = messagesScript;
  }

  const documentStub = {
    readyState,
    head: new FakeNode('head'),
    listeners: {},
    getElementById: (id) => byId[id] || null,
    createElement(tag) {
      const cls = defined[tag];
      if (cls) {
        const el = new cls();
        el.tagName = tag.toUpperCase();
        return el;
      }
      return new FakeNode(tag);
    },
    addEventListener(evt, fn) { (this.listeners[evt] ||= []).push(fn); },
  };

  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/inline_alert.js',
      'workspace/common/static/ui/js/toast.js',
    ],
    {
      HTMLElement: FakeNode,
      customElements: { get: () => undefined, define: (name, cls) => { defined[name] = cls; } },
      document: documentStub,
      setTimeout: (fn, ms) => timers.push({ fn, ms }),
    },
  );

  return { ctx, AppAlert: ctx.AppAlert, container, timers, document: documentStub, messagesScript };
}

// The one assertion helper that knows how a toast encodes its type.
function toastType(el) {
  return el.getAttribute('type');
}

// Depth-first collector, since the ✕ nests inside the rendered tree.
function collect(el, pred, out = []) {
  if (pred(el)) out.push(el);
  for (const child of el.childNodes) {
    if (child.nodeType === 1) collect(child, pred, out);
  }
  return out;
}

const buttons = (el) => collect(el, (n) => n.tagName === 'BUTTON');

test('exposes the AppAlert API', () => {
  const { AppAlert } = makeEnv();
  for (const fn of ['show', 'success', 'error', 'warning', 'info', 'dismiss', 'clearAll']) {
    assert.equal(typeof AppAlert[fn], 'function', `AppAlert.${fn} missing`);
  }
});

test('show appends a toast to the container and returns it', () => {
  const { AppAlert, container } = makeEnv();
  const el = AppAlert.show({ message: 'hello', type: 'success' });
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0], el);
  assert.equal(toastType(el), 'success');
});

test('show without a message returns null and renders nothing', () => {
  const { AppAlert, container } = makeEnv();
  assert.equal(AppAlert.show(), null);
  assert.equal(AppAlert.show({}), null);
  assert.equal(container.children.length, 0);
});

test('unknown types fall back to info styling', () => {
  const { AppAlert } = makeEnv();
  const weird = AppAlert.show({ message: 'm', type: 'catastrophe' });
  assert.ok(weird.className.includes('border-info/30'));
  assert.equal(toastType(AppAlert.show({ message: 'm' })), 'info');
});

test('a toast is an <inline-alert> in its toast placement variant', () => {
  const { AppAlert } = makeEnv();
  const el = AppAlert.show({ message: 'hello', type: 'warning', title: 'Heads up' });
  assert.equal(el.tagName, 'INLINE-ALERT');
  assert.ok(el.hasAttribute('toast'));
  assert.equal(el.getAttribute('message'), 'hello');
  assert.equal(collect(el, (n) => n.textContent === 'Heads up').length, 1);
  const classes = el.className.split(/\s+/);
  assert.ok(classes.includes('bg-base-100'));
  assert.ok(classes.includes('shadow-lg'));
  assert.ok(classes.includes('border-warning/30'));
});

test('toasts are dismissible by default; dismissible false drops the ✕', () => {
  const { AppAlert } = makeEnv();
  assert.equal(buttons(AppAlert.show({ message: 'm' })).length, 1);
  assert.equal(buttons(AppAlert.show({ message: 'm', dismissible: false })).length, 0);
});

test('the ✕ slides the toast out before removing it', () => {
  const { AppAlert, container } = makeEnv();
  const el = AppAlert.show({ message: 'm', duration: 0 });
  buttons(el)[0].click();
  assert.equal(container.children.length, 1);
  assert.ok(String(el.style.animation).includes('slideOutRight'));
  el.dispatch('animationend');
  assert.equal(container.children.length, 0);
});

test('shorthands set the type; error lingers 8s, the others 5s', () => {
  const { AppAlert, timers } = makeEnv();
  const cases = [
    ['success', 5000],
    ['info', 5000],
    ['warning', 5000],
    ['error', 8000],
  ];
  cases.forEach(([type, duration], i) => {
    const el = AppAlert[type]('m');
    assert.equal(toastType(el), type);
    assert.equal(timers[i].ms, duration);
  });
});

test('auto-dismiss fires after the duration and removes the toast', () => {
  const { AppAlert, container, timers } = makeEnv();
  const el = AppAlert.show({ message: 'm' });
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, 5000);

  timers[0].fn();
  // Still in the DOM while the exit animation plays.
  assert.equal(container.children.length, 1);
  assert.ok(String(el.style.animation).includes('slideOutRight'));
  el.dispatch('animationend');
  assert.equal(container.children.length, 0);
});

test('duration 0 disables auto-dismiss', () => {
  const { AppAlert, timers } = makeEnv();
  AppAlert.show({ message: 'm', duration: 0 });
  assert.equal(timers.length, 0);
});

test('dismiss slides the toast out with the position-matched animation', () => {
  const { AppAlert, container } = makeEnv();
  const el = AppAlert.show({ message: 'm', position: 'top-center', duration: 0 });
  assert.ok(String(el.style.animation).includes('slideInDown'));

  AppAlert.dismiss(el);
  assert.ok(String(el.style.animation).includes('slideOutUp'));
  el.dispatch('animationend');
  assert.equal(container.children.length, 0);
});

test('dismissing a toast that is already gone is a no-op', () => {
  const { AppAlert } = makeEnv();
  AppAlert.dismiss(null);
  const el = AppAlert.show({ message: 'm', duration: 0 });
  AppAlert.dismiss(el);
  el.dispatch('animationend');
  AppAlert.dismiss(el);
});

test('each position maps to its container corner classes', () => {
  const cases = {
    'top-right': ['top-4', 'right-4'],
    'top-left': ['top-4', 'left-4'],
    'bottom-right': ['bottom-4', 'right-4'],
    'bottom-left': ['bottom-4', 'left-4'],
    'top-center': ['top-4', 'left-1/2', '-translate-x-1/2'],
  };
  for (const [position, expected] of Object.entries(cases)) {
    const { AppAlert, container } = makeEnv();
    AppAlert.show({ message: 'm', position });
    const classes = container.className.split(/\s+/);
    for (const c of ['fixed', ...expected]) {
      assert.ok(classes.includes(c), `${position} missing ${c}`);
    }
    assert.equal(container.style.zIndex, '99999');
  }
});

test('an unknown position falls back to bottom-right', () => {
  const { AppAlert, container } = makeEnv();
  const el = AppAlert.show({ message: 'm', position: 'under-the-couch' });
  const classes = container.className.split(/\s+/);
  assert.ok(classes.includes('bottom-4'));
  assert.ok(classes.includes('right-4'));
  assert.ok(String(el.style.animation).includes('slideInRight'));
});

test('clearAll empties the container immediately', () => {
  const { AppAlert, container } = makeEnv();
  AppAlert.show({ message: 'a', duration: 0 });
  AppAlert.show({ message: 'b', duration: 0 });
  assert.equal(container.children.length, 2);
  AppAlert.clearAll();
  assert.equal(container.children.length, 0);
});

test('django messages surface as staggered top-right toasts', () => {
  const { container, timers, messagesScript } = makeEnv({
    djangoMessages: [
      { message: 'saved', type: 'success', level: 'success' },
      { message: 'boom', type: 'error', level: 'error' },
    ],
  });

  // One stagger timer per message, 300ms apart, and the payload is consumed.
  assert.deepEqual(timers.map((t) => t.ms), [0, 300]);
  assert.equal(messagesScript.removed, true);

  timers[0].fn();
  timers[1].fn();
  assert.equal(container.children.length, 2);
  assert.equal(toastType(container.children[0]), 'success');
  assert.equal(toastType(container.children[1]), 'error');

  const classes = container.className.split(/\s+/);
  assert.ok(classes.includes('top-4'));
  assert.ok(classes.includes('right-4'));

  // Auto-dismiss durations: 5s for success, 8s for the error.
  assert.deepEqual(timers.slice(2).map((t) => t.ms), [5000, 8000]);
});

test('django message levels map through tags, then level, then info', () => {
  const cases = [
    // [tags, level_tag, expected toast type]
    ['debug', 'debug', 'info'],
    ['info', 'info', 'info'],
    ['success', 'success', 'success'],
    ['warning', 'warning', 'warning'],
    ['error', 'error', 'error'],
    // Extra tags break the exact tags match; the bare level still resolves.
    ['error urgent', 'error', 'error'],
    ['sparkly', 'sparkly', 'info'],
  ];
  const { container, timers } = makeEnv({
    djangoMessages: cases.map(([type, level], i) => ({ message: `m${i}`, type, level })),
  });
  timers.splice(0, cases.length).forEach((t) => t.fn());
  cases.forEach(([tags, level, expected], i) => {
    assert.equal(toastType(container.children[i]), expected, `tags=${tags} level=${level}`);
  });
});

test('django messages wait for DOMContentLoaded while the page is loading', () => {
  const { container, timers, document } = makeEnv({
    djangoMessages: [{ message: 'm', type: 'info', level: 'info' }],
    readyState: 'loading',
  });
  assert.equal(timers.length, 0);
  document.listeners['DOMContentLoaded'].forEach((fn) => fn());
  assert.equal(timers.length, 1);
  timers[0].fn();
  assert.equal(container.children.length, 1);
});

test('a page without the django messages payload loads cleanly', () => {
  const { timers } = makeEnv();
  assert.equal(timers.length, 0);
});
