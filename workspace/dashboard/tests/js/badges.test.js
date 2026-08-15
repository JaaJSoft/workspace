'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * Build a dashboardBadges() component wired to stubbed window listeners,
 * a manual timer queue and a spying $ajax.
 */
function buildApp() {
  const listeners = {};
  const timers = [];
  const ctx = loadScript('workspace/dashboard/static/dashboard/js/badges.js', {
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    removeEventListener(name, fn) {
      listeners[name] = (listeners[name] || []).filter((f) => f !== fn);
    },
    setTimeout(fn, delay) { timers.push({ fn, delay }); return timers.length; },
    clearTimeout(id) { if (timers[id - 1]) timers[id - 1].fn = null; },
  });

  const app = ctx.dashboardBadges('/dashboard/modules');
  const ajaxCalls = [];
  app.$ajax = (url, options) => { ajaxCalls.push({ url, options }); };
  app.init();

  return {
    app,
    ajaxCalls,
    fire(name) { for (const fn of listeners[name] || []) fn(); },
    runTimers() {
      for (const t of timers.splice(0)) if (t.fn) t.fn();
    },
    listeners,
  };
}

test('first count event is the initial snapshot and does not refresh', () => {
  const { fire, runTimers, ajaxCalls } = buildApp();

  fire('sse:notifications.count');
  runTimers();

  assert.equal(ajaxCalls.length, 0);
});

test('a later count event refreshes the grid fragment', () => {
  const { fire, runTimers, ajaxCalls } = buildApp();

  fire('sse:notifications.count');
  fire('sse:notifications.count');
  runTimers();

  assert.equal(ajaxCalls.length, 1);
  assert.equal(ajaxCalls[0].url, '/dashboard/modules');
  assert.deepStrictEqual({ ...ajaxCalls[0].options }, { target: 'dashboard-modules-grid' });
});

test('a burst of count events coalesces into one refresh', () => {
  const { fire, runTimers, ajaxCalls } = buildApp();

  fire('sse:notifications.count');
  for (let i = 0; i < 5; i++) fire('sse:notifications.count');
  runTimers();

  assert.equal(ajaxCalls.length, 1);
});

test('reconnect refreshes immediately without debounce', () => {
  const { fire, ajaxCalls } = buildApp();

  fire('sse:reconnect');

  assert.equal(ajaxCalls.length, 1);
});

test('reconnect supersedes a queued debounced refresh', () => {
  const { fire, runTimers, ajaxCalls } = buildApp();

  fire('sse:notifications.count');
  fire('sse:notifications.count'); // debounced refresh pending
  fire('sse:reconnect');
  runTimers();

  assert.equal(ajaxCalls.length, 1);
});

test('destroy removes the window listeners and pending timer', () => {
  const { app, fire, runTimers, ajaxCalls, listeners } = buildApp();

  fire('sse:notifications.count');
  fire('sse:notifications.count'); // debounced refresh pending
  app.destroy();
  runTimers();
  fire('sse:notifications.count');
  fire('sse:reconnect');

  assert.equal(ajaxCalls.length, 0);
  assert.equal(listeners['sse:notifications.count'].length, 0);
  assert.equal(listeners['sse:reconnect'].length, 0);
});
