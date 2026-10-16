'use strict';

// Large files go up in chunks through an upload session. These tests drive
// sendFileUpload over a fake XMLHttpRequest answered by a scripted server,
// to pin what is sent, what is retried and where a resumed upload restarts.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

const MB = 1024 * 1024;
const SESSION = 's1';
const SESSION_URL = `/api/v1/files/uploads/${SESSION}`;

// A File stand-in: slice() records the range it was cut at.
const bigFile = (size = 100 * MB, name = 'video.mp4') => ({
  name,
  size,
  type: 'video/mp4',
  slice: (start, end) => ({ start, end }),
});

// *answer(xhr)* returns [status, body] or 'network' for a lost connection;
// answers arrive on the next macrotask, as a network's would.
function makeEnv(answer) {
  const sent = [];
  const fetched = [];
  const waits = [];

  class FakeXhr {
    constructor() {
      this.upload = {};
      this.headers = {};
    }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(name, value) { this.headers[name] = value; }
    send(body) {
      this.body = body;
      sent.push(this);
      setImmediate(() => {
        if (this.aborted) return;
        const reply = answer(this, sent);
        if (reply === 'pending') return;
        if (reply === 'network') {
          this.onerror();
          return;
        }
        const [status, data] = reply;
        this.status = status;
        this.responseText = JSON.stringify(data);
        this.onload();
      });
    }
    abort() { this.aborted = true; if (this.onabort) this.onabort(); }
  }

  const ctx = loadScripts(
    [
      'workspace/files/ui/static/files/ui/js/file_actions.js',
      'workspace/files/ui/static/files/ui/js/upload_queue.js',
    ],
    {
      document: { addEventListener: () => {} },
      XMLHttpRequest: FakeXhr,
      FormData: class { append() {} },
      getCSRFToken: () => 'token',
      fetch: async (url, options) => { fetched.push(`${options.method} ${url}`); return { ok: true }; },
      setTimeout: (fn, ms) => { waits.push(ms); setImmediate(fn); return waits.length; },
      clearTimeout: () => {},
    },
  );
  return { ctx, sent, fetched, waits };
}

// A server holding one session with *chunk* bytes per chunk, storing
// whatever each PATCH sends unless *override* answers first.
function sessionServer(file, { chunk = 40 * MB, offset = 0, override = () => null } = {}) {
  const state = { offset };
  const answer = (xhr, sent) => {
    const forced = override(xhr, state, sent);
    if (forced) return forced;
    if (xhr.method === 'POST' && xhr.url === '/api/v1/files/uploads') {
      return [201, { uuid: SESSION, offset: 0, chunk_size: chunk }];
    }
    if (xhr.method === 'GET') return [200, { uuid: SESSION, offset: state.offset, chunk_size: chunk, completed: false }];
    if (xhr.method === 'PATCH') {
      const [, start, end] = xhr.headers['Content-Range'].match(/bytes (\d+)-(\d+)/).map(Number);
      if (start !== state.offset) return [409, { offset: state.offset }];
      state.offset = end + 1;
      return [200, { offset: state.offset }];
    }
    if (xhr.method === 'POST' && xhr.url === `${SESSION_URL}/finalize`) {
      return [201, { uuid: 'f1', name: file.name, duplicates: [] }];
    }
    throw new Error(`unexpected ${xhr.method} ${xhr.url}`);
  };
  return { state, answer };
}

const ranges = (sent) => sent.filter((x) => x.method === 'PATCH').map((x) => x.headers['Content-Range']);

test('a large file goes up in chunks, then is finalized', async () => {
  const file = bigFile();
  const server = sessionServer(file);
  const { ctx, sent } = makeEnv(server.answer);
  const progress = [];

  const result = await ctx.sendFileUpload(file, { folderId: 'folder-a', onConflict: 'rename' }, (n) => progress.push(n)).promise;

  assert.equal(result.status, 201);
  assert.equal(result.body.uuid, 'f1');
  const opened = JSON.parse(sent[0].body);
  assert.deepEqual({ ...opened }, {
    name: 'video.mp4', size: file.size, parent: 'folder-a', on_conflict: 'rename', mime_type: 'video/mp4',
  });
  assert.deepEqual(ranges(sent), [
    `bytes 0-${40 * MB - 1}/${file.size}`,
    `bytes ${40 * MB}-${80 * MB - 1}/${file.size}`,
    `bytes ${80 * MB}-${file.size - 1}/${file.size}`,
  ]);
  assert.deepEqual({ ...sent[1].body }, { start: 0, end: 40 * MB });
  assert.equal(sent.at(-1).url, `${SESSION_URL}/finalize`);
  assert.equal(progress.at(-1), file.size);
});

