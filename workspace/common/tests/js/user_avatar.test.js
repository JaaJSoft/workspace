'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('./loader');

// The element itself needs a DOM and is covered by
// workspace/files/tests/e2e/test_user_avatar.py. What is testable here is the
// size scale, the host geometry and the palette both rendering paths share.
function load() {
  const noop = () => {};
  return loadScript('workspace/common/static/ui/js/user_avatar.js', {
    HTMLElement: class {},
    customElements: { get: () => undefined, define: noop },
    document: { createElement: () => ({ setAttribute: noop, addEventListener: noop }), addEventListener: noop },
    escapeHtml: (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
  });
}

test('userAvatarColorClass is deterministic and covers a 12-color palette', () => {
  const ctx = load();
  assert.equal(ctx.userAvatarColorClass(7), ctx.userAvatarColorClass(7));
  const palette = new Set();
  for (let id = 0; id < 12; id++) palette.add(ctx.userAvatarColorClass(id));
  assert.equal(palette.size, 12);
  for (const cls of palette) assert.match(cls, /^bg-[a-z]+-500$/);
});

test('userAvatarColorClass wraps around the palette and accepts numeric strings', () => {
  const ctx = load();
  assert.equal(ctx.userAvatarColorClass(12), ctx.userAvatarColorClass(0));
  assert.equal(ctx.userAvatarColorClass(25), ctx.userAvatarColorClass(1));
  assert.equal(ctx.userAvatarColorClass('5'), ctx.userAvatarColorClass(5));
});

test('userAvatarColorClass falls back to bg-neutral on invalid input', () => {
  const ctx = load();
  assert.equal(ctx.userAvatarColorClass(undefined), 'bg-neutral');
  assert.equal(ctx.userAvatarColorClass(null), 'bg-neutral');
  assert.equal(ctx.userAvatarColorClass(''), 'bg-neutral');
  assert.equal(ctx.userAvatarColorClass('abc'), 'bg-neutral');
  assert.equal(ctx.userAvatarColorClass(3.5), 'bg-neutral');
});

test('every size step names both a square box and a text size', () => {
  const { USER_AVATAR_SIZES } = load();
  const names = Object.keys(USER_AVATAR_SIZES);
  assert.ok(names.length >= 5);
  for (const name of names) {
    const step = USER_AVATAR_SIZES[name];
    // A non-square box would make the "circle" an ellipse and break the
    // alignment of a row mixing sizes.
    const match = /^w-(\S+) h-(\S+)$/.exec(step.box);
    assert.ok(match, `${name} box is not a w-N h-N pair: ${step.box}`);
    assert.equal(match[1], match[2], `${name} is not square`);
    assert.match(step.text, /^text-/);
  }
});

test('the size scale is strictly increasing', () => {
  const { USER_AVATAR_SIZES } = load();
  // Only the numeric steps are comparable; the arbitrary-value ones are not.
  const widths = Object.values(USER_AVATAR_SIZES)
    .map((s) => Number.parseFloat(s.box.slice(2)))
    .filter((n) => Number.isFinite(n));
  const sorted = [...widths].sort((a, b) => a - b);
  assert.deepEqual(widths, sorted);
  assert.equal(new Set(widths).size, widths.length);
});

test('the host carries the box, so a flex parent measures the avatar itself', () => {
  const ctx = load();
  const classes = ctx.userAvatarHostClasses('sm');

  assert.ok(classes.includes('w-8'));
  assert.ok(classes.includes('h-8'));
  // relative: the presence dot and the image are positioned against the host.
  assert.ok(classes.includes('relative'));
  // inline-flex + align-middle: the avatar must not sit on the text baseline
  // when it lands in a line box (dropdown rows, button labels).
  assert.ok(classes.includes('inline-flex'));
  assert.ok(classes.includes('align-middle'));
  // shrink-0: a long name next to it must never squash the circle into an oval.
  assert.ok(classes.includes('shrink-0'));
});

test('an unknown or missing size falls back to md rather than losing its box', () => {
  const ctx = load();
  const fallback = ctx.userAvatarHostClasses(undefined);

  assert.deepEqual(ctx.userAvatarHostClasses('nope'), fallback);
  assert.ok(fallback.includes('w-10'));
  assert.ok(fallback.includes('h-10'));
});

test('userAvatarTag escapes both attributes it interpolates', () => {
  const ctx = load();
  const tag = ctx.userAvatarTag(5, '"><script>alert(1)</script>', { size: 'sm' });

  assert.ok(!tag.includes('<script>'));
  assert.ok(tag.includes('&quot;'));
  assert.ok(tag.startsWith('<user-avatar '));
  assert.ok(tag.endsWith('</user-avatar>'));
});

test('userAvatarTag emits the flags only when asked', () => {
  const ctx = load();

  const plain = ctx.userAvatarTag(1, 'Bob', { size: 'sm' });
  assert.ok(!plain.includes(' presence'));
  assert.ok(!plain.includes(' card'));

  const full = ctx.userAvatarTag(1, 'Bob', { size: 'sm', presence: true, card: true });
  assert.ok(full.includes(' presence'));
  assert.ok(full.includes(' card'));
});
