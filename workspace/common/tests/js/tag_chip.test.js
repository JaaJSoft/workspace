'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('./loader');

// The element itself needs a DOM and is covered by
// workspace/files/tests/e2e/test_tag_chip.py. What is testable here is the
// pill geometry and the palette both rendering paths share.
function load() {
  const noop = () => {};
  return loadScript('workspace/common/static/ui/js/tag_chip.js', {
    HTMLElement: class {},
    customElements: { get: () => undefined, define: noop },
    document: { createElement: () => ({ setAttribute: noop, addEventListener: noop }) },
  });
}

test('the palette leads with the neutral swatch and holds only CSS colors', () => {
  const { TAG_CHIP_COLORS } = load();

  assert.equal(TAG_CHIP_COLORS[0].value, '');
  const colored = TAG_CHIP_COLORS.slice(1);
  assert.ok(colored.length >= 6);
  colored.forEach((swatch) => {
    assert.match(swatch.value, /^#[0-9a-f]{6}$/);
    assert.ok(swatch.name);
  });
});

test('every swatch value is distinct', () => {
  const { TAG_CHIP_COLORS } = load();
  const values = TAG_CHIP_COLORS.map((c) => c.value);
  assert.equal(new Set(values).size, values.length);
});

test('the default pill reserves room on both sides', () => {
  const classes = load().tagChipClasses(undefined, false);

  assert.ok(classes.includes('px-2.5'));
  assert.ok(classes.includes('min-h-[26px]'));
  assert.ok(!classes.some((c) => c.startsWith('pr-1')));
});

test('a removable pill tightens its right padding for the control', () => {
  const classes = load().tagChipClasses(undefined, true);

  assert.ok(classes.includes('pl-2.5'));
  assert.ok(classes.includes('pr-1'));
  assert.ok(!classes.includes('px-2.5'));
});

test('the sm pill is shorter, in both variants', () => {
  const chip = load();
  const plain = chip.tagChipClasses('sm', false);
  const removable = chip.tagChipClasses('sm', true);

  assert.ok(plain.includes('min-h-[20px]'));
  assert.ok(plain.includes('px-2'));
  assert.ok(removable.includes('min-h-[20px]'));
  assert.ok(removable.includes('pl-2'));
  assert.ok(removable.includes('pr-1'));
});

test('a daisyUI semantic color name resolves to the theme variable', () => {
  const { tagChipColor } = load();

  assert.equal(tagChipColor('primary'), 'oklch(var(--p))');
  assert.equal(tagChipColor('secondary'), 'oklch(var(--s))');
  assert.equal(tagChipColor('accent'), 'oklch(var(--a))');
  assert.equal(tagChipColor('info'), 'oklch(var(--in))');
  assert.equal(tagChipColor('success'), 'oklch(var(--su))');
  assert.equal(tagChipColor('warning'), 'oklch(var(--wa))');
  assert.equal(tagChipColor('error'), 'oklch(var(--er))');
});

test('ghost and empty both mean the neutral pill', () => {
  const { tagChipColor } = load();

  assert.equal(tagChipColor('ghost'), '');
  assert.equal(tagChipColor(''), '');
  assert.equal(tagChipColor(null), '');
  assert.equal(tagChipColor('  '), '');
});

test('a CSS color passes through untouched', () => {
  const { tagChipColor } = load();

  assert.equal(tagChipColor('#ef4444'), '#ef4444');
  assert.equal(tagChipColor(' #ef4444 '), '#ef4444');
  assert.equal(tagChipColor('rgb(1, 2, 3)'), 'rgb(1, 2, 3)');
});
