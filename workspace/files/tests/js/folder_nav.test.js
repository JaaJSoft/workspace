const test = require('node:test');
const assert = require('node:assert/strict');

const { loadScript } = require('../../../common/tests/js/loader');

// The script reads the location and registers a popstate listener at load,
// and moves through history by clicking two hidden links. Stub just enough
// of the browser for that, and record every click.
function load(initialUrl = '/files/shared/tok') {
  const clicks = [];
  const links = {
    'folder-nav-push': { href: '', click() { clicks.push(['push', this.href]); } },
    'folder-nav-replace': { href: '', click() { clicks.push(['replace', this.href]); } },
  };
  const [pathname, search = ''] = initialUrl.split(/(?=\?)/);
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/folder_nav.js', {
    location: { pathname, search },
    addEventListener() {},
    dispatchEvent() {},
    Event: class {},
    document: { getElementById: (id) => links[id] || null },
  });
  return { nav: ctx.folderNav, clicks };
}

test('starts on the current URL with nothing to go back or forward to', () => {
  const { nav } = load('/files/shared/tok?view=grid');
  assert.equal(nav.canGoBack(), false);
  assert.equal(nav.canGoForward(), false);
});

test('a navigation pushes, and going back replaces without pushing', () => {
  const { nav, clicks } = load('/a');
  nav.onNavigate('/b');
  nav.onNavigate('/c');
  assert.equal(nav.canGoBack(), true);

  nav.back();
  assert.deepEqual(clicks.at(-1), ['replace', '/b']);
  // The swap target re-renders and reports the URL it landed on.
  nav.onNavigate('/b');
  assert.equal(nav.canGoBack(), true);
  assert.equal(nav.canGoForward(), true);

  nav.forward();
  assert.deepEqual(clicks.at(-1), ['replace', '/c']);
  nav.onNavigate('/c');
  assert.equal(nav.canGoForward(), false);
});

test('navigating somewhere new after going back drops the forward entries', () => {
  const { nav } = load('/a');
  nav.onNavigate('/b');
  nav.back();
  nav.onNavigate('/a');
  nav.onNavigate('/z');
  assert.equal(nav.canGoForward(), false);
  assert.equal(nav.canGoBack(), true);
});

test('a re-render of the same URL adds nothing', () => {
  const { nav } = load('/a');
  nav.onNavigate('/a');
  nav.onNavigate('/a');
  assert.equal(nav.canGoBack(), false);
});

test('reload re-fetches the current entry through the replace link', () => {
  const { nav, clicks } = load('/a');
  nav.onNavigate('/b');
  nav.reload();
  assert.deepEqual(clicks.at(-1), ['replace', '/b']);
  assert.equal(nav.canGoBack(), true);
});

test('navigateTo goes through the push link and ignores an empty target', () => {
  const { nav, clicks } = load('/a');
  nav.navigateTo('');
  assert.equal(clicks.length, 0);
  nav.navigateTo('/up');
  assert.deepEqual(clicks.at(-1), ['push', '/up']);
});

test('navButtons follows the container it was given', () => {
  const { nav } = load('/a');
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/folder_nav.js', {
    location: { pathname: '/a', search: '' },
    addEventListener() {},
    dispatchEvent() {},
    Event: class {},
    document: {
      getElementById: (id) => (id === 'shared-content' ? { dataset: { parentUrl: '/parent' } } : null),
    },
  });
  const buttons = ctx.navButtons('shared-content');
  buttons.init();
  assert.equal(buttons.parentUrl, '/parent');
  assert.equal(buttons.canGoBack, false);
  assert.ok(nav);
});
