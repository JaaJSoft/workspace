'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const POPOVER = 'workspace/common/static/ui/js/popover.js';

function load(viewport = { innerWidth: 1280, innerHeight: 800 }) {
  return loadScript(POPOVER, viewport);
}

const trigger = (rect) => ({ getBoundingClientRect: () => rect });

test('a trigger with room below opens the popover underneath it', () => {
  const ctx = load();
  const pos = ctx._computePopoverPosition(trigger({ left: 400, width: 40, top: 100, bottom: 140 }));
  assert.deepEqual({ ...pos }, { top: 148, left: 420, placement: 'bottom' });
});

test('a trigger near the bottom edge opens the popover above it', () => {
  const ctx = load();
  const pos = ctx._computePopoverPosition(trigger({ left: 400, width: 40, top: 700, bottom: 740 }));
  assert.equal(pos.placement, 'top');
  assert.equal(pos.top, 692);
});

test('the minimum space below is the caller\'s to choose', () => {
  const ctx = load();
  const rect = { left: 400, width: 40, top: 500, bottom: 540 };
  assert.equal(ctx._computePopoverPosition(trigger(rect)).placement, 'top');
  assert.equal(ctx._computePopoverPosition(trigger(rect), 240).placement, 'bottom');
});

test('the popover is kept inside the viewport horizontally', () => {
  const ctx = load();
  assert.equal(ctx._computePopoverPosition(trigger({ left: 0, width: 10, top: 0, bottom: 10 })).left, 136);
  assert.equal(ctx._computePopoverPosition(trigger({ left: 1270, width: 10, top: 0, bottom: 10 })).left, 1144);
});

test('the transform slides the popover in from its anchor side', () => {
  const ctx = load();
  const popover = { style: {} };
  ctx._applyPopoverTransform(popover, 'top', false);
  assert.deepEqual({ ...popover.style }, { opacity: '0', transform: 'translateX(-50%) translateY(calc(-100% + 6px))' });
  ctx._applyPopoverTransform(popover, 'top', true);
  assert.deepEqual({ ...popover.style }, { opacity: '1', transform: 'translateX(-50%) translateY(-100%)' });
  ctx._applyPopoverTransform(popover, 'bottom', false);
  assert.deepEqual({ ...popover.style }, { opacity: '0', transform: 'translateX(-50%) translateY(-6px)' });
});

test('setting the content replaces what the popover held', () => {
  const fragment = { parsed: '' };
  const ctx = loadScript(POPOVER, {
    document: {
      createElement: () => ({
        content: fragment,
        set innerHTML(html) { fragment.parsed = html; },
      }),
    },
  });
  const appended = [];
  const el = { textContent: 'spinner', appendChild: (node) => appended.push(node) };
  ctx._setPopoverContent(el, '<p>card</p>');
  assert.equal(el.textContent, '');
  assert.equal(fragment.parsed, '<p>card</p>');
  assert.deepEqual(appended, [fragment]);
});
