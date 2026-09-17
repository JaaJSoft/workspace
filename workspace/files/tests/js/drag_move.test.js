'use strict';

// Moving files by drag & drop: drag_move.js keeps the dragged items from
// dragstart to drop, decides which data-drop-folder element may take them
// and hands the accepted drop to the browser component as a window event.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

class FakeElement {
  constructor(dataset = {}, parent = null) {
    this.dataset = dataset;
    this.parent = parent;
    this.classes = new Set();
    this.classList = {
      add: (c) => this.classes.add(c),
      remove: (c) => this.classes.delete(c),
    };
  }

  closest(selector) {
    assert.equal(selector, '[data-drop-folder]');
    let node = this;
    while (node) {
      if ('dropFolder' in node.dataset) return node;
      node = node.parent;
    }
    return null;
  }
}

function load({ actions = {} } = {}) {
  const documentListeners = {};
  const windowListeners = {};
  const dispatched = [];
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/drag_move.js', {
    Element: FakeElement,
    document: {
      addEventListener: (type, fn) => { documentListeners[type] = fn; },
      body: null,
    },
    addEventListener: (type, fn) => { windowListeners[type] = fn; },
    dispatchEvent: (event) => { dispatched.push(event); return true; },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    setTimeout: () => 0,
    fileActions: {
      fetchActions: async (uuids) => Object.fromEntries(uuids.map((u) => [u, actions[u] || []])),
    },
  });
  return { dnd: ctx.fileDragMove, documentListeners, windowListeners, dispatched };
}

function dataTransfer() {
  const data = {};
  return {
    data,
    dropEffect: 'none',
    setData: (type, value) => { data[type] = value; },
    get types() { return Object.keys(data); },
  };
}

