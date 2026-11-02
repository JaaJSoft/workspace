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
// answers arrive on the next macrotask, as a network's would. 'pending'
// leaves the request in flight, for the test to drive with *sending()* and
// *respond()*.
// *timers: 'held'* keeps each wait pending until *fireTimers()* runs it,
// so a test can act while an upload waits.
// The stall watchdog runs on a clock of its own, moved by *advance(ms)*.
function makeEnv(answer, { document = {}, timers = 'immediate' } = {}) {
  const sent = [];
  const held = [];
  const fetched = [];
  const waits = [];
  const pending = [];
  const watchdogs = new Map();
  let clock = 0;
  let lastTimer = 0;

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
        if (reply === 'pending') pending.push(this);
        else this.respond(reply);
      });
    }
    respond(reply) {
      if (reply === 'network') {
        this.onerror();
        return;
      }
      const [status, data] = reply;
      this.status = status;
      this.responseText = JSON.stringify(data);
      this.onload();
    }
    // Bytes of the body handed to the network so far.
    sending(loaded, total = this.body.end - this.body.start) {
      this.upload.onprogress({ lengthComputable: true, loaded, total });
    }
    abort() { this.aborted = true; if (this.onabort) this.onabort(); }
  }

  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/uuid.js',
      'workspace/files/ui/static/files/ui/js/file_actions.js',
      'workspace/files/ui/static/files/ui/js/upload_queue.js',
    ],
    {
      document: { addEventListener: () => {}, ...document },
      XMLHttpRequest: FakeXhr,
      FormData: class { append() {} },
      getCSRFToken: () => 'token',
      fetch: async (url, options) => { fetched.push(`${options.method} ${url}`); return { ok: true }; },
      setTimeout: (fn, ms) => {
        lastTimer += 1;
        if (ms === ctx.UPLOAD_STALL_TIMEOUT) {
          watchdogs.set(lastTimer, { at: clock + ms, fn });
          return lastTimer;
        }
        waits.push(ms);
        if (timers === 'held') held.push(fn);
        else setImmediate(fn);
        return lastTimer;
      },
      clearTimeout: (id) => watchdogs.delete(id),
      crypto: { randomUUID: () => SESSION },
    },
  );
  const fireTimers = () => held.splice(0).forEach((fn) => fn());
  const advance = (ms) => {
    clock += ms;
    for (const [id, { at, fn }] of [...watchdogs]) {
      if (at > clock || !watchdogs.has(id)) continue;
      watchdogs.delete(id);
      fn();
    }
  };
  // Lets the upload run until a request left pending is in flight.
  const inFlight = async () => {
    while (!pending.length) await new Promise((resolve) => setImmediate(resolve));
    return pending.shift();
  };
  return { ctx, sent, fetched, waits, held, fireTimers, advance, watchdogs, inFlight };
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
    uuid: SESSION, name: 'video.mp4', size: file.size, parent: 'folder-a', on_conflict: 'rename', mime_type: 'video/mp4',
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

test('an open whose answer was lost is retried under the same uuid', async () => {
  const file = bigFile(60 * MB);
  const opened = [];
  const server = sessionServer(file, {
    override: (xhr) => {
      if (xhr.method !== 'POST' || xhr.url !== '/api/v1/files/uploads') return null;
      opened.push(JSON.parse(xhr.body).uuid);
      // The first open lands on the server; its answer never comes back.
      return opened.length === 1 ? 'network' : [200, { uuid: SESSION, offset: 0, chunk_size: 40 * MB }];
    },
  });
  const { ctx } = makeEnv(server.answer);

  const result = await ctx.sendFileUpload(file, {}, () => {}).promise;

  assert.equal(result.status, 201);
  assert.deepEqual(opened, [SESSION, SESSION], 'both attempts name the same session');
});

