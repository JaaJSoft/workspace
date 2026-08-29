// Copying a secret, and taking it back.
//
// The clearing half is where the care is. A clipboard is shared with every
// other application on the machine, so wiping it is only correct while it
// still holds what we put there - and a browser is allowed to refuse the read
// that would tell us. Both cases are pinned here, because getting the second
// one wrong destroys whatever the user copied in the meantime.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function clipboard(options = {}) {
  const timers = [];
  const clip = { value: null, denied: options.denied || false };
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/clipboard.js', {
    navigator: {
      clipboard: {
        writeText: async (text) => {
          if (options.writeDenied) throw new Error('denied');
          clip.value = text;
        },
        readText: async () => {
          if (clip.denied) throw new Error('denied');
          return clip.value;
        },
      },
    },
    // Fake timers: a countdown asserted against the wall clock is a test that
    // fails on a slow machine and proves nothing on a fast one.
    setInterval: (fn) => { timers.push(fn); return timers.length; },
    clearInterval: (id) => { timers[id - 1] = null; },
  });
  const tick = (times = 1) => {
    const pending = [];
    for (let i = 0; i < times; i += 1) {
      timers.forEach((fn) => fn && pending.push(fn()));
    }
    return Promise.all(pending);
  };
  return { ctx, clip, tick };
}

test('a copy writes the value and starts a visible countdown', async () => {
  const { ctx, clip } = clipboard();
  await ctx.vaultClipboard.copy('Password', 's3cret', { transient: true });
  assert.equal(clip.value, 's3cret');
  assert.equal(ctx.vaultClipboard.state().active, true);
  assert.equal(ctx.vaultClipboard.state().label, 'Password');
  assert.ok(ctx.vaultClipboard.state().secondsLeft > 0);
});

test('the countdown runs out and the clipboard is cleared', async () => {
  const { ctx, clip, tick } = clipboard();
  await ctx.vaultClipboard.copy('Password', 's3cret', { transient: true });
  const seconds = ctx.vaultClipboard.state().secondsLeft;
  await tick(seconds);
  assert.equal(clip.value, '');
  assert.equal(ctx.vaultClipboard.state().active, false);
});

test('the clipboard is left alone when it no longer holds what we wrote', async () => {
  // The user copied something else in the meantime. Wiping here would destroy
  // their work to protect a secret that is already gone.
  const { ctx, clip, tick } = clipboard();
  await ctx.vaultClipboard.copy('Password', 's3cret', { transient: true });
  clip.value = 'a shopping list';
  await tick(ctx.vaultClipboard.state().secondsLeft);
  assert.equal(clip.value, 'a shopping list');
});

test('a refused read leaves the clipboard alone and says so', async () => {
  // Firefox refuses navigator.clipboard.readText outright. Clearing anyway
  // would mean wiping a clipboard we were never allowed to look at.
  const { ctx, clip, tick } = clipboard({ denied: true });
  await ctx.vaultClipboard.copy('Password', 's3cret', { transient: true });
  await tick(ctx.vaultClipboard.state().secondsLeft);
  assert.equal(clip.value, 's3cret');
  assert.equal(ctx.vaultClipboard.state().active, false);
  assert.match(ctx.vaultClipboard.state().note, /could not be cleared/i);
});

test('cancelling stops the countdown and clears at once', async () => {
  const { ctx, clip, tick } = clipboard();
  await ctx.vaultClipboard.copy('Password', 's3cret', { transient: true });
  await ctx.vaultClipboard.cancel();
  assert.equal(clip.value, '');
  assert.equal(ctx.vaultClipboard.state().active, false);
  // The timer is gone, so a later tick must not clear anything a second time.
  clip.value = 'something else';
  await tick(60);
  assert.equal(clip.value, 'something else');
});

test('a second copy replaces the first countdown rather than racing it', async () => {
  // Two live intervals would take a second off twice per tick, expiring the
  // second value in half its time - and only one of them would ever be
  // cleared, so the other would outlive every copy after it.
  const { ctx, clip, tick } = clipboard();
  await ctx.vaultClipboard.copy('Password', 'first', { transient: true });
  const full = ctx.vaultClipboard.state().secondsLeft;
  await tick(3);
  assert.equal(ctx.vaultClipboard.state().secondsLeft, full - 3);

  await ctx.vaultClipboard.copy('Username', 'second', { transient: true });
  assert.equal(ctx.vaultClipboard.state().label, 'Username');
  assert.equal(ctx.vaultClipboard.state().secondsLeft, full);
  await tick(1);
  assert.equal(ctx.vaultClipboard.state().secondsLeft, full - 1);
  assert.equal(clip.value, 'second');
});

test('a value copied without a timer is never taken back', async () => {
  // A username is not a secret; taking it out of the clipboard would be a
  // surprise with nothing to gain.
  const { ctx, clip, tick } = clipboard();
  await ctx.vaultClipboard.copy('Username', 'jc', {});
  assert.equal(ctx.vaultClipboard.state().active, false);
  await tick(60);
  assert.equal(clip.value, 'jc');
});

test('a refused write reports rather than pretending it copied', async () => {
  const { ctx } = clipboard({ writeDenied: true });
  await assert.rejects(
    () => ctx.vaultClipboard.copy('Password', 's3cret', { transient: true }),
  );
  assert.equal(ctx.vaultClipboard.state().active, false);
});
