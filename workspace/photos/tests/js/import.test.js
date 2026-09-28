const test = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

function load({ folder = null, fetchResponse = null, sortOk = () => true } = {}) {
  const added = [];
  const warnings = [];
  const requests = [];
  // The upload queue store: rows as createUploadQueue keeps them.
  const uploads = {
    items: [],
    add(entries) {
      added.push(...entries);
      for (const entry of entries) {
        uploads.items.push({ id: uploads.items.length + 1, folderId: entry.folderId, status: 'queued', uuid: null });
      }
    },
  };
  const ctx = loadScript('workspace/photos/ui/static/photos/ui/js/import.js', {
    document: {
      getElementById: (id) =>
        id === 'photos-import-folder-data' ? { textContent: JSON.stringify(folder) } : null,
    },
    location: { origin: 'https://cloud.example.org' },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      requests.push({ url, method: options.method, body: options.body });
      const ok = url.endsWith('/by-date') ? sortOk() : true;
      return { ok, json: async () => fetchResponse };
    },
    Alpine: { store: () => uploads },
    AppAlert: { warning: (message) => warnings.push(message), error: () => {} },
  });
  return { ctx, mixin: ctx.photosImportMixin(), added, warnings, requests, uploads };
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

  assert.deepEqual(requests.map(({ url, method }) => ({ url, method })), [
    { url: '/api/v1/photos/import-folder', method: 'POST' },
  ]);
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

// ── Sorting by date ──────────────────────────────────────

const SORT_API = '/api/v1/photos/import-folder/by-date';

async function importedTwo({ sortByDate, sortOk }) {
  const folder = { uuid: 'f1', name: 'Pictures', path: 'Pictures', group: null };
  const loaded = load({ fetchResponse: folder, sortOk });
  loaded.mixin.photoPrefs = { import_by_date: sortByDate };
  // Something another page queued, into another folder.
  loaded.uploads.items.push({ id: 99, folderId: 'other', status: 'done', uuid: 'elsewhere' });
  await loaded.mixin.importFiles([file('a.jpg', 'image/jpeg'), file('b.jpg', 'image/jpeg')]);
  loaded.requests.length = 0;
  return loaded;
}

test('a finished import is reported for sorting, once, and only its own rows', async () => {
  const { mixin, uploads, requests } = await importedTwo({ sortByDate: true });
  const [, a, b] = uploads.items;
  a.status = 'done';
  a.uuid = 'u-a';
  b.status = 'cancelled';

  await mixin._sortImportsByDate();
  await mixin._sortImportsByDate();

  assert.deepEqual(requests, [
    { url: SORT_API, method: 'POST', body: JSON.stringify({ files: ['u-a'] }) },
  ]);
});

test('a failed row waits for its retry before it is reported', async () => {
  const { mixin, uploads, requests } = await importedTwo({ sortByDate: true });
  const [, a, b] = uploads.items;
  a.status = 'done';
  a.uuid = 'u-a';
  b.status = 'failed';
  await mixin._sortImportsByDate();

  b.status = 'done';
  b.uuid = 'u-b';
  await mixin._sortImportsByDate();

  assert.deepEqual(
    requests.map((request) => JSON.parse(request.body).files),
    [['u-a'], ['u-b']],
  );
});

test('with the preference off nothing is reported, not even later', async () => {
  const { mixin, uploads, requests } = await importedTwo({ sortByDate: false });
  for (const row of uploads.items.slice(1)) {
    row.status = 'done';
    row.uuid = `u-${row.id}`;
  }
  mixin._sortImportsByDate();
  mixin.photoPrefs.import_by_date = true;
  mixin._sortImportsByDate();

  assert.equal(requests.length, 0);
});

test('a report the server refused goes again with the next one', async () => {
  const answers = [false, true];
  const { mixin, uploads, requests } = await importedTwo({ sortByDate: true, sortOk: () => answers.shift() });
  for (const row of uploads.items.slice(1)) {
    row.status = 'done';
    row.uuid = `u-${row.id}`;
  }

  await mixin._sortImportsByDate();
  await mixin._sortImportsByDate();
  await mixin._sortImportsByDate();

  assert.deepEqual(
    requests.map((request) => JSON.parse(request.body).files),
    [['u-2', 'u-3'], ['u-2', 'u-3']],
  );
});

test('a report on its way is not sent twice', async () => {
  const { mixin, uploads, requests } = await importedTwo({ sortByDate: true });
  const [, a] = uploads.items;
  a.status = 'done';
  a.uuid = 'u-a';

  const first = mixin._sortImportsByDate();
  const second = mixin._sortImportsByDate();
  await Promise.all([first, second]);

  assert.equal(requests.length, 1);
});