test('a connection lost while the page is hidden does not use up the waits', async () => {
  const file = bigFile(60 * MB);
  let cuts = 20; // more than the waits allow while the page is shown
  const page = { visibilityState: 'hidden' };
  const server = sessionServer(file, {
    override: (xhr) => {
      if (xhr.method !== 'PATCH' || cuts <= 0) return null;
      cuts -= 1;
      if (cuts === 0) page.visibilityState = 'visible';
      return 'network';
    },
  });
  const { ctx, waits } = makeEnv(server.answer, { document: page });

  const result = await ctx.sendFileUpload(file, {}, () => {}).promise;

  assert.equal(result.status, 201);
  assert.equal(waits.length, 20);
  assert.ok(waits.every((ms) => ms <= 30000), 'the waits stop growing at their longest');
});

test('the waits run out as usual while the page is shown', async () => {
  const file = bigFile(60 * MB);
  const server = sessionServer(file, {
    override: (xhr) => (xhr.method === 'PATCH' ? 'network' : null),
  });
  const { ctx, waits } = makeEnv(server.answer, { document: { visibilityState: 'visible' } });

  await assert.rejects(ctx.sendFileUpload(file, {}, () => {}).promise, (error) => error.transient === true);
  assert.equal(waits.length, 8);
});

test('waking an upload that waits on the connection sends it now', async () => {
  const file = bigFile(60 * MB);
  let cut = false;
  const server = sessionServer(file, {
    override: (xhr) => {
      if (xhr.method !== 'PATCH' || cut) return null;
      cut = true;
      return 'network';
    },
  });
  const { ctx, sent, held } = makeEnv(server.answer, { timers: 'held' });

  const upload = ctx.sendFileUpload(file, {}, () => {});
  while (!held.length) await new Promise((resolve) => setImmediate(resolve));
  upload.wake();

  const result = await upload.promise;
  assert.equal(result.status, 201);
  assert.equal(ranges(sent).length, 3, 'the cut chunk went again, then the last one');
});

test('a refusal is not mistaken for a lost connection', async () => {
  const file = bigFile(60 * MB);
  const server = sessionServer(file, {
    override: (xhr) => (xhr.method === 'POST' && !xhr.url.endsWith('/finalize') ? [400, { detail: 'Storage quota exceeded' }] : null),
  });
  const { ctx } = makeEnv(server.answer);

  await assert.rejects(ctx.sendFileUpload(file, {}, () => {}).promise, (error) => !error.transient);
});

test('a small file cut by the network fails as a lost connection', async () => {
  const { ctx } = makeEnv(() => 'network');

  await assert.rejects(ctx.sendFileUpload(bigFile(MB, 'note.txt'), {}, () => {}).promise, (error) => error.transient === true);
});

test('a chunk whose bytes stop going up is cut, and sent again from where the server stands', async () => {
  const file = bigFile();
  let stalled = false;
  const server = sessionServer(file, {
    override: (xhr, state) => {
      // The second chunk gets 10 MB to the server, then nothing more moves.
      if (xhr.method === 'PATCH' && state.offset === 40 * MB && !stalled) {
        stalled = true;
        state.offset = 50 * MB;
        return 'pending';
      }
      return null;
    },
  });
  const { ctx, sent, waits, advance, watchdogs, inFlight } = makeEnv(server.answer);
  const retrying = [];

  const upload = ctx.sendFileUpload(file, {}, () => {}, (flag) => retrying.push(flag));
  const chunk = await inFlight();
  chunk.sending(10 * MB);
  advance(ctx.UPLOAD_STALL_TIMEOUT - 1);
  assert.equal(chunk.aborted, undefined, 'not cut a moment too soon');
  advance(1);
  assert.equal(chunk.aborted, true);

  const result = await upload.promise;
  assert.equal(result.status, 201);
  assert.deepEqual(waits, [1000], 'retried as a lost connection');
  assert.ok(retrying.includes(true));
  assert.deepEqual(ranges(sent), [
    `bytes 0-${40 * MB - 1}/${file.size}`,
    `bytes ${40 * MB}-${80 * MB - 1}/${file.size}`, // cut
    `bytes ${40 * MB}-${80 * MB - 1}/${file.size}`, // answered 409: the server holds 50 MB
    `bytes ${50 * MB}-${90 * MB - 1}/${file.size}`,
    `bytes ${90 * MB}-${file.size - 1}/${file.size}`,
  ]);
  assert.equal(watchdogs.size, 0, 'no watchdog outlives its request');
});

