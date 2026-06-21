// Chat call connection diagnostic: a non-intrusive self-test that verifies the
// microphone, network/ICE reachability, and a WebRTC loopback whose signaling
// round-trips through the real server.
//
// Pure helpers (testable via the node:vm loader) are top-level functions
// mirrored onto window. The Alpine mixin (chatCallDiagnosticMixin) holds the
// RTCPeerConnection wiring and is validated in a real browser.

function chatDiagClassifyCandidate(candidate) {
  if (!candidate) return 'unknown';
  const explicit = candidate.type;
  if (explicit === 'host' || explicit === 'srflx' || explicit === 'prflx' || explicit === 'relay') {
    return explicit;
  }
  const raw = typeof candidate === 'string' ? candidate : (candidate.candidate || '');
  const m = /\btyp\s+(host|srflx|prflx|relay)\b/.exec(raw);
  return m ? m[1] : 'unknown';
}

function chatDiagSummarizeIce(candidates) {
  const counts = { host: 0, srflx: 0, relay: 0 };
  for (const c of candidates || []) {
    const t = chatDiagClassifyCandidate(c);
    if (t === 'host') counts.host += 1;
    else if (t === 'srflx' || t === 'prflx') counts.srflx += 1;
    else if (t === 'relay') counts.relay += 1;
  }
  let verdict;
  if (counts.relay > 0 || counts.srflx > 0) verdict = 'pass';
  else if (counts.host > 0) verdict = 'warn';
  else verdict = 'fail';
  return { host: counts.host, srflx: counts.srflx, relay: counts.relay, verdict };
}

function chatDiagRouteLane(detail, runId) {
  if (!detail || detail.run_id !== runId) return null;
  if (detail.lane === 'to_callee') return 'callee';
  if (detail.lane === 'to_caller') return 'caller';
  return null;
}

window.chatDiagClassifyCandidate = chatDiagClassifyCandidate;
window.chatDiagSummarizeIce = chatDiagSummarizeIce;
window.chatDiagRouteLane = chatDiagRouteLane;
