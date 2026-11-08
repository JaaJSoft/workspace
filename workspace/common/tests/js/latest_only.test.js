'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const { latestOnly } = loadScript('workspace/common/static/ui/js/latest_only.js');

test('a lone request stays current', () => {
  const request = latestOnly().start();
  assert.equal(request.isCurrent(), true);
  assert.equal(request.isCurrent(), true);
});

test('starting a request supersedes the earlier ones', () => {
  const requests = latestOnly();
  const first = requests.start();
  const second = requests.start();
  const third = requests.start();
  assert.equal(first.isCurrent(), false);
  assert.equal(second.isCurrent(), false);
  assert.equal(third.isCurrent(), true);
});

test('a superseded request never becomes current again', () => {
  const requests = latestOnly();
  const first = requests.start();
  requests.start();
  requests.cancel();
  assert.equal(first.isCurrent(), false);
});

test('cancel supersedes the request in flight without starting one', () => {
  const requests = latestOnly();
  const request = requests.start();
  requests.cancel();
  assert.equal(request.isCurrent(), false);
  assert.equal(requests.start().isCurrent(), true);
});

test('join shares the current request and is superseded with it', () => {
  const requests = latestOnly();
  const page = requests.start();
  const nextPage = requests.join();
  assert.equal(page.isCurrent(), true);
  assert.equal(nextPage.isCurrent(), true);
  requests.start();
  assert.equal(page.isCurrent(), false);
  assert.equal(nextPage.isCurrent(), false);
});

test('trackers are independent of each other', () => {
  const a = latestOnly();
  const b = latestOnly();
  const request = a.start();
  b.start();
  b.cancel();
  assert.equal(request.isCurrent(), true);
});

test('drops the response of a request superseded while it awaited', async () => {
  const requests = latestOnly();
  const applied = [];
  let releaseSlow;
  const slow = new Promise((resolve) => { releaseSlow = resolve; });
  const load = async (value, wait) => {
    const request = requests.start();
    await wait;
    if (!request.isCurrent()) return;
    applied.push(value);
  };
  const older = load('older', slow);
  const newer = load('newer', Promise.resolve());
  await newer;
  releaseSlow();
  await older;
  assert.deepEqual(applied, ['newer']);
});