test('a chunk that keeps moving is never cut, however slowly', async () => {
  const file = bigFile(60 * MB);
  const server = sessionServer(file, {
    override: (xhr) => (xhr.method === 'PATCH' ? 'pending' : null),
  });
  const { ctx, advance, inFlight } = makeEnv(server.answer);

  const upload = ctx.sendFileUpload(file, {}, () => {});
  const chunk = await inFlight();
  for (let kb = 1; kb <= 20; kb++) {
    advance(ctx.UPLOAD_STALL_TIMEOUT - 1000);
    chunk.sending(kb * 1024);
  }
  assert.equal(chunk.aborted, undefined);

  upload.abort();
  await assert.rejects(upload.promise, /Cancelled/);
});

test('a chunk whose every byte is sent waits for the answer as long as it takes', async () => {
  const file = bigFile(60 * MB);
  let held = true;
  const server = sessionServer(file, {
    override: (xhr) => {
      if (xhr.method !== 'PATCH' || !held) return null;
      held = false;
      return 'pending';
    },
  });
  const { ctx, advance, inFlight } = makeEnv(server.answer);

  const upload = ctx.sendFileUpload(file, {}, () => {});
  const chunk = await inFlight();
  chunk.sending(40 * MB);
  advance(10 * ctx.UPLOAD_STALL_TIMEOUT);
  assert.equal(chunk.aborted, undefined);

  chunk.respond(server.answer(chunk));
  assert.equal((await upload.promise).status, 201);
});

test('the finalize sends no body, so storing and scanning the file is never cut', async () => {
  const file = bigFile(60 * MB);
  let held = true;
  const server = sessionServer(file, {
    override: (xhr) => {
      if (!xhr.url.endsWith('/finalize') || !held) return null;
      held = false;
      return 'pending';
    },
  });
  const { ctx, advance, watchdogs, inFlight } = makeEnv(server.answer);

  const upload = ctx.sendFileUpload(file, {}, () => {});
  const finalize = await inFlight();
  assert.equal(watchdogs.size, 0);
  advance(10 * ctx.UPLOAD_STALL_TIMEOUT);
  assert.equal(finalize.aborted, undefined);

  finalize.respond(server.answer(finalize));
  assert.equal((await upload.promise).status, 201);
});

test('a small file whose bytes stop going up fails as a lost connection', async () => {
  const { ctx, advance, watchdogs, inFlight } = makeEnv(() => 'pending');

  const upload = ctx.sendFileUpload({ name: 'note.txt', size: 10 * MB }, {}, () => {});
  const xhr = await inFlight();
  xhr.sending(MB, 10 * MB);
  advance(ctx.UPLOAD_STALL_TIMEOUT);

  await assert.rejects(upload.promise, (error) => error.transient === true && error.message === 'Network error');
  assert.equal(xhr.aborted, true);
  assert.equal(watchdogs.size, 0);
});

test('a small file all sent waits for the server as long as it takes', async () => {
  const { ctx, advance, inFlight } = makeEnv(() => 'pending');

  const upload = ctx.sendFileUpload({ name: 'note.txt', size: 10 * MB }, {}, () => {});
  const xhr = await inFlight();
  xhr.sending(10 * MB, 10 * MB);
  advance(10 * ctx.UPLOAD_STALL_TIMEOUT);
  assert.equal(xhr.aborted, undefined);

  xhr.respond([201, { uuid: 'f1', name: 'note.txt' }]);
  assert.equal((await upload.promise).status, 201);
});

test('a cancelled upload leaves no watchdog behind', async () => {
  const { ctx, watchdogs, inFlight } = makeEnv(() => 'pending');

  const upload = ctx.sendFileUpload({ name: 'note.txt', size: 10 * MB }, {}, () => {});
  await inFlight();
  upload.abort();

  await assert.rejects(upload.promise, /Cancelled/);
  assert.equal(watchdogs.size, 0);
});

test('a slow mobile link is never mistaken for a dead one', () => {
  const { ctx } = makeEnv(() => 'pending');
  assert.ok(ctx.UPLOAD_STALL_TIMEOUT >= 30000);
});
