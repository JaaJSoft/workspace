'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

// Composes the sse + bot mixins the way chatApp() does, with controllable
// timers so the failsafe hide can be triggered synchronously.
function buildApp() {
  const timers = { fns: [], cleared: 0 };
  const stubs = {
    setTimeout: (fn) => { timers.fns.push(fn); return timers.fns.length; },
    clearTimeout: (id) => { if (id) timers.cleared++; },
  };
  const sseCtx = loadScript('workspace/chat/ui/static/chat/ui/js/sse.js', stubs);
  const botCtx = loadScript('workspace/chat/ui/static/chat/ui/js/bot.js', stubs);
  // Same order as chatApp(): sse first, bot last, so an overlapping key
  // resolves here the way it does in production.
  const app = Object.assign({}, sseCtx.chatSseMixin(), botCtx.chatBotMixin(), {
    activeConversation: { uuid: 'conv-1' },
  });
  return { app, timers };
}

test('bot_step raises the typing indicator and stores the step', () => {
  const { app } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', html: '<span>Web Search</span>' });

  assert.equal(app.botTyping, true);
  assert.deepStrictEqual(
    Array.from(app.botSteps, s => ({ ...s })),
    [{ html: '<span>Web Search</span>' }],
  );
});

test('bot_step for another conversation is ignored', () => {
  const { app } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-2', html: '<span>Web Search</span>' });

  assert.equal(app.botTyping, false);
  assert.equal(app.botSteps.length, 0);
});

test('bot_step with no active conversation is ignored', () => {
  const { app } = buildApp();
  app.activeConversation = null;

  app.handleSSEBotStep({ conversation_id: 'conv-1', html: '<span>Web Search</span>' });

  assert.equal(app.botTyping, false);
  assert.equal(app.botSteps.length, 0);
});

test('later steps accumulate in order and re-arm the failsafe', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', html: '<span>Web Search</span>' });
  app.handleSSEBotStep({ conversation_id: 'conv-1', html: '<span>Calendar</span>' });

  assert.deepStrictEqual(
    Array.from(app.botSteps, s => s.html),
    ['<span>Web Search</span>', '<span>Calendar</span>'],
  );
  assert.equal(timers.fns.length, 2, 'each step arms a fresh failsafe timer');
  assert.equal(timers.cleared, 1, 'the previous timer is cancelled');
});

test('the step list is capped', () => {
  const { app } = buildApp();

  for (let i = 0; i < 40; i++) {
    app.handleSSEBotStep({ conversation_id: 'conv-1', html: `<span>${i}</span>` });
  }

  assert.equal(app.botSteps.length, 30);
  assert.equal(app.botSteps[app.botSteps.length - 1].html, '<span>39</span>');
});

test('the failsafe timer hides both the steps and the typing indicator', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', html: '<span>Web Search</span>' });
  timers.fns[timers.fns.length - 1]();

  assert.equal(app.botTyping, false);
  assert.equal(app.botSteps.length, 0);
});

test('clearBotStep cancels the timer and drops the steps', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', html: '<span>Web Search</span>' });
  app.clearBotStep();

  assert.equal(app.botSteps.length, 0);
  assert.equal(app._botStepTimer, null);
  assert.equal(timers.cleared, 1);
});
