'use strict';

// A read that is deferred until the user comes back is only dropped once the
// server recorded it, so markAsRead must tell a refused POST from a done one.

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function buildMixin(fetchImpl) {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    getCSRFToken: () => 'csrf-token',
    fetch: fetchImpl,
    console: { error: () => {} },
  });
  return ctx.chatMessagesMixin();
}

test('markAsRead resolves true when the server accepts the read', async () => {
  const mixin = buildMixin(async () => ({ ok: true }));
  assert.equal(await mixin.markAsRead('conv-1'), true);
});

test('markAsRead resolves false on an error status', async () => {
  const mixin = buildMixin(async () => ({ ok: false, status: 503 }));
  assert.equal(await mixin.markAsRead('conv-1'), false);
});

test('markAsRead resolves false when the request fails', async () => {
  const mixin = buildMixin(async () => { throw new TypeError('network down'); });
  assert.equal(await mixin.markAsRead('conv-1'), false);
});
