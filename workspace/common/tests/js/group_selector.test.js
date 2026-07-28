'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const GROUPS = [
  { id: 1, name: 'devs' },
  { id: 2, name: 'design' },
  { id: 3, name: 'ops' },
];

function make(extraGlobals) {
  const ctx = loadScript(
    'workspace/common/static/ui/js/group_selector.js',
    extraGlobals
  );
  return ctx.groupSelector('evt', () => GROUPS);
}

test('empty query lists every group on open', () => {
  const sel = make();
  sel.search();
  assert.equal(Array.from(sel.results).length, 3);
  assert.equal(sel.showDropdown, true);
});

test('filters by name substring, case-insensitive', () => {
  const sel = make();
  sel.query = 'DES';
  sel.search();
  assert.deepEqual(
    Array.from(sel.results).map((g) => g.name),
    ['design']
  );
});

test('no match leaves the dropdown open with an empty list', () => {
  const sel = make();
  sel.query = 'zzz';
  sel.search();
  assert.equal(Array.from(sel.results).length, 0);
  assert.equal(sel.showDropdown, true);
});

test('selectGroup dispatches the event and resets state', () => {
  let dispatched = null;
  const sel = make({
    dispatchEvent: (event) => {
      dispatched = event;
    },
    CustomEvent: class {
      constructor(type, options) {
        this.type = type;
        this.detail = options.detail;
      }
    },
  });
  sel.query = 'dev';
  sel.search();
  sel.selectGroup(sel.results[0]);
  assert.equal(dispatched.type, 'evt');
  assert.equal(dispatched.detail.group.name, 'devs');
  assert.equal(sel.query, '');
  assert.equal(sel.showDropdown, false);
});

test('enter selects the highlighted group', () => {
  let dispatched = null;
  const sel = make({
    dispatchEvent: (event) => {
      dispatched = event;
    },
    CustomEvent: class {
      constructor(type, options) {
        this.type = type;
        this.detail = options.detail;
      }
    },
  });
  sel.search();
  sel.handleKeydown({ key: 'ArrowDown', preventDefault: () => {} });
  sel.handleKeydown({ key: 'ArrowDown', preventDefault: () => {} });
  sel.handleKeydown({ key: 'Enter', preventDefault: () => {} });
  assert.equal(dispatched.detail.group.name, 'design');
});
