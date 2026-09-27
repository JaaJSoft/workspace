const test = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

function load({ folder = null, fetchResponse = null } = {}) {
  const added = [];
  const warnings = [];
  const requests = [];
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/import.js', {
    document: {
      getElementById: (id) =>
        id === 'photos-import-folder-data' ? { textContent: JSON.stringify(folder) } : null,
    },
    location: { origin: 'https://cloud.example.org' },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      requests.push({ url, method: options.method });
      return { ok: true, json: async () => fetchResponse };
    },
    Alpine: { store: () => ({ add: (entries) => added.push(...entries) }) },
    AppAlert: { warning: (message) => warnings.push(message), error: () => {} },
  });
  return { ctx, mixin: ctx.photosImportMixin(), added, warnings, requests };
}

const file = (name, type) => ({ name, type });

test('only photos and videos are importable', () => {
  const { ctx } = load();

  assert.equal(ctx.photosImportable(file('a.jpg', 'image/jpeg')), true);
  assert.equal(ctx.photosImportable(file('a.mov', 'video/quicktime')), true);
  assert.equal(ctx.photosImportable(file('IMG_0001.HEIC', '')), true);
  assert.equal(ctx.photosImportable(file('logo.svg', 'image/svg+xml')), false);
  assert.equal(ctx.photosImportable(file('notes.pdf', 'application/pdf')), false);
  assert.equal(ctx.photosImportable(file('archive', '')), false);
});

test('an import queues the media into the resolved folder, keeping both on a name clash', async () => {
  const folder = { uuid: 'f1', name: 'Photos', path: 'Photos', group: null };
  const { mixin, added, warnings, requests } = load({ fetchResponse: folder });

  await mixin.importFiles([file('a.jpg', 'image/jpeg'), file('b.txt', 'text/plain')]);

  assert.deepEqual(requests, [{ url: '/api/v1/photos/import-folder', method: 'POST' }]);
  assert.equal(added.length, 1);
  assert.equal(added[0].file.name, 'a.jpg');
  assert.equal(added[0].folderId, 'f1');
  assert.equal(added[0].onConflict, 'rename');
  assert.equal(warnings.length, 1);
  assert.equal(mixin.importFolder.uuid, 'f1');
});

test('nothing importable never creates the folder', async () => {
  const { mixin, added, requests } = load();

  await mixin.importFiles([file('b.txt', 'text/plain')]);

  assert.equal(requests.length, 0);
  assert.equal(added.length, 0);
});

test('the WebDAV address points at a personal import folder, encoded', () => {
  const personal = load({ folder: { uuid: 'f', name: 'Roll', path: 'Camera Roll/2024', group: null } });
  const shared = load({ folder: { uuid: 'g', name: 'Holidays', path: 'Holidays', group: 'Family' } });

  assert.equal(personal.mixin.importDavUrl(), 'https://cloud.example.org/dav/Camera%20Roll/2024/');
  assert.equal(personal.mixin.importFolderLabel(), 'Camera Roll/2024');
  assert.equal(shared.mixin.importDavUrl(), '');
  assert.equal(shared.mixin.importFolderLabel(), 'Holidays (Family)');
});