function dragEvent(target, dt = dataTransfer()) {
  return {
    target,
    dataTransfer: dt,
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
}

const PASTE = [{ id: 'paste_into' }];

test('the drag payload travels under its own MIME type', () => {
  const { dnd } = load();
  const dt = dataTransfer();
  dnd.start(dt, [{ uuid: 'a', name: 'report.pdf', nodeType: 'file' }], { sourceFolder: 'src' });
  assert.deepEqual(JSON.parse(dt.data[dnd.MIME]), {
    items: [{ uuid: 'a', name: 'report.pdf', nodeType: 'file' }],
    sourceFolder: 'src',
  });
  assert.equal(dnd.isDragging(), true);
  dnd.end();
  assert.equal(dnd.isDragging(), false);
});

test('a folder never receives itself nor the folder its items are already in', () => {
  const { dnd } = load();
  const drag = { items: [{ uuid: 'f1', nodeType: 'folder' }, { uuid: 'x', nodeType: 'file' }], sourceFolder: 'src' };
  assert.equal(dnd.canDrop(drag, 'f1'), false, 'onto itself');
  assert.equal(dnd.canDrop(drag, 'src'), false, 'back where it is');
  assert.equal(dnd.canDrop(drag, 'other'), true);
  assert.equal(dnd.canDrop(drag, null), true, 'the root is a folder like any other');
});

test('from a listing that is not a folder, only the items themselves are refused', () => {
  const { dnd } = load();
  const drag = { items: [{ uuid: 'f1', nodeType: 'folder' }], sourceFolder: undefined };
  assert.equal(dnd.canDrop(drag, null), true);
  assert.equal(dnd.canDrop(drag, 'f1'), false);
});

test('dragover accepts a writable folder with the move effect and highlights it', () => {
  const { dnd } = load();
  dnd.rememberActions({ dest: PASTE });
  const folder = new FakeElement({ dropFolder: 'dest' });
  const inner = new FakeElement({}, folder);

  const dt = dataTransfer();
  dnd.start(dt, [{ uuid: 'a', nodeType: 'file' }], { sourceFolder: 'src' });
  const event = dragEvent(inner, dt);
  dnd.onDragOver(event);

  assert.equal(event.prevented, true);
  assert.equal(dt.dropEffect, 'move');
  assert.ok(folder.classes.has(dnd.TARGET_CLASS));

  // Moving off the target clears the highlight.
  dnd.onDragOver(dragEvent(new FakeElement({}), dt));
  assert.ok(!folder.classes.has(dnd.TARGET_CLASS));
});

test('dragover refuses a folder the user cannot paste into', async () => {
  const { dnd } = load({ actions: { readonly: [{ id: 'download' }] } });
  const folder = new FakeElement({ dropFolder: 'readonly' });
  const dt = dataTransfer();
  dnd.start(dt, [{ uuid: 'a', nodeType: 'file' }], { sourceFolder: 'src' });

  // First hover asks the registry; the answer is not in yet.
  let event = dragEvent(folder, dt);
  dnd.onDragOver(event);
  assert.equal(event.prevented, false);
  assert.equal(dt.dropEffect, 'none');

  await new Promise((resolve) => setImmediate(resolve));
  event = dragEvent(folder, dt);
  dnd.onDragOver(event);
  assert.equal(event.prevented, false, 'the registry said no');
  assert.ok(!folder.classes.has(dnd.TARGET_CLASS));
});

test('a folder is asked once and accepted once the registry allows it', async () => {
  const { dnd } = load({ actions: { later: PASTE } });
  const folder = new FakeElement({ dropFolder: 'later' });
  const dt = dataTransfer();
  dnd.start(dt, [{ uuid: 'a', nodeType: 'file' }], { sourceFolder: 'src' });

  dnd.onDragOver(dragEvent(folder, dt));
  await new Promise((resolve) => setImmediate(resolve));
  const event = dragEvent(folder, dt);
  dnd.onDragOver(event);
  assert.equal(event.prevented, true);
  assert.equal(dt.dropEffect, 'move');
});

test('the root needs no permission lookup', () => {
  const { dnd } = load();
  const root = new FakeElement({ dropFolder: '' });
  const dt = dataTransfer();
  dnd.start(dt, [{ uuid: 'a', nodeType: 'file' }], { sourceFolder: 'src' });
  const event = dragEvent(root, dt);
  dnd.onDragOver(event);
  assert.equal(event.prevented, true);
});

test('the drop hands the items and the destination to the browser component', () => {
  const { dnd, dispatched } = load();
  dnd.rememberActions({ dest: PASTE });
  const folder = new FakeElement({ dropFolder: 'dest', dropFolderName: 'Reports' });
  const source = new FakeElement({});
  const dt = dataTransfer();
  const items = [{ uuid: 'a', name: 'a.txt', nodeType: 'file' }, { uuid: 'b', name: 'B', nodeType: 'folder' }];
  dnd.start(dt, items, { sourceFolder: 'src', sources: [source] });
  assert.ok(source.classes.has(dnd.SOURCE_CLASS));

  const event = dragEvent(folder, dt);
  dnd.onDrop(event);

  assert.equal(event.prevented, true);
  assert.equal(dispatched.length, 1);
  assert.equal(dispatched[0].type, dnd.EVENT);
  assert.deepEqual({ ...dispatched[0].detail, items: dispatched[0].detail.items.map((i) => ({ ...i })) }, {
    items,
    targetFolderId: 'dest',
    targetName: 'Reports',
  });
  assert.ok(!source.classes.has(dnd.SOURCE_CLASS), 'the drag is over');
  assert.equal(dnd.isDragging(), false);
});

test('a drop on a refused target dispatches nothing', () => {
  const { dnd, dispatched } = load();
  const dt = dataTransfer();
  dnd.start(dt, [{ uuid: 'f1', nodeType: 'folder' }], { sourceFolder: 'src' });
  dnd.onDrop(dragEvent(new FakeElement({ dropFolder: 'f1' }), dt));
  assert.equal(dispatched.length, 0);
});

test('the listeners live on the document, so a swapped listing keeps them', () => {
  const { documentListeners, windowListeners } = load();
  assert.deepEqual(Object.keys(documentListeners).sort(), ['dragend', 'dragover', 'drop']);
  assert.ok(windowListeners['folder-browser-replaced'], 'permissions are re-read after a swap');
});

test('isOverTarget tells the pin zone to step back only during a move drag', () => {
  const { dnd } = load();
  const folder = new FakeElement({ dropFolder: 'dest' });
  assert.equal(dnd.isOverTarget(dragEvent(folder)), false, 'nothing is being dragged');
  const dt = dataTransfer();
  dnd.start(dt, [{ uuid: 'a', nodeType: 'file' }], { sourceFolder: 'src' });
  assert.equal(dnd.isOverTarget(dragEvent(folder, dt)), true);
  assert.equal(dnd.isOverTarget(dragEvent(new FakeElement({}), dt)), false);
});
