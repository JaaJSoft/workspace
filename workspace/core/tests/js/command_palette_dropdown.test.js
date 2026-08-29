'use strict';

// The command palette's "actions only" mode: a query starting with `>` lists
// the workspace commands alone, filtered on the client from the JSON every
// page already embeds, and never asks /api/v1/search for files.
const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const COMMANDS = [
  { name: 'Notes', keywords: ['notes', 'markdown'], icon: 'notebook-pen', color: 'success', url: '/notes', kind: 'navigate', module_slug: 'notes', order: 1 },
  { name: 'New note', keywords: ['new note', 'create note'], icon: 'file-plus', color: 'success', url: '/notes?action=new', kind: 'action', module_slug: 'notes', order: 2 },
  { name: "Today's journal", keywords: ['journal', 'daily', 'diary'], icon: 'book-open', color: 'success', url: '/notes?view=journal', kind: 'action', module_slug: 'notes', order: 3 },
  { name: 'Mail', keywords: ['mail', 'inbox'], icon: 'mail', color: 'info', url: '/mail', kind: 'navigate', module_slug: 'mail', order: 4 },
];

// Arrays built inside the vm carry that realm's prototypes; normalize before
// comparing them with test-side literals.
const names = (palette) => Array.from(palette.commands, (c) => c.name);

class FakeInput {
  constructor() {
    this.value = '';
    this.focused = false;
    this.selected = false;
    this.selection = null;
    this.events = [];
  }
  focus() { this.focused = true; }
  select() { this.selected = true; }
  setSelectionRange(start, end) { this.selection = [start, end]; }
  dispatchEvent(event) { this.events.push(event.type); }
}

function boot({ fetchImpl } = {}) {
  const listeners = {};
  const input = new FakeInput();
  const fetchCalls = [];
  const ctx = loadScript('workspace/core/static/core/js/command_palette_dropdown.js', {
    document: {
      getElementById: (id) => (id === 'workspace-commands' ? { textContent: JSON.stringify(COMMANDS) } : null),
      querySelector: () => ({ querySelector: () => input }),
      addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    },
    localStorage: { getItem: () => null, setItem: () => {} },
    CustomEvent: class { constructor(type) { this.type = type; } },
    fetch: (url) => { fetchCalls.push(url); return fetchImpl ? fetchImpl(url) : new Promise(() => {}); },
  });
  const palette = ctx.commandPaletteDropdown();
  palette.$watch = () => {};
  palette.$nextTick = (fn) => fn();
  palette.$refs = { input };
  palette.init();
  const keydown = (init) => {
    const event = { key: 'k', ctrlKey: false, metaKey: false, shiftKey: false, prevented: false, ...init };
    event.preventDefault = () => { event.prevented = true; };
    for (const fn of listeners.keydown) fn(event);
    return event;
  };
  return { palette, input, fetchCalls, keydown };
}

describe('command mode detection', () => {
  let palette;
  beforeEach(() => { ({ palette } = boot()); });

  test('a query starting with > is command mode, whatever follows', () => {
    for (const query of ['>', '> ', '>notes', '>  new note']) {
      palette.query = query;
      assert.equal(palette.isCommandMode(), true, JSON.stringify(query));
    }
  });

  test('an ordinary query is not', () => {
    for (const query of ['', 'notes', 'a > b']) {
      palette.query = query;
      assert.equal(palette.isCommandMode(), false, JSON.stringify(query));
    }
  });

  test('the term drops the prefix and surrounding whitespace', () => {
    palette.query = '>  new note ';
    assert.equal(palette.commandTerm(), 'new note');
    palette.query = '>';
    assert.equal(palette.commandTerm(), '');
  });
});