test('a small file keeps the single request', async () => {
  const { ctx, sent } = makeEnv(() => [201, { uuid: 'f1', name: 'note.txt' }]);

  await ctx.sendFileUpload({ name: 'note.txt', size: 10 }, {}, () => {}).promise;

  assert.deepEqual(sent.map((x) => `${x.method} ${x.url}`), ['POST /api/v1/files']);
});

test('a chunk the network cut is retried after a wait, from where the server stands', async () => {
  const file = bigFile();
  let cut = false;
  const server = sessionServer(file, {
    override: (xhr, state) => {
      // The second chunk lands on the server, but its answer is lost.
      if (xhr.method === 'PATCH' && state.offset === 40 * MB && !cut) {
        cut = true;
        state.offset = 80 * MB;
        return 'network';
      }
      return null;
    },
  });
  const { ctx, sent, waits } = makeEnv(server.answer);
  const retrying = [];

  const result = await ctx.sendFileUpload(file, {}, () => {}, (flag) => retrying.push(flag)).promise;

  assert.equal(result.status, 201);
  assert.deepEqual(waits, [1000]);
  assert.ok(retrying.includes(true));
  assert.equal(retrying.at(-1), false);
  assert.deepEqual(ranges(sent), [
    `bytes 0-${40 * MB - 1}/${file.size}`,
    `bytes ${40 * MB}-${80 * MB - 1}/${file.size}`,
    `bytes ${40 * MB}-${80 * MB - 1}/${file.size}`, // answered 409: it is there already
    `bytes ${80 * MB}-${file.size - 1}/${file.size}`,
  ]);
});

test('a chunk refused while the server still writes the last attempt waits instead of spinning', async () => {
  const file = bigFile(60 * MB);
  let busy = 2;
  const server = sessionServer(file, {
    override: (xhr, state) => (xhr.method === 'PATCH' && busy-- > 0 ? [409, { offset: state.offset }] : null),
  });
  const { ctx, waits } = makeEnv(server.answer);

  const result = await ctx.sendFileUpload(file, {}, () => {}).promise;

  assert.equal(result.status, 201);
  assert.deepEqual(waits, [1000, 2000]);
});

test('a server that stays busy fails the upload once the waits run out', async () => {
  const file = bigFile(60 * MB);
  let busy = 20; // past the retries: the loop must give up, not start them over
  const server = sessionServer(file, {
    override: (xhr, state) => (xhr.method === 'PATCH' && busy-- > 0 ? [409, { offset: state.offset }] : null),
  });
  const { ctx, waits, fetched } = makeEnv(server.answer);

  await assert.rejects(ctx.sendFileUpload(file, {}, () => {}).promise, /Network error/);
  assert.equal(waits.length, 8);
  assert.deepEqual(fetched, [], 'the session is kept for a retry');
});

test('when the waits run out it fails, and a retry resumes the same session', async () => {
  const file = bigFile();
  let down = true;
  const server = sessionServer(file, {
    override: (xhr, state) => (down && xhr.method === 'PATCH' && state.offset > 0 ? 'network' : null),
  });
  const { ctx, sent, fetched } = makeEnv(server.answer);

  await assert.rejects(ctx.sendFileUpload(file, {}, () => {}).promise, /Network error/);
  assert.deepEqual(fetched, [], 'the session is kept for a retry');

  down = false;
  sent.length = 0;
  const result = await ctx.sendFileUpload(file, {}, () => {}).promise;

  assert.equal(result.status, 201);
  assert.deepEqual(sent.map((x) => x.method).slice(0, 2), ['GET', 'PATCH']);
  assert.equal(ranges(sent)[0], `bytes ${40 * MB}-${80 * MB - 1}/${file.size}`);
});

test('a name taken by the time it is finalized is a collision, and the session goes', async () => {
  const file = bigFile(60 * MB);
  const server = sessionServer(file, {
    override: (xhr) => (xhr.url.endsWith('/finalize') ? [400, { name: ['A file with the same name already exists in this folder.'] }] : null),
  });
  const { ctx, fetched } = makeEnv(server.answer);

  await assert.rejects(ctx.sendFileUpload(file, {}, () => {}).promise, (error) => error.nameCollision === true);
  assert.deepEqual(fetched, [`DELETE ${SESSION_URL}`]);
});

test('cancelling stops the transfer and drops the session', async () => {
  const file = bigFile();
  const server = sessionServer(file, {
    override: (xhr) => (xhr.method === 'PATCH' ? 'pending' : null),
  });
  const { ctx, sent, fetched } = makeEnv(server.answer);

  const upload = ctx.sendFileUpload(file, {}, () => {});
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  upload.abort();

  await assert.rejects(upload.promise, /Cancelled/);
  assert.equal(sent.at(-1).aborted, true);
  assert.deepEqual(fetched, [`DELETE ${SESSION_URL}`]);
});
