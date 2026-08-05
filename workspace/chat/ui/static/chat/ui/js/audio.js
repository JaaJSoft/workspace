'use strict';

// Inline audio player for chat attachments. Pure helpers first so they can be
// unit-tested through the shared node:vm loader, Alpine component below.

window.CHAT_AUDIO_SPEEDS = [1, 1.5, 2];

window.formatAudioTime = function formatAudioTime(seconds) {
  // Infinity is the normal reading for a MediaRecorder WebM whose header
  // carries no duration, so it gets a placeholder rather than a crash.
  if (!Number.isFinite(seconds) || seconds < 0) return '--:--';
  const total = Math.floor(seconds);
  return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
};

window.audioProgressPercent = function audioProgressPercent(current, duration) {
  if (!Number.isFinite(duration) || duration <= 0) return 0;
  if (!Number.isFinite(current) || current <= 0) return 0;
  return Math.min(100, (current / duration) * 100);
};

window.nextAudioSpeed = function nextAudioSpeed(speed) {
  const idx = window.CHAT_AUDIO_SPEEDS.indexOf(speed);
  return window.CHAT_AUDIO_SPEEDS[(idx + 1) % window.CHAT_AUDIO_SPEEDS.length];
};

window.chatAudioPlayer = function chatAudioPlayer(uuid, knownDuration) {
  return {
    uuid: uuid,
    playing: false,
    currentTime: 0,
    // The server value wins at first paint: it is the only source that works
    // for a recorded WebM, and it avoids a layout shift once metadata lands.
    duration: Number.isFinite(knownDuration) && knownDuration > 0 ? knownDuration : 0,
    speed: 1,
    detailsOpen: false,
    _onOtherPlay: null,

    init() {
      // Only one attachment plays at a time. Every instance listens; the one
      // that just started is excluded by uuid.
      this._onOtherPlay = (e) => {
        if (e.detail && e.detail.uuid !== this.uuid) this.pause();
      };
      window.addEventListener('chat-audio-play', this._onOtherPlay);
    },

    destroy() {
      window.removeEventListener('chat-audio-play', this._onOtherPlay);
    },

    onMetadata() {
      const d = this.$refs.audio.duration;
      if (Number.isFinite(d) && d > 0) this.duration = d;
    },

    toggle() {
      if (this.playing) this.pause();
      else this.play();
    },

    play() {
      window.dispatchEvent(
        new CustomEvent('chat-audio-play', { detail: { uuid: this.uuid } })
      );
      this.$refs.audio.playbackRate = this.speed;
      this.$refs.audio.play();
      this.playing = true;
    },

    pause() {
      if (!this.playing) return;
      this.$refs.audio.pause();
      this.playing = false;
    },

    onEnded() {
      this.playing = false;
      this.currentTime = 0;
    },

    seekToPercent(percent) {
      if (!this.duration) return;
      const t = (Number(percent) / 100) * this.duration;
      this.$refs.audio.currentTime = t;
      this.currentTime = t;
    },

    cycleSpeed() {
      this.speed = window.nextAudioSpeed(this.speed);
      this.$refs.audio.playbackRate = this.speed;
    },

    speedLabel() {
      return 'x' + this.speed;
    },

    elapsedLabel() {
      return window.formatAudioTime(this.currentTime);
    },

    durationLabel() {
      return window.formatAudioTime(this.duration);
    },

    progressPercent() {
      return window.audioProgressPercent(this.currentTime, this.duration);
    },
  };
};
