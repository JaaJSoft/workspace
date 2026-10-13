const test = require('node:test');
const assert = require('node:assert/strict');

const { loadScript } = require('../../../common/tests/js/loader');

// Back and forward belong to navHistory; folderNav moves between listings
// by clicking two hidden links. Stub both, and record every click.
function load({ url = '/files/shared/tok', canGoBack = false, canGoForward = false, containers = {} } = {}) {
  const clicks = [];
  const moves = [];
  const links = {
    'folder-nav-push': { href: '', click() { clicks.push(['push', this.href]); } },
    'folder-nav-replace': { href: '', click() { clicks.push(['replace', this.href]); } },
  };
  const window = new EventTarget();
  const [pathname, search = ''] = url.split(/(?=\?)/);
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/folder_nav.js', {
    location: { pathname, search },
    addEventListener: window.addEventListener.bind(window),
    dispatchEvent: window.dispatchEvent.bind(window),
    Event,
    navHistory: {
      canGoBack: () => canGoBack,
      canGoForward: () => canGoForward,
      back: () => moves.push('back'),
      forward: () => moves.push('forward'),
    },
    document: { getElementById: (id) => links[id] || containers[id] || null },
  });
  const restore = (stale) => {
    const event = new CustomEvent('nav:restore', { cancelable: true, detail: { stale } });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  };
  return { ctx, nav: ctx.folderNav, clicks, moves, restore };
}

test('back and forward are the browser history', () => {
  const { nav, moves } = load({ canGoBack: true });

  assert.equal(nav.canGoBack(), true);
  assert.equal(nav.canGoForward(), false);
  nav.back();
  nav.forward();
  assert.deepEqual(moves, ['back', 'forward']);
});

test('a step onto another listing swaps it in through the replace link', () => {
  const { restore, clicks } = load({ url: '/files/a?view=grid' });

  assert.equal(restore(true), true);
  assert.deepEqual(clicks, [['replace', '/files/a?view=grid']]);
});

test('a step that keeps the listing on screen fetches nothing', () => {
  const { restore, clicks } = load();

  assert.equal(restore(false), false);
  assert.equal(clicks.length, 0);
});

test('reload re-fetches the listing the address bar shows', () => {
  const { nav, clicks } = load({ url: '/files/b' });

  nav.reload();

  assert.deepEqual(clicks, [['replace', '/files/b']]);
});

test('navigateTo goes through the push link and ignores an empty target', () => {
  const { nav, clicks } = load();

  nav.navigateTo('');
  assert.equal(clicks.length, 0);
  nav.navigateTo('/up');
  assert.deepEqual(clicks, [['push', '/up']]);
});

test('navButtons follows the container it was given and every history change', () => {
  let canGoBack = false;
  const { ctx } = load({ containers: { 'shared-content': { dataset: { parentUrl: '/parent' } } } });
  ctx.navHistory.canGoBack = () => canGoBack;
  const buttons = ctx.navButtons('shared-content');
  buttons.init();
  assert.equal(buttons.parentUrl, '/parent');
  assert.equal(buttons.canGoBack, false);

  canGoBack = true;
  ctx.dispatchEvent(new Event('nav:change'));

  assert.equal(buttons.canGoBack, true);
});