describe('searching in command mode', () => {
  test('a bare > lists every command without touching the network', () => {
    const { palette, fetchCalls } = boot();
    palette.query = '>';
    palette.search();
    assert.deepEqual(names(palette), ['Notes', 'New note', "Today's journal", 'Mail']);
    assert.deepEqual(Array.from(palette.results), []);
    assert.equal(palette.loading, false);
    assert.deepEqual(fetchCalls, []);
  });

  test('filters on the name first, then on the keywords, like the server registry', () => {
    const { palette } = boot();
    palette.query = '>note';
    palette.search();
    // "Notes" and "New note" match by name; no other command mentions notes.
    assert.deepEqual(names(palette), ['Notes', 'New note']);

    palette.query = '> diary';
    palette.search();
    assert.deepEqual(names(palette), ["Today's journal"]);
    assert.equal(palette.searchQuery, 'diary');
  });

  test('a single character is enough - no two-character minimum', () => {
    const { palette, fetchCalls } = boot();
    palette.query = '>m';
    palette.search();
    assert.deepEqual(names(palette), ['Mail', 'Notes']);
    assert.deepEqual(fetchCalls, []);
  });

  test('matching is case-insensitive', () => {
    const { palette } = boot();
    palette.query = '>JOURNAL';
    palette.search();
    assert.deepEqual(names(palette), ["Today's journal"]);
  });

  test('an ordinary query still goes to the unified search endpoint', () => {
    const { palette, fetchCalls } = boot();
    palette.query = 'notes';
    palette.search();
    assert.deepEqual(fetchCalls, ['/api/v1/search?q=notes']);
    assert.equal(palette.loading, true);
  });

  test('a stale unified-search response cannot overwrite the command list', async () => {
    let resolveFetch;
    const { palette } = boot({
      fetchImpl: () => new Promise((resolve) => { resolveFetch = resolve; }),
    });
    palette.query = 'notes';
    palette.search();
    palette.query = '>';
    palette.search();
    resolveFetch({ json: async () => ({ commands: [], results: [{ uuid: 'x' }] }) });
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(palette.commands.length, COMMANDS.length);
    assert.deepEqual(Array.from(palette.results), []);
    assert.equal(palette.loading, false);
  });
});

describe('keyboard navigation in command mode', () => {
  test('the item count is the command list, and quick actions are never active', () => {
    const { palette } = boot();
    palette.open = true;
    palette.query = '>';
    palette.search();
    assert.equal(palette.getItemCount(), COMMANDS.length);
    palette.activeIndex = 0;
    assert.equal(palette.isCommandActive(0), true);
    assert.equal(palette.isQuickActionActive(0), false);
    assert.equal(palette.isResultActive(0), false);
  });

  test('the panels: results are hidden, quick actions are hidden, commands are shown', () => {
    const { palette } = boot();
    palette.query = '>';
    assert.equal(palette.showQuickActions(), false);
    assert.equal(palette.showResults(), true);
    palette.query = '';
    assert.equal(palette.showQuickActions(), true);
    assert.equal(palette.showResults(), false);
    palette.query = 'n';
    assert.equal(palette.showQuickActions(), false);
    assert.equal(palette.showResults(), false);
    palette.query = 'no';
    assert.equal(palette.showResults(), true);
  });
});

describe('the shortcuts', () => {
  test('Ctrl+K focuses the input and selects its content', () => {
    const { input, keydown } = boot();
    const event = keydown({ ctrlKey: true });
    assert.equal(event.prevented, true);
    assert.equal(input.focused, true);
    assert.equal(input.selected, true);
    assert.deepEqual(input.events, []);
  });

  test('Ctrl+Shift+K asks the palette owning the input for command mode', () => {
    const { input, keydown } = boot();
    // With Shift held the browser reports the upper-case key.
    const event = keydown({ ctrlKey: true, shiftKey: true, key: 'K' });
    assert.equal(event.prevented, true);
    assert.deepEqual(input.events, ['palette:commands']);
  });

  test('Cmd+Shift+K works the same on a Mac', () => {
    const { input, keydown } = boot();
    keydown({ metaKey: true, shiftKey: true, key: 'K' });
    assert.deepEqual(input.events, ['palette:commands']);
  });

  test('a bare K or an unrelated chord is left alone', () => {
    const { input, keydown } = boot();
    assert.equal(keydown({}).prevented, false);
    assert.equal(keydown({ ctrlKey: true, key: 'j' }).prevented, false);
    assert.equal(input.focused, false);
    assert.deepEqual(input.events, []);
  });

  test('entering command mode pre-fills > and puts the caret after it', () => {
    const { palette, input } = boot();
    palette.enterCommandMode();
    assert.equal(palette.query, '>');
    assert.equal(palette.open, true);
    assert.equal(palette.commands.length, COMMANDS.length);
    assert.equal(input.focused, true);
    assert.deepEqual(input.selection, [1, 1]);
  });
});
