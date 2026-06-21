const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/call_diagnostic.js');

test('classify from explicit type field', () => {
  assert.equal(ctx.chatDiagClassifyCandidate({ type: 'relay' }), 'relay');
  assert.equal(ctx.chatDiagClassifyCandidate({ type: 'host' }), 'host');
});

test('classify from SDP candidate string', () => {
  const c = { candidate: 'candidate:1 1 udp 2122260223 192.168.1.2 50000 typ host' };
  assert.equal(ctx.chatDiagClassifyCandidate(c), 'host');
  const s = 'candidate:2 1 udp 1686052607 1.2.3.4 50001 typ srflx raddr 192.168.1.2 rport 50000';
  assert.equal(ctx.chatDiagClassifyCandidate(s), 'srflx');
  const r = { candidate: 'candidate:3 1 udp 41885439 5.6.7.8 60000 typ relay raddr 1.2.3.4 rport 50001' };
  assert.equal(ctx.chatDiagClassifyCandidate(r), 'relay');
  assert.equal(ctx.chatDiagClassifyCandidate('candidate:4 1 udp 1685987071 1.2.3.4 50002 typ prflx raddr 192.168.1.2 rport 50000'), 'prflx');
});

test('classify unknown when no type present', () => {
  assert.equal(ctx.chatDiagClassifyCandidate({ candidate: 'garbage' }), 'unknown');
  assert.equal(ctx.chatDiagClassifyCandidate(null), 'unknown');
});

test('summarize verdict: relay present -> pass', () => {
  const out = ctx.chatDiagSummarizeIce([{ type: 'host' }, { type: 'srflx' }, { type: 'relay' }]);
  assert.deepStrictEqual({ ...out }, { host: 1, srflx: 1, relay: 1, verdict: 'pass' });
});

test('summarize verdict: srflx only -> pass, prflx counts as srflx', () => {
  const out = ctx.chatDiagSummarizeIce([{ type: 'host' }, { type: 'prflx' }]);
  assert.deepStrictEqual({ ...out }, { host: 1, srflx: 1, relay: 0, verdict: 'pass' });
});

test('summarize verdict: host only -> warn', () => {
  const out = ctx.chatDiagSummarizeIce([{ type: 'host' }, { type: 'host' }]);
  assert.deepStrictEqual({ ...out }, { host: 2, srflx: 0, relay: 0, verdict: 'warn' });
});

test('summarize verdict: empty -> fail', () => {
  const out = ctx.chatDiagSummarizeIce([]);
  assert.deepStrictEqual({ ...out }, { host: 0, srflx: 0, relay: 0, verdict: 'fail' });
});

test('routeLane maps lanes and guards run_id', () => {
  assert.equal(ctx.chatDiagRouteLane({ lane: 'to_callee', run_id: 'r1' }, 'r1'), 'callee');
  assert.equal(ctx.chatDiagRouteLane({ lane: 'to_caller', run_id: 'r1' }, 'r1'), 'caller');
  assert.equal(ctx.chatDiagRouteLane({ lane: 'to_caller', run_id: 'OLD' }, 'r1'), null);
  assert.equal(ctx.chatDiagRouteLane(null, 'r1'), null);
  assert.equal(ctx.chatDiagRouteLane({ lane: 'sideways', run_id: 'r1' }, 'r1'), null);
});
