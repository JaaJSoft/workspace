// Chat call sounds: short synthesized cues for call transitions.
//
// chatCallSoundCue is a pure mapper (testable via the node:vm loader).
// chatCallSounds is the Web Audio engine; it is validated in a real browser
// (Web Audio is absent in Node) and never throws so a sound failure can never
// break a call.

function chatCallSoundCue(event, isMuted) {
  // Map a call event to a sound cue name, or null when there is none.
  // For 'toggle-mute', isMuted is the resulting state AFTER the toggle.
  switch (event) {
    case 'join': return 'join';
    case 'leave': return 'leave';
    case 'peer-join': return 'peer-join';
    case 'peer-leave': return 'peer-leave';
    case 'toggle-mute': return isMuted ? 'mute' : 'unmute';
    default: return null;
  }
}

window.chatCallSoundCue = chatCallSoundCue;

window.chatCallSounds = (function () {
  let enabled = true;
  let ctx = null;

  // Each cue is one or two sine tones. Times are seconds relative to the cue
  // start; rising pairs ascend, falling pairs descend, peers are single blips.
  const CUES = {
    join: [{ f: 440, t: 0, d: 0.10 }, { f: 660, t: 0.09, d: 0.12 }],
    leave: [{ f: 660, t: 0, d: 0.10 }, { f: 440, t: 0.09, d: 0.12 }],
    'peer-join': [{ f: 620, t: 0, d: 0.09 }],
    'peer-leave': [{ f: 380, t: 0, d: 0.09 }],
    mute: [{ f: 300, t: 0, d: 0.06 }],
    unmute: [{ f: 500, t: 0, d: 0.06 }],
  };

  function context() {
    if (ctx) return ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    return ctx;
  }

  function tone(ac, master, freq, startAt, dur) {
    const osc = ac.createOscillator();
    const gain = ac.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    // ~5ms attack to avoid a click/pop, then decay to near-silence by the end.
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.linearRampToValueAtTime(1, startAt + 0.005);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + dur);
    osc.connect(gain).connect(master);
    osc.start(startAt);
    osc.stop(startAt + dur + 0.02);
  }

  return {
    setEnabled(value) { enabled = !!value; },
    play(cue) {
      if (!enabled || !cue) return;
      const tones = CUES[cue];
      if (!tones) return;
      try {
        const ac = context();
        if (!ac) return;
        if (ac.state === 'suspended' && ac.resume) ac.resume();
        const master = ac.createGain();
        master.gain.value = 0.08; // quiet: a nicety, not an alert
        master.connect(ac.destination);
        const now = ac.currentTime;
        for (const tn of tones) tone(ac, master, tn.f, now + tn.t, tn.d);
      } catch (e) {
        // Audio is best-effort; never surface into the call flow.
      }
    },
  };
})();
