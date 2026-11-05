'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// Opening a shortcut opens its target by the target's own uuid, never the
// shortcut's: the server answers for the target under the viewer's access.
function load() {
  const navigated = [];
  const viewed = [];
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/file_actions.js', {
    document: { getElementById: () => null },
    getCSRFToken: () => 'token',
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
      }
    },
  });
  ctx.folderNav = { navigateTo: (url) => navigated.push(url) };
  ctx.dispatchEvent = (event) => {
    if (event.type === 'open-file-viewer') viewed.push({ ...event.detail });
  };
  return { ctx, navigated, viewed };
}

test('a folder shortcut navigates to the target folder', () => {
  const { ctx, navigated, viewed } = load();

  const opened = ctx.fileActions.openShortcutTarget({
    uuid: 'shortcut-uuid', opens: 'folder', targetUuid: 'folder-uuid',
  });

  assert.equal(opened, true);
  assert.deepStrictEqual(navigated, ['/files/folder-uuid']);
  assert.equal(viewed.length, 0);
});

test('a file shortcut opens the target in the viewer', () => {
  const { ctx, navigated, viewed } = load();

  const opened = ctx.fileActions.openShortcutTarget({
    uuid: 'shortcut-uuid',
    opens: 'viewer',
    targetUuid: 'file-uuid',
    targetName: 'report.pdf',
    targetFileType: 'pdf',
  });

  assert.equal(opened, true);
  assert.deepStrictEqual(viewed, [{ uuid: 'file-uuid', name: 'report.pdf', type: 'pdf' }]);
  assert.equal(navigated.length, 0);
});

test('a shortcut that opens nothing does nothing', () => {
  const { ctx, navigated, viewed } = load();

  // What an unavailable shortcut's row carries: no target, nothing to open.
  assert.equal(ctx.fileActions.openShortcutTarget({ uuid: 'shortcut-uuid', opens: '', targetUuid: '' }), false);
  assert.equal(ctx.fileActions.openShortcutTarget({ opens: 'viewer', targetUuid: '' }), false);
  assert.equal(ctx.fileActions.openShortcutTarget(null), false);
  assert.equal(navigated.length + viewed.length, 0);
});
