'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

// Stand-in for the button carrying data-dispatch and for whatever the user
// actually clicked inside it (an icon, a label span).
class FakeElement {
  constructor(dataset = null, ancestor = null) {
    this.dataset = dataset ?? {};
    this._ancestor = ancestor;
  }

  closest(selector) {
    assert.equal(selector, '[data-dispatch]');
    if (this.dataset.dispatch) return this;
    return this._ancestor && this._ancestor.dataset.dispatch ? this._ancestor : null;
  }
}

function setup() {
  let clickHandler = null;
  const dispatched = [];
  const ctx = loadScript('workspace/common/static/ui/js/dispatch_action.js', {
    Element: FakeElement,
    CustomEvent: class CustomEvent {
      constructor(type) { this.type = type; }
    },
    document: {
      addEventListener(type, handler) {
        assert.equal(type, 'click');
        clickHandler = handler;
      },
    },
  });
  ctx.dispatchEvent = (event) => dispatched.push(event.type);
  return { click: (target) => clickHandler({ target }), dispatched };
}

test('a click on the trigger broadcasts the event it names', () => {
  const { click, dispatched } = setup();
  click(new FakeElement({ dispatch: 'changelog-open' }));
  assert.deepEqual(dispatched, ['changelog-open']);
});

test('a click on a child of the trigger broadcasts it too', () => {
  // The buttons wrap an <i data-lucide> icon and a label, so the event target
  // is almost never the element carrying the attribute.
  const { click, dispatched } = setup();
  const trigger = new FakeElement({ dispatch: 'onboarding-open' });
  click(new FakeElement(null, trigger));
  assert.deepEqual(dispatched, ['onboarding-open']);
});

test('a click anywhere else broadcasts nothing', () => {
  const { click, dispatched } = setup();
  click(new FakeElement());
  assert.deepEqual(dispatched, []);
});

test('a non-element target is ignored rather than thrown on', () => {
  // Clicks land on text nodes and on the document itself; closest() does not
  // exist there, and an exception in a delegated listener kills every later
  // handler on the page.
  const { click, dispatched } = setup();
  click({ nodeType: 3 });
  assert.deepEqual(dispatched, []);
});
