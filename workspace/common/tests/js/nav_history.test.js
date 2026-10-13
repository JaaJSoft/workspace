'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const SCRIPT = 'workspace/common/static/ui/js/nav_history.js';
const ORIGIN = 'https://app.test';

// A browser tab's session history: entries, a cursor, and popstate on
// every move. A reload runs the script again on the same entries, as a new
// page load would.
class Tab {
  constructor(path, { state = null, referrer = '', behind = 0 } = {}) {
    this.entries = [
      ...Array.from({ length: behind }, () => ({ url: 'https://elsewhere.test/', state: null })),
      { url: ORIGIN + path, state },
    ];
    this.index = behind;
    this.referrer = referrer;
    this.reloads = 0;
    this.load();
  }

  load() {
    const tab = this;
    const window = new EventTarget();
    const document = new EventTarget();
    document.referrer = this.referrer;
    this.window = window;
    this.document = document;
    this.restores = [];
    window.addEventListener('nav:restore', (e) => {
      this.restores.push(e.detail);
      if (this.onRestore) this.onRestore(e);
    });
    const history = {
      get length() {
        return tab.entries.length;
      },
      get state() {
        return tab.entry.state;
      },
      pushState(state, _title, url) {
        tab.entries.splice(tab.index + 1);
        tab.entries.push({ url: tab.resolve(url), state: structuredClone(state) });
        tab.index += 1;
      },
      replaceState(state, _title, url) {
        tab.entries[tab.index] = { url: tab.resolve(url), state: structuredClone(state) };
      },
      back: () => this.go(-1),
      forward: () => this.go(1),
    };
    const location = {
      origin: ORIGIN,
      get pathname() {
        return new URL(tab.entry.url).pathname;
      },
      get search() {
        return new URL(tab.entry.url).search;
      },
      get href() {
        return tab.entry.url;
      },
      reload: () => this.reload(),
    };
    this.ctx = loadScript(SCRIPT, {
      history,
      location,
      document,
      URL,
      CustomEvent,
      addEventListener: window.addEventListener.bind(window),
      dispatchEvent: window.dispatchEvent.bind(window),
    });
    this.nav = this.ctx.navHistory;
    this.history = history;
  }

  reload() {
    this.reloads += 1;
    this.load();
  }

  get entry() {
    return this.entries[this.index];
  }

  get path() {
    const url = new URL(this.entry.url);
    return url.pathname + url.search;
  }

  resolve(url) {
    return url === undefined ? this.entry.url : new URL(url, this.entry.url).href;
  }

  go(delta) {
    const next = this.index + delta;
    if (next < 0 || next >= this.entries.length) return;
    this.index = next;
    const event = new Event('popstate');
    event.state = this.entry.state;
    this.window.dispatchEvent(event);
  }

  // What alpine-ajax does when it records a swap.
  ajaxPush(url) {
    this.history.pushState({ __ajax: true }, '', url);
  }

  click(link) {
    const event = new Event('click', { cancelable: true });
    Object.defineProperty(event, 'target', { value: { closest: () => link } });
    event.button = 0;
    this.document.dispatchEvent(event);
    return event.defaultPrevented;
  }
}

test('the first page loaded has nothing behind it', () => {
  const tab = new Tab('/photos');

  assert.equal(tab.nav.canGoBack(), false);
  assert.equal(tab.nav.canGoForward(), false);
});

test('a page reached from another page of the app has it behind', () => {
  const tab = new Tab('/photos?q=dog', { referrer: `${ORIGIN}/files?folder=1`, behind: 1 });

  assert.equal(tab.nav.canGoBack(), true);
  assert.equal(tab.nav.returnTo('/files?folder=1'), true);
});

test('a page opened from outside or in a new tab has nothing behind', () => {
  for (const options of [
    { referrer: 'https://elsewhere.test/', behind: 1 },
    { referrer: `${ORIGIN}/files`, behind: 0 },
  ]) {
    assert.equal(new Tab('/photos', options).nav.canGoBack(), false);
  }
});

test('going back to the first page after a swap reloads it', () => {
  const tab = new Tab('/photos');
  tab.ajaxPush('/photos?similar=1');

  tab.nav.back();

  assert.equal(tab.path, '/photos');
  assert.equal(tab.reloads, 1);
  assert.equal(tab.nav.canGoBack(), false);
});

