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

test('connectionUp: connectionState connected -> true', () => {
  assert.equal(ctx.chatDiagConnectionUp('connected', 'checking'), true);
});

test('connectionUp: iceConnectionState connected or completed -> true', () => {
  // Regression: a same-machine loopback can reach ICE 'connected' while
  // connectionState lags at 'connecting' (DTLS stalls without media flowing).
  // The old criterion (connectionState === 'connected' only) timed out here.
  assert.equal(ctx.chatDiagConnectionUp('connecting', 'connected'), true);
  assert.equal(ctx.chatDiagConnectionUp('connecting', 'completed'), true);
});

test('connectionUp: still establishing -> false', () => {
  assert.equal(ctx.chatDiagConnectionUp('connecting', 'checking'), false);
  assert.equal(ctx.chatDiagConnectionUp('new', 'new'), false);
  assert.equal(ctx.chatDiagConnectionUp('failed', 'failed'), false);
});

test('rmsToLevel: silence (centered samples) -> 0', () => {
  const silent = new Uint8Array(16).fill(128);
  assert.equal(ctx.chatDiagRmsToLevel(silent), 0);
});

test('rmsToLevel: empty or nullish -> 0', () => {
  assert.equal(ctx.chatDiagRmsToLevel(new Uint8Array(0)), 0);
  assert.equal(ctx.chatDiagRmsToLevel(null), 0);
});

test('rmsToLevel: known mid input -> proportional level', () => {
  // (144 - 128) / 128 = 0.125 rms; 0.125 * 400 = 50.
  const mid = new Uint8Array(16).fill(144);
  assert.equal(ctx.chatDiagRmsToLevel(mid), 50);
});

test('rmsToLevel: full-scale signal clamps to 100', () => {
  const loud = Uint8Array.from({ length: 16 }, (_, i) => (i % 2 ? 255 : 0));
  assert.equal(ctx.chatDiagRmsToLevel(loud), 100);
});

test('rmsToLevel: monotonic - louder is not quieter', () => {
  const quiet = new Uint8Array(16).fill(136); // d = 0.0625
  const louder = new Uint8Array(16).fill(144); // d = 0.125
  assert.ok(ctx.chatDiagRmsToLevel(louder) >= ctx.chatDiagRmsToLevel(quiet));
});

test('loopbackConnected: caller connected -> true', () => {
  assert.equal(ctx.chatDiagLoopbackConnected('connected', 'checking', 'new', 'new'), true);
});

test('loopbackConnected: callee connected while caller checking -> true', () => {
  // Regression: in a same-machine loopback the answerer (callee) often reaches
  // ICE 'connected' first while the caller is still 'checking'. Watching only
  // the caller missed this and produced a false media timeout.
  assert.equal(ctx.chatDiagLoopbackConnected('connecting', 'checking', 'connecting', 'connected'), true);
});

test('loopbackConnected: neither peer up -> false', () => {
  assert.equal(ctx.chatDiagLoopbackConnected('connecting', 'checking', 'connecting', 'checking'), false);
  assert.equal(ctx.chatDiagLoopbackConnected('new', 'new', 'new', 'new'), false);
});
