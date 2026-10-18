'use strict';

// The shell every Files viewer presentation spreads (file_viewer.js): what an
// openFile does once its panel load settles, when another open went out
// meanwhile.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function makeShell() {
  const video = { currentTime: 0 };
  const panel = {
    blocked: false,
    replaceChildren() {},
    querySelector(selector) {
      if (selector === 'video') return video;
      if (selector === '[data-viewer-blocked]') return this.blocked ? {} : null;
      return null;
    },
  };
  const ctx = loadScripts(
    [
      'workspace/files/ui/static/files/ui/js/viewer_panel.js',
      'workspace/files/ui/static/files/ui/js/file_viewer.js',
    ],
    {
      CustomEvent: class {},
      dispatchEvent() {},
      document: {
        getElementById: (id) => (id === 'viewer-panel' ? panel : null),
        querySelector: () => null,
      },
    },
  );
  const loads = [];
  const shell = {
    ...ctx.fileViewerShellMixin(),
    _present() {},
    _dismiss() {},
    $ajax() {
      const load = deferred();
      loads.push(load);
      return load.promise;
    },
  };
  return { shell, loads, panel, video };
}

test('a superseded open does not seek the video the newer one shows', async () => {
  const { shell, loads, video } = makeShell();

  const first = shell.openFile('a', 'a.webm', 'video', false, 30);
  const second = shell.openFile('b', 'b.webm', 'video', false, null);
  loads[1].resolve([{}]);
  await second;
  loads[0].resolve([{}]);
  await first;

  assert.equal(shell.fileUuid, 'b');
  assert.equal(video.currentTime, 0);
});

test('a superseded open does not report the newer panel as blocked or not', async () => {
  const { shell, loads, panel } = makeShell();

  const first = shell.openFile('a', 'a.exe', 'binary', false);
  const second = shell.openFile('b', 'b.exe', 'binary', false);
  panel.blocked = true;
  loads[1].resolve([{}]);
  await second;
  assert.equal(shell.viewerBlocked, true);
  // The first file's answer lands once the second one's panel was cleared
  // for a reload: it must not speak for it.
  panel.blocked = false;
  loads[0].resolve([{}]);
  await first;

  assert.equal(shell.viewerBlocked, true);
});

test('an open the viewer was closed under leaves the closed state alone', async () => {
  const { shell, loads, video } = makeShell();

  const opening = shell.openFile('a', 'a.webm', 'video', false, 30);
  shell._onClosed();
  loads[0].resolve([{}]);
  await opening;

  assert.equal(shell.open, false);
  assert.equal(video.currentTime, 0);
});

test('the open that wins applies its start time and blocked state', async () => {
  const { shell, loads, panel, video } = makeShell();
  panel.blocked = true;

  const opening = shell.openFile('a', 'a.webm', 'video', false, 30);
  loads[0].resolve([{}]);
  await opening;

  assert.equal(video.currentTime, 30);
  assert.equal(shell.viewerBlocked, true);
});