test('a module that refreshes the stale content itself spares the reload', () => {
  const tab = new Tab('/photos');
  tab.onRestore = (e) => {
    if (e.detail.stale) e.preventDefault();
  };
  tab.ajaxPush('/photos?album=1');

  tab.nav.back();

  assert.equal(tab.reloads, 0);
  assert.equal(tab.restores.at(-1).stale, true);
  // The content now matches this entry: forward is the stale one.
  tab.nav.forward();
  assert.equal(tab.restores.at(-1).stale, true);
});

test('an entry pushed by a module keeps the content and restores without reload', () => {
  const tab = new Tab('/people');
  tab.nav.push('/people?person=1', { person: '1' });

  tab.nav.back();

  assert.equal(tab.reloads, 0);
  assert.deepEqual({ ...tab.restores.at(-1).state }, {});
  assert.equal(tab.restores.at(-1).stale, false);
  tab.nav.forward();
  assert.deepEqual({ ...tab.restores.at(-1).state }, { person: '1' });
});

test('closing a panel the module opened steps back onto the list', () => {
  const tab = new Tab('/people?q=a');
  tab.nav.push('/people?q=a&person=1');

  assert.equal(tab.nav.returnTo('/people?q=a'), true);

  assert.equal(tab.path, '/people?q=a');
  assert.equal(tab.entries.length, 2);
});

test('closing a panel the page was opened on rewrites the entry', () => {
  const tab = new Tab('/people?person=1');

  assert.equal(tab.nav.returnTo('/people'), false);

  assert.equal(tab.path, '/people');
  assert.equal(tab.entries.length, 1);
  assert.equal(tab.restores.length, 0);
});

test('closing after switching panels in place still lands on the list', () => {
  const tab = new Tab('/people');
  tab.nav.push('/people?person=1');
  tab.nav.replace('/people?person=2');

  assert.equal(tab.nav.returnTo('/people'), true);
  assert.equal(tab.path, '/people');
});

test('forward is known after going back, and forgotten by a push', () => {
  const tab = new Tab('/files');
  tab.onRestore = (e) => e.preventDefault();
  tab.ajaxPush('/files/a');
  tab.ajaxPush('/files/a/b');

  tab.nav.back();
  tab.nav.back();
  assert.equal(tab.nav.canGoForward(), true);
  tab.nav.forward();
  assert.equal(tab.nav.canGoForward(), true);

  tab.ajaxPush('/files/c');
  assert.equal(tab.nav.canGoForward(), false);
  assert.equal(tab.nav.canGoBack(), true);
});

test('a reload keeps the place in the history, not the content', () => {
  const tab = new Tab('/files');
  tab.nav.push('/files?open=1');
  tab.reload();

  assert.equal(tab.nav.canGoBack(), true);
  tab.nav.back();
  // Written by the previous page load: whatever is on screen is not it.
  assert.equal(tab.reloads, 2);
  assert.equal(tab.path, '/files');
  assert.equal(tab.nav.canGoForward(), true);
});

test('replace keeps the depth and drops the previous module state', () => {
  const tab = new Tab('/calendar');
  tab.nav.push('/calendar?event=1', { event: '1' });

  tab.nav.replace('/calendar?event=2', { event: '2' });

  assert.equal(tab.nav.canGoBack(), true);
  assert.deepEqual({ ...tab.nav.state() }, { event: '2' });
});

test('a back link goes back when an in-app page is behind it', () => {
  const tab = new Tab('/photos?album=1');
  tab.ajaxPush('/photos?similar=1');

  assert.equal(tab.click({}), true);

  assert.equal(tab.path, '/photos?album=1');
});

test('a back link on the first page follows its own href', () => {
  const tab = new Tab('/photos?similar=1');

  assert.equal(tab.click({}), false);

  assert.equal(tab.path, '/photos?similar=1');
});

test('a click outside a back link is left alone', () => {
  const tab = new Tab('/photos');
  tab.ajaxPush('/photos?album=1');

  assert.equal(tab.click(null), false);
  assert.equal(tab.path, '/photos?album=1');
});
