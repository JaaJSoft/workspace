'use strict';

// The timezone bookkeeping base.html runs on every authenticated page: detect
// the browser zone on first sign-in, and afterwards offer a one-click update
// when it diverges from the stored one.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

class FakeElement {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.textContent = '';
    this.className = '';
    this.classes = new Set();
    this.listeners = {};
    this.classList = {
      add: (c) => this.classes.add(c),
      remove: (c) => this.classes.delete(c),
      contains: (c) => this.classes.has(c),
    };
  }

  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); }
  click() { for (const fn of this.listeners.click || []) fn(); }
  find(text) {
    if (this.textContent === text) return this;
    for (const child of this.children) {
      const hit = child instanceof FakeElement ? child.find(text) : null;
      if (hit) return hit;
    }
    return null;
  }
}

function boot({ stored = 'UTC', detected = 'Europe/Paris', dismissed = null,
                storageThrows = false, reply = { ok: true } } = {}) {
  const banner = new FakeElement();
  banner.classes.add('hidden');
  const state = { fetches: [], warnings: [], stored: dismissed, reloaded: false };
  let resolveFetch;

  const ctx = loadScript('workspace/core/static/core/js/timezone_suggest.js', {
    getCSRFToken: () => 'token',
    fetch: (url, options) => {
      state.fetches.push({ url, options });
      if (reply === 'pending') return new Promise((r) => { resolveFetch = r; });
      return typeof reply === 'function' ? reply() : Promise.resolve(reply);
    },
    console: { warn: (...args) => state.warnings.push(args.join(' ')) },
    Intl: { DateTimeFormat: () => ({ resolvedOptions: () => ({ timeZone: detected }) }) },
    localStorage: {
      getItem: () => {
        if (storageThrows) throw new Error('storage disabled');
        return state.stored;
      },
      setItem: (_key, value) => {
        if (storageThrows) throw new Error('storage disabled');
        state.stored = value;
      },
    },
    location: { reload: () => { state.reloaded = true; } },
    document: {
      documentElement: { getAttribute: () => stored },
      addEventListener: (type, fn) => {
        if (type === 'DOMContentLoaded') state.ready = fn;
      },
      getElementById: (id) => (id === 'tz-suggest-banner' ? banner : null),
      createElement: (tag) => new FakeElement(tag),
    },
  });
  ctx.window = ctx;
  return { banner, state, settle: (value) => resolveFetch(value), ctx };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test('a first sign-in stores the zone the browser reports', async () => {
  const { state } = boot({ stored: '' });
  assert.equal(state.fetches.length, 1);
  assert.equal(state.fetches[0].url, '/api/v1/settings/core/timezone');
  assert.equal(JSON.parse(state.fetches[0].options.body).value, 'Europe/Paris');
});

test('a first sign-in the server refuses says so', async () => {
  // No recovery path is needed - the setting is still unset, so the next page
  // load tries again - but a silent failure is undiagnosable.
  const { state } = boot({ stored: '', reply: { ok: false, status: 500 } });
  await tick();
  assert.equal(state.warnings.length, 1);
  assert.match(state.warnings[0], /Timezone save failed/);
});

test('a browser that refuses storage still gets the banner', async () => {
  // getItem throws in a private window rather than returning null. Letting it
  // escape killed the whole script, banner included.
  const { banner, state } = boot({ storageThrows: true });
  assert.ok(state.ready, 'the DOMContentLoaded handler was never registered');
  state.ready();
  assert.equal(banner.classes.has('hidden'), false);
});

test('a zone already dismissed raises no banner', () => {
  const { state } = boot({ dismissed: 'Europe/Paris' });
  assert.equal(state.ready, undefined);
});

test('a matching stored zone raises no banner', () => {
  const { state } = boot({ stored: 'Europe/Paris' });
  assert.equal(state.ready, undefined);
});

test('Ignore stores the dismissal and hides the banner', () => {
  const { banner, state } = boot();
  state.ready();
  banner.find('Ignore').click();
  assert.equal(state.stored, 'Europe/Paris');
  assert.equal(banner.classes.has('hidden'), true);
});

test('Ignore does nothing while a save is in flight', async () => {
  // Otherwise the dismissal outlives the banner: a save that then fails writes
  // its error into an element nobody can see, and the prompt never returns.
  const { banner, state, settle } = boot({ reply: 'pending' });
  state.ready();
  banner.find('Update').click();
  banner.find('Ignore').click();
  assert.equal(state.stored, null, 'the dismissal was stored mid-save');
  assert.equal(banner.classes.has('hidden'), false);

  // And once that save fails, the prompt is still there to be answered.
  settle({ ok: false, status: 500 });
  await tick();
  assert.equal(banner.classes.has('hidden'), false);
  assert.equal(state.stored, null);
});

test('a failed update leaves the banner up and the message visible', async () => {
  const { banner, state } = boot({ reply: { ok: false, status: 500 } });
  state.ready();
  banner.find('Update').click();
  await tick();
  assert.equal(banner.classes.has('hidden'), false);
  assert.equal(state.reloaded, false);
  assert.ok(banner.find('Could not save your timezone. Try again?'));
});

test('a successful update reloads the page', async () => {
  const { banner, state } = boot();
  state.ready();
  banner.find('Update').click();
  await tick();
  assert.equal(state.reloaded, true);
});
