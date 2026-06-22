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

function chatDiagConnectionUp(connectionState, iceConnectionState) {
  // The loopback is usable as soon as a candidate pair is established. ICE
  // reaching 'connected'/'completed' proves the peers can exchange media, even
  // when connectionState lags at 'connecting' - on a same-machine loopback the
  // DTLS handshake can stall without media actually flowing, so gating success
  // on connectionState alone produces a false timeout.
  return (
    connectionState === 'connected' ||
    iceConnectionState === 'connected' ||
    iceConnectionState === 'completed'
  );
}

function chatDiagRmsToLevel(samples) {
  // Analyser time-domain bytes are centered on 128. Compute the RMS deviation
  // from center (0..~1), scale so normal speech moves the bar noticeably, and
  // clamp to a 0..100 percentage for the volume bar width.
  if (!samples || !samples.length) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const d = (samples[i] - 128) / 128;
    sum += d * d;
  }
  const rms = Math.sqrt(sum / samples.length);
  return Math.max(0, Math.min(100, Math.round(rms * 400)));
}

window.chatDiagClassifyCandidate = chatDiagClassifyCandidate;
window.chatDiagSummarizeIce = chatDiagSummarizeIce;
window.chatDiagRouteLane = chatDiagRouteLane;
window.chatDiagConnectionUp = chatDiagConnectionUp;
window.chatDiagRmsToLevel = chatDiagRmsToLevel;

