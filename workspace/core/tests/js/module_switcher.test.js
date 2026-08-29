'use strict';

// Keyboard navigation of the navbar module switcher grid: arrows move across
// a fixed-column grid, letters jump to the next tile whose name starts with
// them, both wrapping where it makes sense. The index -1 stands for the
// trigger itself, which only ever moves into the first tile.
const { test, describe } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const nav = loadScript('workspace/core/static/core/js/module_switcher.js').moduleSwitcherNav;

describe('nextIndex', () => {
  test('arrows wrap horizontally', () => {
    assert.equal(nav.nextIndex(0, 'ArrowRight', 7, 4), 1);
    assert.equal(nav.nextIndex(6, 'ArrowRight', 7, 4), 0);
    assert.equal(nav.nextIndex(0, 'ArrowLeft', 7, 4), 6);
  });

  test('arrows move by a row vertically and stop at the edges', () => {
    assert.equal(nav.nextIndex(1, 'ArrowDown', 7, 4), 5);
    assert.equal(nav.nextIndex(5, 'ArrowDown', 7, 4), null);
    assert.equal(nav.nextIndex(5, 'ArrowUp', 7, 4), 1);
    assert.equal(nav.nextIndex(1, 'ArrowUp', 7, 4), null);
  });

  test('home and end jump to the first and last tile', () => {
    assert.equal(nav.nextIndex(3, 'Home', 7, 4), 0);
    assert.equal(nav.nextIndex(3, 'End', 7, 4), 6);
  });

  test('the trigger only enters the grid on down or right', () => {
    assert.equal(nav.nextIndex(-1, 'ArrowDown', 7, 4), 0);
    assert.equal(nav.nextIndex(-1, 'ArrowRight', 7, 4), 0);
    assert.equal(nav.nextIndex(-1, 'ArrowUp', 7, 4), null);
    assert.equal(nav.nextIndex(-1, 'ArrowLeft', 7, 4), null);
  });

  test('other keys are ignored', () => {
    assert.equal(nav.nextIndex(2, 'Enter', 7, 4), null);
    assert.equal(nav.nextIndex(2, 'Tab', 7, 4), null);
  });
});

describe('letterIndex', () => {
  const names = ['Home', 'Files', 'Chat', 'Calendar', 'Mail', 'Notes', 'Projects'];

  test('jumps to the first tile starting with the letter', () => {
    assert.equal(nav.letterIndex(names, 'c', -1), 2);
    assert.equal(nav.letterIndex(names, 'M', -1), 4);
  });

  test('cycles between tiles sharing a letter', () => {
    assert.equal(nav.letterIndex(names, 'c', 2), 3);
    assert.equal(nav.letterIndex(names, 'c', 3), 2);
  });

  test('returns null when no tile matches', () => {
    assert.equal(nav.letterIndex(names, 'z', 0), null);
  });
});
