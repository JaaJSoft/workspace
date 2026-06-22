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

function chatDiagLoopbackConnected(callerConnState, callerIceState, calleeConnState, calleeIceState) {
  // A same-machine loopback is usable as soon as EITHER local peer reports a
  // working candidate pair. The offerer (caller) and answerer (callee) reach
  // 'connected' at different times - the callee frequently gets there first
  // while the caller still shows 'checking' - so watching only one side misses
  // the connection and produces a false media timeout.
  return (
    chatDiagConnectionUp(callerConnState, callerIceState) ||
    chatDiagConnectionUp(calleeConnState, calleeIceState)
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
window.chatDiagLoopbackConnected = chatDiagLoopbackConnected;
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

    // -- Live audio stage (shown after the loopback passes) --
    diagLive: false,          // is the live volume/monitor stage active?
    diagLevel: 0,             // 0..100 current volume bar level
    diagMonitor: false,       // is "hear myself" playback unmuted?
    _diagRemoteStream: null,  // audio received back through the loopback
    _diagAudioCtx: null,
    _diagAnalyser: null,
    _diagRaf: null,

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
      // Invalidate the active run token so in-flight async resolutions (the
      // loopback timer, getUserMedia, ICE gathering) from the closed run are
      // ignored when they fire and cannot overwrite a later reopened run.
      this.diagRunId = '';
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
      if (this._diagRaf) { cancelAnimationFrame(this._diagRaf); this._diagRaf = null; }
      this._diagAnalyser = null;
      if (this._diagAudioCtx) {
        try { this._diagAudioCtx.close(); } catch (e) { /* ignore */ }
        this._diagAudioCtx = null;
      }
      const monEl = this.$refs && this.$refs.diagMonitorEl;
      if (monEl) { try { monEl.pause(); } catch (e) { /* ignore */ } monEl.srcObject = null; }
      this._diagRemoteStream = null;
      this.diagLive = false;
      this.diagLevel = 0;
      this.diagMonitor = false;
      this._diagOnServerEcho = null;
    },

    async _runDiagnostic() {
      this.diagRunning = true;
      this.diagSummary = '';
      this._diagCleanup();
      // New nonce per run so echoes from a previous run are ignored.
      this.diagRunId = 'diag-' + Date.now() + '-' + Math.floor(Math.random() * 1e6);
      const runId = this.diagRunId;
      this.diagSteps.forEach((s) => { s.status = 'pending'; s.detail = ''; });

      // After each await, bail out if the run was closed or superseded (the
      // token changed) so a stale step cannot drive the summary/running state.
      await this._diagStepMic();
      if (this.diagRunId !== runId) return;
      const iceVerdict = await this._diagStepIce();
      if (this.diagRunId !== runId) return;
      const loopOk = await this._diagStepLoopback();
      if (this.diagRunId !== runId) return;

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
      const runId = this.diagRunId;
      this._diagSet('mic', 'running', 'Requesting microphone...');
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        // If the run was closed/superseded while the prompt was open, drop the
        // capture instead of leaving the microphone live and clobbering state.
        if (this.diagRunId !== runId) { stream.getTracks().forEach((t) => t.stop()); return; }
        this._diagStream = stream;
        this._diagSet('mic', 'pass', 'Microphone captured.');
      } catch (e) {
        if (this.diagRunId !== runId) return;
        this._diagSet('mic', 'fail', 'Microphone unavailable or permission denied.');
      }
    },

    async _diagStepIce() {
      const runId = this.diagRunId;
      this._diagSet('ice', 'running', 'Gathering ICE candidates...');
      const candidates = await this._diagGatherCandidates();
      if (this.diagRunId !== runId) return 'fail'; // run closed/superseded mid-gather
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
      const runId = this.diagRunId;
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
          clearInterval(resend);
          // Stale run (closed/superseded): the cleanup already ran elsewhere, so
          // just resolve without touching the current run's UI or resources.
          if (this.diagRunId !== runId) { resolve(ok); return; }
          this._diagSet('loopback', ok ? 'pass' : 'fail', detail);
          if (ok) {
            this.diagLive = true;
            this._diagStartMeter();
          } else {
            // Failure: no live stage will use the peers or mic, so release them
            // now instead of holding the microphone open until the user closes.
            this._diagCleanup();
          }
          resolve(ok);
        };
        const timer = setTimeout(
          () => finish(false, serverProven
            ? 'Server relay OK, but the media connection timed out (NAT/firewall?).'
            : 'No response from the server relay.'),
          25000,
        );

        // Non-trickle ICE: the full local SDP (candidates embedded) is sent as a
        // single message per side, so the loopback exchanges 2 messages instead
        // of ~20 trickled candidates - far more reliable than relying on every
        // candidate POST surviving the SSE relay. The send timing is handled by
        // _diagSendSdp (gathering-ready or a short cap), not gathering completion,
        // so a slow or unreachable TURN server cannot stall the offer.
        callee.ontrack = (ev) => {
          this._diagRemoteStream = ev.streams[0];
          if (this.diagLive) this._diagStartMeter();
        };
        const onConnected = () => {
          if (window.chatDiagLoopbackConnected(
            caller.connectionState, caller.iceConnectionState,
            callee.connectionState, callee.iceConnectionState,
          )) {
            finish(true, 'Connected end-to-end through the server.');
          }
        };
        caller.onconnectionstatechange = onConnected;
        caller.oniceconnectionstatechange = onConnected;
        callee.onconnectionstatechange = onConnected;
        callee.oniceconnectionstatechange = onConnected;

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

        // setLocalDescription starts ICE gathering; _diagSendSdp posts the offer
        // as soon as usable candidates are gathered (or a short cap elapses).
        caller.createOffer()
          .then((offer) => caller.setLocalDescription(offer))
          .then(() => this._diagSendSdp(caller, 'to_callee', 'offer', runId))
          .catch(() => finish(false, 'Failed to create the loopback offer.'));

        // Resend the offer until the answer arrives, so a single dropped echo on
        // the signaling relay does not strand the loopback. The callee resends
        // its answer when it sees a duplicate offer (see onCallDiagnosticSignal).
        const resend = setInterval(() => {
          if (settled || this.diagRunId !== runId) { clearInterval(resend); return; }
          if (!caller.currentRemoteDescription && caller.localDescription) {
            this._diagSendSignal('to_callee', { type: 'offer', sdp: caller.localDescription });
          }
        }, 4000);
      });
    },

    _diagSendSdp(pc, lane, type, runId) {
      // Send pc.localDescription once ICE gathering has produced usable
      // candidates, then stop. We do NOT wait for gathering to fully complete:
      // completion also waits on STUN/TURN, and an unreachable TURN can delay it
      // for many seconds. The localDescription already carries the host
      // candidates that a same-machine loopback needs, so a short cap is safe.
      let sent = false;
      const send = () => {
        if (sent || this.diagRunId !== runId || !pc.localDescription) return;
        sent = true;
        this._diagSendSignal(lane, { type, sdp: pc.localDescription });
      };
      if (pc.iceGatheringState === 'complete') { send(); return; }
      pc.addEventListener('icegatheringstatechange', () => {
        if (pc.iceGatheringState === 'complete') send();
      });
      setTimeout(send, 1500);
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
          if (pc.localDescription) {
            // We already answered this loopback; the answer echo was likely lost
            // on the relay (the caller resent its offer). Resend the answer.
            this._diagSendSignal('to_caller', { type: 'answer', sdp: pc.localDescription });
            return;
          }
          await pc.setRemoteDescription(signal.sdp);
          await this._diagFlushPending(role);
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          this._diagSendSdp(pc, 'to_caller', 'answer', this.diagRunId);
        } else if (signal.type === 'answer') {
          if (pc.currentRemoteDescription) return; // already applied (duplicate)
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

    _diagStartMeter() {
      const stream = this._diagRemoteStream;
      const el = this.$refs && this.$refs.diagMonitorEl;
      if (!stream || !el) return; // nothing came back; bar stays flat
      if (this._diagAudioCtx) return; // meter already running
      // Attach the remote stream to the (muted) audio element. This both keeps
      // playback ready for the "hear myself" toggle and, on Chrome, pumps the
      // remote WebRTC stream so the AnalyserNode actually receives samples.
      el.srcObject = stream;
      el.muted = !this.diagMonitor;
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        this._diagAudioCtx = new Ctx();
        const source = this._diagAudioCtx.createMediaStreamSource(stream);
        this._diagAnalyser = this._diagAudioCtx.createAnalyser();
        this._diagAnalyser.fftSize = 256;
        source.connect(this._diagAnalyser);
        const buf = new Uint8Array(this._diagAnalyser.fftSize);
        const tick = () => {
          if (!this._diagAnalyser) return;
          this._diagAnalyser.getByteTimeDomainData(buf);
          this.diagLevel = window.chatDiagRmsToLevel(buf);
          this._diagRaf = requestAnimationFrame(tick);
        };
        tick();
      } catch (e) {
        // Web Audio unavailable; the toggle still plays audio via the element.
      }
    },

    _diagToggleMonitor() {
      this.diagMonitor = !this.diagMonitor;
      const el = this.$refs.diagMonitorEl;
      if (el) el.muted = !this.diagMonitor;
      // Browsers may start the context suspended until a user gesture; this
      // toggle is that gesture.
      if (this._diagAudioCtx && this._diagAudioCtx.state === 'suspended') {
        this._diagAudioCtx.resume();
      }
    },
  };
};