window.chatCallDiagnosticMixin = function chatCallDiagnosticMixin() {
  return {
    // -- Diagnostic state ------------------------------------
    diagOpen: false,
    diagRunning: false,
    diagSummary: '',          // overall verdict label, set when the run ends
    diagRunId: '',            // nonce guarding echoes against stale/foreign runs
    diagSteps: [
      { key: 'mic', label: 'Microphone access', status: 'pending', detail: '' },
      { key: 'ice', label: 'Network / ICE servers', status: 'pending', detail: '' },
      { key: 'loopback', label: 'Server relay + WebRTC loopback', status: 'pending', detail: '' },
    ],
    _diagPeers: { caller: null, callee: null },
    _diagStream: null,
    _diagPending: { caller: [], callee: [] }, // ICE buffered until remote desc set
    _diagOnServerEcho: null,

    _diagStep(key) {
      return this.diagSteps.find((s) => s.key === key);
    },
    _diagSet(key, status, detail) {
      const s = this._diagStep(key);
      if (s) { s.status = status; if (detail !== undefined) s.detail = detail; }
    },

    openDiagnostic() {
      if (this.diagRunning) return;
      this.diagOpen = true;
      this._runDiagnostic();
    },

    closeDiagnostic() {
      this.diagOpen = false;
      this._diagCleanup();
      this.diagRunning = false;
    },

    _diagCleanup() {
      for (const role of ['caller', 'callee']) {
        const pc = this._diagPeers[role];
        if (pc) { try { pc.close(); } catch (e) { /* ignore */ } }
        this._diagPeers[role] = null;
        this._diagPending[role] = [];
      }
      if (this._diagStream) {
        this._diagStream.getTracks().forEach((t) => t.stop());
        this._diagStream = null;
      }
      this._diagOnServerEcho = null;
    },

    async _runDiagnostic() {
      this.diagRunning = true;
      this.diagSummary = '';
      this._diagCleanup();
      // New nonce per run so echoes from a previous run are ignored.
      this.diagRunId = 'diag-' + Date.now() + '-' + Math.floor(Math.random() * 1e6);
      this.diagSteps.forEach((s) => { s.status = 'pending'; s.detail = ''; });

      await this._diagStepMic();
      const iceVerdict = await this._diagStepIce();
      const loopOk = await this._diagStepLoopback();

      const micOk = this._diagStep('mic').status === 'pass';
      if (loopOk && micOk && iceVerdict !== 'warn') {
        this.diagSummary = 'Everything works. Your connection is ready for calls.';
      } else if (loopOk) {
        this.diagSummary = 'Calls should work, but some checks need attention.';
      } else {
        this.diagSummary = 'Connection problem detected. Calls may not work here.';
      }
      this.diagRunning = false;
    },

    async _diagStepMic() {
      this._diagSet('mic', 'running', 'Requesting microphone...');
      try {
        this._diagStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this._diagSet('mic', 'pass', 'Microphone captured.');
      } catch (e) {
        this._diagSet('mic', 'fail', 'Microphone unavailable or permission denied.');
      }
    },

    async _diagStepIce() {
      this._diagSet('ice', 'running', 'Gathering ICE candidates...');
      const candidates = await this._diagGatherCandidates();
      const summary = window.chatDiagSummarizeIce(candidates);
      const parts = [];
      if (summary.relay) parts.push('TURN reachable');
      else if (summary.srflx) parts.push('STUN reachable (no TURN)');
      else if (summary.host) parts.push('local network only');
      else parts.push('no candidates gathered');
      const status = summary.verdict === 'pass' ? 'pass'
        : (summary.verdict === 'warn' ? 'warn' : 'fail');
      this._diagSet('ice', status, parts[0] + '.');
      return summary.verdict;
    },

    _diagGatherCandidates() {
      // Open a throwaway PC, add a data channel to trigger gathering, and
      // collect candidates until the browser signals completion or ~5s pass.
      return new Promise((resolve) => {
        let pc;
        try {
          pc = new RTCPeerConnection({ iceServers: this._loadIceServers() });
        } catch (e) { resolve([]); return; }
        const found = [];
        let done = false;
        const finish = () => {
          if (done) return;
          done = true;
          clearTimeout(timer);
          try { pc.close(); } catch (e) { /* ignore */ }
          resolve(found);
        };
        const timer = setTimeout(finish, 5000);
        pc.onicecandidate = (ev) => {
          if (ev.candidate && ev.candidate.candidate) found.push(ev.candidate);
          else finish(); // null candidate = gathering complete
        };
        try {
          pc.createDataChannel('diag');
          pc.createOffer()
            .then((o) => pc.setLocalDescription(o))
            .catch(() => finish());
        } catch (e) { finish(); }
      });
    },

    _diagStepLoopback() {
      this._diagSet('loopback', 'running', 'Connecting through the server...');
      return new Promise((resolve) => {
        let settled = false;
        const ice = this._loadIceServers();
        let caller, callee;
        try {
          caller = new RTCPeerConnection({ iceServers: ice });
          callee = new RTCPeerConnection({ iceServers: ice });
        } catch (e) {
          this._diagSet('loopback', 'fail', 'Could not create peer connections.');
          resolve(false);
          return;
        }
        this._diagPeers.caller = caller;
        this._diagPeers.callee = callee;
        let serverProven = false;

        const finish = (ok, detail) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          this._diagSet('loopback', ok ? 'pass' : 'fail', detail);
          resolve(ok);
        };
        const timer = setTimeout(
          () => finish(false, serverProven
            ? 'Server relay OK, but the media connection timed out (NAT/firewall?).'
            : 'No response from the server relay.'),
          12000,
        );

        // ICE trickle: caller -> callee uses lane to_callee, and vice versa.
        caller.onicecandidate = (ev) => {
          if (ev.candidate) this._diagSendSignal('to_callee', { type: 'ice', candidate: ev.candidate });
        };
        callee.onicecandidate = (ev) => {
          if (ev.candidate) this._diagSendSignal('to_caller', { type: 'ice', candidate: ev.candidate });
        };
        const onConnected = () => {
          if (window.chatDiagConnectionUp(caller.connectionState, caller.iceConnectionState)) {
            finish(true, 'Connected end-to-end through the server.');
          }
        };
        caller.onconnectionstatechange = onConnected;
        caller.oniceconnectionstatechange = onConnected;

        // Track when the first echo proves the server path is alive.
        this._diagOnServerEcho = () => {
          if (!serverProven) {
            serverProven = true;
            this._diagSet('loopback', 'running', 'Server relay OK. Establishing media...');
          }
        };

        if (this._diagStream) {
          this._diagStream.getTracks().forEach((t) => caller.addTrack(t, this._diagStream));
        } else {
          try { caller.addTransceiver('audio'); } catch (e) { /* ignore */ }
        }

        caller.createOffer()
          .then((offer) => caller.setLocalDescription(offer))
          .then(() => this._diagSendSignal('to_callee', { type: 'offer', sdp: caller.localDescription }))
          .catch(() => finish(false, 'Failed to create the loopback offer.'));
      });
    },

    async _diagSendSignal(lane, signal) {
      try {
        await fetch('/api/v1/chat/call/diagnostic/signal', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() },
          body: JSON.stringify({ lane, signal, run_id: this.diagRunId }),
        });
      } catch (e) { /* the loopback timeout reports the failure */ }
    },

    async onCallDiagnosticSignal(detail) {
      const role = window.chatDiagRouteLane(detail, this.diagRunId);
      if (!role) return; // stale run or foreign echo
      if (this._diagOnServerEcho) this._diagOnServerEcho();
      const pc = this._diagPeers[role];
      if (!pc) return;
      const signal = detail.signal || {};
      try {
        if (signal.type === 'offer') {
          await pc.setRemoteDescription(signal.sdp);
          await this._diagFlushPending(role);
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          this._diagSendSignal('to_caller', { type: 'answer', sdp: pc.localDescription });
        } else if (signal.type === 'answer') {
          await pc.setRemoteDescription(signal.sdp);
          await this._diagFlushPending(role);
        } else if (signal.type === 'ice' && signal.candidate) {
          if (pc.remoteDescription) await pc.addIceCandidate(signal.candidate);
          else this._diagPending[role].push(signal.candidate);
        }
      } catch (e) {
        console.warn('Diagnostic signal handling failed:', signal.type, e);
      }
    },

    async _diagFlushPending(role) {
      const pc = this._diagPeers[role];
      const queued = this._diagPending[role];
      this._diagPending[role] = [];
      for (const cand of queued) {
        try { await pc.addIceCandidate(cand); } catch (e) { /* ignore */ }
      }
    },
  };
};
