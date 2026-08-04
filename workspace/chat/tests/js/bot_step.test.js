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
  const app = Object.assign({}, botCtx.chatBotMixin(), sseCtx.chatSseMixin(), {
    activeConversation: { uuid: 'conv-1' },
  });
  return { app, timers };
}

test('bot_step raises the typing indicator and stores the step', () => {
  const { app } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', icon: '🔍', label: 'Web Search', detail: 'meteo paris' });

  assert.equal(app.botTyping, true);
  assert.deepStrictEqual({ ...app.botStep }, { icon: '🔍', label: 'Web Search', detail: 'meteo paris' });
});

test('bot_step for another conversation is ignored', () => {
  const { app } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-2', icon: '🔍', label: 'Web Search', detail: '' });

  assert.equal(app.botTyping, false);
  assert.equal(app.botStep, null);
});

test('bot_step with no active conversation is ignored', () => {
  const { app } = buildApp();
  app.activeConversation = null;

  app.handleSSEBotStep({ conversation_id: 'conv-1', icon: '🔍', label: 'Web Search', detail: '' });

  assert.equal(app.botTyping, false);
});

test('a later step replaces the previous one and re-arms the failsafe', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', icon: '🔍', label: 'Web Search', detail: 'a' });
  app.handleSSEBotStep({ conversation_id: 'conv-1', icon: '📅', label: 'Calendar', detail: 'b' });

  assert.equal(app.botStep.label, 'Calendar');
  assert.equal(timers.fns.length, 2, 'each step arms a fresh failsafe timer');
  assert.equal(timers.cleared, 1, 'the previous timer is cancelled');
});

test('the failsafe timer hides both the step and the typing indicator', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', icon: '🔍', label: 'Web Search', detail: '' });
  timers.fns[timers.fns.length - 1]();

  assert.equal(app.botTyping, false);
  assert.equal(app.botStep, null);
});

test('clearBotStep cancels the timer and drops the step', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', icon: '🔍', label: 'Web Search', detail: '' });
  app.clearBotStep();

  assert.equal(app.botStep, null);
  assert.equal(app._botStepTimer, null);
  assert.equal(timers.cleared, 1);
});
