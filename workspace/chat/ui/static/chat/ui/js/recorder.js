'use strict';

// Voice recording in the composer. Pure helpers first so they can be unit
// tested through the shared node:vm loader, Alpine mixin below.

// Ordered by preference. Opus is the smallest for speech; Safari supports
// neither WebM flavour and needs the MP4 container.
window.CHAT_VOICE_TYPES = [
  { mime: 'audio/webm;codecs=opus', ext: 'webm' },
  { mime: 'audio/webm', ext: 'webm' },
  { mime: 'audio/mp4', ext: 'm4a' },
];

window.pickVoiceRecordingType = function pickVoiceRecordingType(isSupported) {
  if (typeof isSupported !== 'function') return null;
  for (const candidate of window.CHAT_VOICE_TYPES) {
    if (isSupported(candidate.mime)) return candidate;
  }
  return null;
};

window.voiceFileName = function voiceFileName(ext, stamp) {
  // Colons are illegal in Windows filenames and awkward in Content-Disposition;
  // the ISO stamp is flattened to hyphens.
  return 'voice-' + String(stamp).replace(/[:.]/g, '-') + '.' + ext;
};

// Floor for a measured recording: the endpoint rejects duration <= 0, and a
// clip short enough to read as zero still holds audio worth sending.
window.CHAT_VOICE_MIN_SECONDS = 0.1;

window.recordedDurationSeconds = function recordedDurationSeconds(
  startedAt,
  endedAt,
  maxSeconds
) {
  const elapsed = (endedAt - startedAt) / 1000;
  if (!Number.isFinite(elapsed) || elapsed <= 0) return window.CHAT_VOICE_MIN_SECONDS;
  if (Number.isFinite(maxSeconds) && maxSeconds > 0) return Math.min(elapsed, maxSeconds);
  return elapsed;
};

// performance.now() is monotonic: a system clock adjustment mid-recording
// cannot make the measured length negative or absurd the way Date.now() can.
window.monotonicNow = function monotonicNow() {
  return typeof performance !== 'undefined' && performance.now
    ? performance.now()
    : Date.now();
};

window.chatRecorderMixin = function chatRecorderMixin() {
  return {
    recorderState: 'idle', // 'idle' | 'recording' | 'preview'
    recorderSupported: false,
    recordSeconds: 0,
    recordedFile: null,
    recordedUrl: null,
    recordedDuration: 0,
    sendingRecording: false,
    _recorder: null,
    _recorderStream: null,
    _recorderChunks: [],
    _recorderType: null,
    _recordTimer: null,
    _recordStartedAt: 0,
    _startingRecording: false,

    initRecorder() {
      const hasApi =
        typeof window.MediaRecorder !== 'undefined' &&
        !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
      this._recorderType = hasApi
        ? window.pickVoiceRecordingType((m) => window.MediaRecorder.isTypeSupported(m))
        : null;
      this.recorderSupported = this._recorderType !== null;
    },

    voiceMaxSeconds() {
      const el = document.getElementById('voice-max-seconds-data');
      return el ? JSON.parse(el.textContent) : 300;
    },

    recordLabel() {
      return window.formatAudioTime(this.recordSeconds);
    },

    async startRecording() {
      if (!this.recorderSupported || this.recorderState !== 'idle' || this._startingRecording) return;
      // _startingRecording prevents double-click re-entry: recorderState cannot serve as a
      // guard while getUserMedia is pending (several seconds during the permission prompt).
      this._startingRecording = true;
      try {
        this._recorderStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this._recorderChunks = [];
        this._recorder = new MediaRecorder(this._recorderStream, {
          mimeType: this._recorderType.mime,
        });
        this._recorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) this._recorderChunks.push(e.data);
        };
        this._recorder.onstop = () => this._finalizeRecording();
        this._recorder.start();
        this.recorderState = 'recording';
        this.recordSeconds = 0;
        // recordSeconds only drives the displayed timer. The duration that
        // reaches the server is measured from this stamp, so a clip stopped
        // before the first tick is not reported as zero seconds.
        this._recordStartedAt = window.monotonicNow();
        const max = this.voiceMaxSeconds();
        this._recordTimer = setInterval(() => {
          this.recordSeconds += 1;
          if (this.recordSeconds >= max) this.stopRecording();
        }, 1000);
      } catch (e) {
        // getUserMedia denied, or MediaRecorder construction/start failed
        this._releaseMic();
        if (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError') {
          this.showAlert?.('error', 'Microphone access was denied.');
        } else {
          this.showAlert?.('error', 'Recording could not start.');
        }
      } finally {
        this._startingRecording = false;
      }
    },

    stopRecording() {
      if (this.recorderState !== 'recording') return;
      this._clearRecordTimer();
      // A second click before onstop lands would otherwise throw
      // InvalidStateError out of the Alpine handler.
      if (this._recorder && this._recorder.state !== 'inactive') {
        this._recorder.stop(); // triggers _finalizeRecording via onstop
      }
    },

    cancelRecording() {
      this._clearRecordTimer();
      if (this._recorder && this._recorder.state !== 'inactive') {
        // Drop the buffered chunks first so onstop produces nothing.
        this._recorderChunks = [];
        this._recorder.stop();
      }
      this._releaseMic();
      this._discardRecordedFile();
      this.recorderState = 'idle';
      this.recordSeconds = 0;
      this._recordStartedAt = 0;
    },

    async sendRecording() {
      if (this.recorderState !== 'preview' || !this.recordedFile || this.sendingRecording) {
        return;
      }
      const file = this.recordedFile;
      const duration = this.recordedDuration;
      this.sendingRecording = true;
      let sent = false;
      try {
        sent = await this.sendVoiceMessage(file, duration);
      } finally {
        this.sendingRecording = false;
      }
      // A failed send keeps the blob and its object URL alive so the user can
      // retry; sendVoiceMessage has already surfaced the error.
      if (!sent) return;
      this._discardRecordedFile();
      this.recorderState = 'idle';
      this.recordSeconds = 0;
      this._recordStartedAt = 0;
    },

    _finalizeRecording() {
      // A cancelled recorder's queued stop event can land after a new
      // recording has started; that stale handler must not release the new
      // stream or overwrite the new state.
      if (this.recorderState !== 'recording') return;
      this._releaseMic();
      if (this._recorderChunks.length === 0) {
        this.recorderState = 'idle';
        this._recordStartedAt = 0;
        return;
      }
      const blob = new Blob(this._recorderChunks, { type: this._recorderType.mime });
      this._recorderChunks = [];
      const name = window.voiceFileName(this._recorderType.ext, new Date().toISOString());
      this.recordedFile = new File([blob], name, { type: this._recorderType.mime });
      this.recordedUrl = URL.createObjectURL(blob);
      this.recordedDuration = window.recordedDurationSeconds(
        this._recordStartedAt,
        window.monotonicNow(),
        this.voiceMaxSeconds()
      );
      this._recordStartedAt = 0;
      this.recorderState = 'preview';
    },

    _releaseMic() {
      // Stop the tracks so the browser's recording indicator goes away.
      if (this._recorderStream) {
        for (const track of this._recorderStream.getTracks()) track.stop();
        this._recorderStream = null;
      }
      this._recorder = null;
    },

    _clearRecordTimer() {
      if (this._recordTimer) {
        clearInterval(this._recordTimer);
        this._recordTimer = null;
      }
    },

    _discardRecordedFile() {
      if (this.recordedUrl) URL.revokeObjectURL(this.recordedUrl);
      this.recordedUrl = null;
      this.recordedFile = null;
      this.recordedDuration = 0;
    },
  };
};
