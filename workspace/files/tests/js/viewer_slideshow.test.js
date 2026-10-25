'use strict';

// The Files viewer's slideshow (viewer_slideshow.js): when each kind of file
// hands over to the next, what it skips, where it stops and which keys it
// answers.

const assert = require('node:assert');
const { test, beforeEach, afterEach, mock } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

class FakeElement extends EventTarget {
  constructor(props = {}) {
    super();
    Object.assign(this, props);
  }
}

function makePanel({ media = null, img = null, covered = false } = {}) {
  return {
    querySelector(selector) {
      if (selector === 'video, audio') return media;
      if (selector === 'img') return img;
      if (selector === '.sensitive-cover') return covered ? {} : null;
      return null;
    },
  };
}

function makeHost({ uuids = ['a', 'b', 'c'], blurSensitive = false, storage = {} } = {}) {
  const doc = new FakeElement({
    hidden: false,
    fullscreenElement: null,
    documentElement: { matches: () => blurSensitive },
    getElementById: () => host.panel,
    exitFullscreen() {
      this.fullscreenElement = null;
      exits.push(true);
      return Promise.resolve();
    },
  });
  const exits = [];
  const preloads = [];
  const win = new FakeElement();
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/viewer_slideshow.js', {
    document: doc,
    addEventListener: win.addEventListener.bind(win),
    removeEventListener: win.removeEventListener.bind(win),
    localStorage: {
      getItem: (key) => (key in storage ? storage[key] : null),
      setItem: (key, value) => { storage[key] = value; },
    },
    // Resolved at call time, so the test's mocked timers drive the script.
    setTimeout: (...args) => setTimeout(...args),
    clearTimeout: (id) => clearTimeout(id),
    Image: class {
      set src(url) { preloads.push(url); }
    },
  });
  const frame = {
    requestFullscreen() {
      doc.fullscreenElement = frame;
      return Promise.resolve();
    },
  };
  const host = {
    ...ctx.viewerSlideshowMixin(),
    slideshowEnabled: true,
    open: true,
    panelEditing: false,
    viewerLoading: false,
    fileUuid: uuids[0],
    uuids,
    panel: makePanel(),
    steps: [],
    $refs: { frame },
    _openEvent: 'open-file-viewer',
    _viewerPanelId: () => 'viewer-panel',
    _getViewableFiles() {
      return this.uuids.map((uuid) => ({ uuid, name: uuid, fileType: 'image/jpeg' }));
    },
    _totalViewable() {
      return this.uuids.length;
    },
    _row: () => null,
    _on(target, type, handler) {
      target.addEventListener(type, handler);
    },
    // As the shell does: the next file opens, and its panel merges later.
    navigateNext() {
      const idx = this.uuids.indexOf(this.fileUuid);
      this.fileUuid = this.uuids[idx + 1];
      this.steps.push(this.fileUuid);
    },
  };
  return { host, doc, win, exits, preloads, storage };
}

beforeEach(() => mock.timers.enable({ apis: ['setTimeout'] }));
afterEach(() => mock.timers.reset());

test('an image hands over once its interval has passed', () => {
  const { host } = makeHost();
  host.startSlideshow();

  mock.timers.tick(4999);
  assert.deepEqual(host.steps, []);
  mock.timers.tick(1);
  assert.deepEqual(host.steps, ['b']);
});

test('the countdown starts when the image has loaded, not when its panel merged', () => {
  const { host } = makeHost();
  const img = new FakeElement({ complete: false });
  host.panel = makePanel({ img });
  host.startSlideshow();

  mock.timers.tick(10000);
  assert.deepEqual(host.steps, []);
  img.dispatchEvent(new Event('load'));
  mock.timers.tick(5000);
  assert.deepEqual(host.steps, ['b']);
});

test('a manual step restarts the countdown for the file it lands on', () => {
  const { host } = makeHost();
  host.startSlideshow();
  mock.timers.tick(4000);
  host.fileUuid = 'b';
  host._onShown(host.panel);

  mock.timers.tick(4000);
  assert.deepEqual(host.steps, []);
  mock.timers.tick(1000);
  assert.deepEqual(host.steps, ['c']);
});

test('a playing video holds its turn until it ends', () => {
  const { host } = makeHost();
  const media = new FakeElement({ tagName: 'VIDEO', readyState: 1, videoWidth: 640, paused: false, currentTime: 2, ended: false, error: null });
  host.panel = makePanel({ media });
  host.startSlideshow();

  mock.timers.tick(60000);
  assert.deepEqual(host.steps, []);
  media.dispatchEvent(new Event('ended'));
  assert.deepEqual(host.steps, ['b']);
});

test('a video the browser would not start gets an image turn', () => {
  const { host } = makeHost();
  const media = new FakeElement({ tagName: 'VIDEO', readyState: 0, paused: true, currentTime: 0, ended: false, error: null });
  host.panel = makePanel({ media });
  host.startSlideshow();

  mock.timers.tick(5000);
  assert.deepEqual(host.steps, ['b']);
});

