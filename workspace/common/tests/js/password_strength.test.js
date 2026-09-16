// The meter component: what it says while the estimator is on its way, what
// it says when it never arrives, and that a stale answer never lands on a
// password the user has since replaced.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('./loader');

const SCRIPT = 'workspace/common/static/ui/js/password_strength.js';
const BUNDLE_URL = '/static/ui/js/vendor/password-strength/password-strength.js';

// A page whose <script> tags load through a hook the test controls: `land`
// registers the bundle's global and fires onload, `fail` fires onerror.
function page(tools) {
  const scripts = [];
  const ctx = loadScript(SCRIPT, {
    document: {
      createElement: () => ({}),
      head: { appendChild: (script) => scripts.push(script) },
    },
    // Debounce runs synchronously: the pacing is a browser concern, and the
    // ordering it protects is exercised through the generation token below.
    setTimeout: (fn) => { fn(); return 1; },
    clearTimeout: () => {},
  });
  return {
    ctx,
    scripts,
    meter: () => ctx.passwordStrengthMeter(BUNDLE_URL),
    land(estimate = tools) {
      ctx.window.passwordStrengthTools = { estimateStrength: estimate };
      for (const script of scripts.splice(0)) script.onload();
    },
    fail() {
      for (const script of scripts.splice(0)) script.onerror();
    },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

const scoreBy = (table) => async (password) => ({
  score: table[password],
  warning: table[password] < 3 ? 'This is a heavily used password.' : '',
  suggestions: [],
});

test('an empty field shows nothing', () => {
  const { meter } = page();
  const m = meter();
  m.track('', true);
  assert.equal(m.status, 'idle');
  assert.equal(m.verdict().label, '');
});

test('the estimator is fetched once the field is on screen, before any keystroke', () => {
  const { meter, scripts } = page();
  const m = meter();
  m.track('', false);
  assert.equal(scripts.length, 0, 'fetched while the field was hidden');
  m.track('', true);
  assert.equal(scripts.length, 1);
  assert.equal(scripts[0].src, BUNDLE_URL);
  meter().track('', true);
  assert.equal(scripts.length, 1, 'a second meter fetched the bundle again');
});

test('a hidden field still gets a verdict on input', async () => {
  const { meter, scripts, land } = page(scoreBy({ hunter2: 0 }));
  const m = meter();
  m.track('hunter2', false);
  assert.equal(scripts.length, 1, 'typing did not fetch the estimator');
  land();
  await settle();
  assert.equal(m.status, 'ready');
  assert.equal(m.score, 0);
});

test('the verdict says "checking" until the estimate lands, never "passed"', async () => {
  const { meter, land } = page(scoreBy({ 'correct horse battery staple': 4 }));
  const m = meter();
  m.track('correct horse battery staple', true);
  assert.equal(m.status, 'checking');
  assert.match(m.verdict().label, /checking/i);
  assert.equal(m.verdict().value, 0);
  land();
  await settle();
  assert.equal(m.status, 'ready');
  assert.equal(m.verdict().label, 'Very strong');
  assert.equal(m.verdict().css, 'progress-success');
  assert.equal(m.verdict().value, 5);
});

test('each score band has its own label and colour', async () => {
  const table = { a: 0, b: 1, c: 2, d: 3, e: 4 };
  const { meter, land } = page(scoreBy(table));
  land();
  const seen = [];
  for (const password of Object.keys(table)) {
    const m = meter();
    m.track(password, true);
    await settle();
    seen.push([m.verdict().label, m.verdict().css]);
  }
  assert.deepEqual(seen, [
    ['Very weak', 'progress-error'],
    ['Weak', 'progress-error'],
    ['Fair', 'progress-warning'],
    ['Strong', 'progress-success'],
    ['Very strong', 'progress-success'],
  ]);
});

test('the warning is passed through for the user to read', async () => {
  const { meter, land } = page(scoreBy({ password123: 0 }));
  land();
  const m = meter();
  m.track('password123', true);
  await settle();
  assert.equal(m.warning, 'This is a heavily used password.');
});

test('a bundle that fails to load reports it instead of pretending', async () => {
  const { meter, fail } = page();
  const m = meter();
  m.track('whatever-the-user-typed', true);
  fail();
  await settle();
  assert.equal(m.status, 'unavailable');
  assert.match(m.verdict().label, /could not be checked/);
  assert.equal(m.score, null);
});

test('a failed load is retried on the next keystroke', async () => {
  const { meter, scripts, fail, land } = page(scoreBy({ 'second try': 3 }));
  const m = meter();
  m.track('first try', true);
  fail();
  await settle();
  assert.equal(m.status, 'unavailable');
  m.track('second try', true);
  assert.equal(scripts.length, 1, 'the failed load was remembered as final');
  land();
  await settle();
  assert.equal(m.status, 'ready');
  assert.equal(m.score, 3);
});

test('an estimate that throws leaves the meter unavailable, not stuck on checking', async () => {
  const { meter, land } = page(async () => {
    throw new Error('dictionaries missing');
  });
  land();
  const m = meter();
  m.track('whatever', true);
  await settle();
  assert.equal(m.status, 'unavailable');
});

test('a slow answer about a replaced password is discarded', async () => {
  // The first estimate resolves after the user has typed more: its score is
  // about a password no longer in the field.
  const gates = {};
  const { meter, land } = page((password) => new Promise((resolve) => {
    gates[password] = () => resolve({ score: password === 'abc' ? 0 : 4, warning: '', suggestions: [] });
  }));
  land();
  const m = meter();
  m.track('abc', true);
  m.track('abc-then-much-more-typed', true);
  await settle();
  gates['abc-then-much-more-typed']();
  await settle();
  assert.equal(m.score, 4);
  gates['abc']();
  await settle();
  assert.equal(m.score, 4, 'the stale verdict overwrote the current one');
  assert.equal(m.status, 'ready');
});

test('clearing the field takes the verdict down with it', async () => {
  const { meter, land } = page(scoreBy({ hunter2: 0 }));
  land();
  const m = meter();
  m.track('hunter2', true);
  await settle();
  assert.equal(m.status, 'ready');
  m.track('', true);
  assert.equal(m.status, 'idle');
  assert.equal(m.score, null);
  assert.equal(m.warning, '');
});

test('an answer arriving after teardown is dropped', async () => {
  let release;
  const { meter, land } = page(() => new Promise((resolve) => {
    release = () => resolve({ score: 2, warning: '', suggestions: [] });
  }));
  land();
  const m = meter();
  m.track('something', true);
  await settle();
  m.destroy();
  release();
  await settle();
  assert.notEqual(m.status, 'ready');
});