test('a video with no picture gets an image turn', () => {
  const { host } = makeHost();
  const media = new FakeElement({ tagName: 'VIDEO', readyState: 0, videoWidth: 0, paused: false, currentTime: 1, ended: false, error: null });
  host.panel = makePanel({ media });
  host.startSlideshow();
  media.dispatchEvent(new Event('loadedmetadata'));

  mock.timers.tick(5000);
  assert.deepEqual(host.steps, ['b']);
});

test('a file behind its sensitive cover is skipped', () => {
  const { host } = makeHost({ blurSensitive: true });
  host.panel = makePanel({ covered: true });
  host.startSlideshow();

  mock.timers.tick(0);
  assert.deepEqual(host.steps, ['b']);
});

test('a sensitive file the viewer shows uncovered gets its turn', () => {
  const { host } = makeHost({ blurSensitive: false });
  host.panel = makePanel({ covered: true });
  host.startSlideshow();

  mock.timers.tick(0);
  assert.deepEqual(host.steps, []);
});

test('the last file waits a turn for the next page, then the slideshow stops', async () => {
  const { host, exits } = makeHost({ uuids: ['a', 'b'] });
  host.fileUuid = 'b';
  host.startSlideshow();
  await Promise.resolve();

  mock.timers.tick(5000);
  assert.equal(host.slideshowPlaying, true);
  mock.timers.tick(5000);
  assert.equal(host.slideshowPlaying, false);
  assert.deepEqual(host.steps, []);
  assert.equal(exits.length, 1);
});

test('a page loaded during the wait carries the slideshow on', () => {
  const { host } = makeHost({ uuids: ['a', 'b'] });
  host.fileUuid = 'b';
  host.startSlideshow();

  mock.timers.tick(5000);
  host.uuids = ['a', 'b', 'c'];
  mock.timers.tick(5000);
  assert.deepEqual(host.steps, ['c']);
  assert.equal(host.slideshowPlaying, true);
});

test('a hidden tab holds the slideshow, and showing it again restarts the turn', () => {
  const { host, doc } = makeHost();
  host.startSlideshow();
  host._listenSlideshow();

  doc.hidden = true;
  doc.dispatchEvent(new Event('visibilitychange'));
  mock.timers.tick(20000);
  assert.deepEqual(host.steps, []);
  doc.hidden = false;
  doc.dispatchEvent(new Event('visibilitychange'));
  mock.timers.tick(5000);
  assert.deepEqual(host.steps, ['b']);
});

test('leaving full screen stops the slideshow', async () => {
  const { host, doc } = makeHost();
  host._listenSlideshow();
  host.startSlideshow();
  await Promise.resolve();

  doc.fullscreenElement = null;
  doc.dispatchEvent(new Event('fullscreenchange'));
  assert.equal(host.slideshowPlaying, false);
  mock.timers.tick(10000);
  assert.deepEqual(host.steps, []);
});

test('stopping leaves the full screen the slideshow entered', async () => {
  const { host, exits } = makeHost();
  host.startSlideshow();
  await Promise.resolve();

  host.stopSlideshow();
  assert.equal(exits.length, 1);
});

test('the slideshow does not start on a gallery of one, nor while an image is edited', () => {
  const single = makeHost({ uuids: ['a'] }).host;
  single.startSlideshow();
  assert.equal(single.slideshowPlaying, false);

  const editing = makeHost().host;
  editing.panelEditing = true;
  editing.startSlideshow();
  assert.equal(editing.slideshowPlaying, false);
});

test('Space plays and pauses, Escape stops, a focused control keeps its keys', () => {
  const { host } = makeHost();
  const key = (k, target = { closest: () => null }) => {
    const event = { key: k, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    return { handled: host._slideshowKeydown(event), event };
  };

  assert.equal(key(' ').handled, true);
  assert.equal(host.slideshowPlaying, true);
  assert.equal(key(' ', { closest: () => ({}) }).handled, false);
  assert.equal(host.slideshowPlaying, true);
  assert.equal(key('Escape').handled, true);
  assert.equal(host.slideshowPlaying, false);
  assert.equal(key('Escape').handled, false, 'Escape closes the viewer when nothing plays');
});

test('the interval is remembered, and only the offered ones are taken', () => {
  const { host, storage } = makeHost();
  host.setSlideshowInterval(10);
  host.setSlideshowInterval(7);

  assert.equal(host.slideshowInterval, 10);
  assert.equal(storage['files.slideshowInterval'], '10');
  assert.equal(makeHost({ storage }).host.slideshowInterval, 10);
  assert.equal(makeHost({ storage: { 'files.slideshowInterval': '42' } }).host.slideshowInterval, 5);
});

test('the next image is preloaded from its content endpoint', () => {
  const { host, preloads } = makeHost();
  host.startSlideshow();

  assert.deepEqual(preloads, ['/api/v1/files/b/content']);
});

test('an opener asking for the slideshow starts it on the file it opened', () => {
  const { host, win } = makeHost();
  host._listenSlideshow();

  win.dispatchEvent(Object.assign(new Event('open-file-viewer'), { detail: { uuid: 'a' } }));
  assert.equal(host.slideshowPlaying, false);
  win.dispatchEvent(Object.assign(new Event('open-file-viewer'), { detail: { uuid: 'a', slideshow: true } }));
  assert.equal(host.slideshowPlaying, true);
});
