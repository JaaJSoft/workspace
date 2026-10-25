// The Files viewer's slideshow: plays the gallery the viewer steps through,
// in full screen when the browser allows it. Spread into fileViewerInline();
// a page turns it on with `slideshow: true`. It drives the shell's gallery
// (navigateNext, _getViewableFiles) and is told by _onShown() when a file has
// merged into the panel, which is where each file's timer starts: a manual
// step therefore restarts the countdown rather than fighting it.
//
// An image stays on screen for slideshowInterval seconds once it has loaded,
// a video or a recording until it ends. A file behind its "Sensitive content"
// cover is skipped. The slideshow stops at the end of the gallery, on Escape
// or Space, when full screen is left, and when the viewer closes. It writes
// no history: gallery steps only rewrite ?open.
const SLIDESHOW_INTERVALS = [3, 5, 10];
const SLIDESHOW_DEFAULT_INTERVAL = 5;
const SLIDESHOW_STORAGE_KEY = 'files.slideshowInterval';
// The pointer at rest this long hides the controls and the cursor.
const SLIDESHOW_IDLE_MS = 2500;

function readSlideshowInterval() {
  let stored = null;
  try {
    stored = Number(window.localStorage.getItem(SLIDESHOW_STORAGE_KEY));
  } catch (_) {}
  return SLIDESHOW_INTERVALS.includes(stored) ? stored : SLIDESHOW_DEFAULT_INTERVAL;
}

window.viewerSlideshowMixin = function viewerSlideshowMixin() {
  return {
    slideshowEnabled: false,
    slideshowPlaying: false,
    slideshowIdle: false,
    slideshowIntervals: SLIDESHOW_INTERVALS,
    slideshowInterval: readSlideshowInterval(),
    _slideshowTimer: null,
    _slideshowIdleTimer: null,
    _slideshowMediaOff: null,
    // Set once the last loaded item has waited a turn for the listing's
    // infinite scroll: the next page may still be on its way.
    _slideshowAtEnd: false,
    _slideshowFullscreen: false,

    canSlideshow() {
      return this.slideshowEnabled && this.open && !this.panelEditing && this._totalViewable() > 1;
    },

    toggleSlideshow() {
      if (this.slideshowPlaying) this.stopSlideshow();
      else this.startSlideshow();
    },

    // Must run inside the user's gesture: requestFullscreen needs it.
    startSlideshow() {
      if (this.slideshowPlaying || !this.canSlideshow()) return;
      this.slideshowPlaying = true;
      this._slideshowAtEnd = false;
      this._enterSlideshowFullscreen();
      this.slideshowWake();
      if (!this.viewerLoading) this._onShown(document.getElementById(this._viewerPanelId()));
    },

    stopSlideshow() {
      this._clearSlideshowStep();
      clearTimeout(this._slideshowIdleTimer);
      this._slideshowIdleTimer = null;
      this.slideshowIdle = false;
      if (!this.slideshowPlaying) return;
      this.slideshowPlaying = false;
      this._exitSlideshowFullscreen();
    },

    setSlideshowInterval(seconds) {
      if (!SLIDESHOW_INTERVALS.includes(seconds)) return;
      this.slideshowInterval = seconds;
      try {
        window.localStorage.setItem(SLIDESHOW_STORAGE_KEY, String(seconds));
      } catch (_) {}
      if (this.slideshowPlaying && !this.viewerLoading) {
        this._onShown(document.getElementById(this._viewerPanelId()));
      }
    },

    // Shows the controls again, and hides them once the pointer rests.
    slideshowWake() {
      this.slideshowIdle = false;
      clearTimeout(this._slideshowIdleTimer);
      this._slideshowIdleTimer = null;
      if (!this.slideshowPlaying) return;
      this._slideshowIdleTimer = setTimeout(() => {
        if (this.slideshowPlaying) this.slideshowIdle = true;
      }, SLIDESHOW_IDLE_MS);
    },

    // The shell's hook, once a file's panel has merged.
    _onShown(panel) {
      this._clearSlideshowStep();
      if (!this.slideshowPlaying) return;
      if (this._slideshowCovered(panel)) {
        this._slideshowTimer = setTimeout(() => this._slideshowAdvance(), 0);
        return;
      }
      const media = panel ? panel.querySelector('video, audio') : null;
      if (media) {
        this._waitForMedia(media);
        return;
      }
      const img = panel ? panel.querySelector('img') : null;
      if (img && !img.complete) {
        const start = () => {
          this._slideshowMediaOff = null;
          this._scheduleSlideshowStep();
        };
        img.addEventListener('load', start, { once: true });
        img.addEventListener('error', start, { once: true });
        this._slideshowMediaOff = () => {
          img.removeEventListener('load', start);
          img.removeEventListener('error', start);
        };
      } else {
        this._scheduleSlideshowStep();
      }
      this._preloadNextImage();
    },

    // A video moves on when it ends. One the browser cannot play (an error,
    // or no picture: media_viewer.html), or one held paused at its start
    // (autoplay refused), gets the image's turn instead.
    _waitForMedia(media) {
      const advance = () => this._slideshowAdvance();
      const stuck = () => {
        if (media.error || media.ended || (media.paused && media.currentTime === 0)) advance();
      };
      const unplayable = () => this._scheduleSlideshowStep();
      const metadata = () => {
        if (media.tagName === 'VIDEO' && !media.videoWidth) unplayable();
      };
      media.addEventListener('ended', advance);
      media.addEventListener('error', unplayable);
      media.addEventListener('loadedmetadata', metadata);
      this._slideshowMediaOff = () => {
        media.removeEventListener('ended', advance);
        media.removeEventListener('error', unplayable);
        media.removeEventListener('loadedmetadata', metadata);
      };
      if (media.error) unplayable();
      else if (media.readyState >= 1) metadata();
      if (this._slideshowTimer === null) {
        this._slideshowTimer = setTimeout(stuck, this.slideshowInterval * 1000);
      }
    },

    _scheduleSlideshowStep() {
      clearTimeout(this._slideshowTimer);
      this._slideshowTimer = setTimeout(() => this._slideshowAdvance(), this.slideshowInterval * 1000);
    },

    _clearSlideshowStep() {
      clearTimeout(this._slideshowTimer);
      this._slideshowTimer = null;
      if (this._slideshowMediaOff) this._slideshowMediaOff();
      this._slideshowMediaOff = null;
    },

    _slideshowAdvance() {
      this._clearSlideshowStep();
      if (!this.slideshowPlaying || !this.open) return;
      // Picked up again by the visibilitychange listener.
      if (document.hidden) return;
      const files = this._getViewableFiles();
      const idx = files.findIndex((f) => f.uuid === this.fileUuid);
      if (idx >= 0 && idx < files.length - 1) {
        this._slideshowAtEnd = false;
        this.navigateNext();
        return;
      }
      if (idx >= 0 && !this._slideshowAtEnd) {
        this._slideshowAtEnd = true;
        this._scheduleSlideshowStep();
        return;
      }
      this.stopSlideshow();
    },

    // The cover only shows while the page blurs sensitive previews and the
    // user has not asked the viewer to skip it (sensitive_viewer.html).
    _slideshowCovered(panel) {
      if (!panel || !panel.querySelector('.sensitive-cover')) return false;
      return document.documentElement.matches('[data-blur-sensitive]:not([data-reveal-sensitive-in-viewer])');
    },

    // Warms the cache with the next image's bytes, so its turn does not open
    // on a blank stage. A row may name them (an album shared with the
    // viewer: data-content-url); Files' content endpoint otherwise.
    _preloadNextImage() {
      const files = this._getViewableFiles();
      const idx = files.findIndex((f) => f.uuid === this.fileUuid);
      const next = idx >= 0 ? files[idx + 1] : null;
      if (!next || !(next.fileType || '').startsWith('image/')) return;
      const row = this._row(next.uuid);
      const url = (row && row.dataset.contentUrl) || `/api/v1/files/${next.uuid}/content`;
      const img = new Image();
      img.decoding = 'async';
      img.src = url;
    },

    _enterSlideshowFullscreen() {
      const frame = this.$refs.frame;
      if (document.fullscreenElement || !frame || !frame.requestFullscreen) return;
      frame.requestFullscreen().then(
        () => { this._slideshowFullscreen = true; },
        () => {},
      );
    },

    _exitSlideshowFullscreen() {
      if (!this._slideshowFullscreen) return;
      this._slideshowFullscreen = false;
      if (document.fullscreenElement && document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
      }
    },

    // Space plays and pauses, Escape stops; both only while nothing else
    // that answers them (a button, a field, a video's controls) has focus.
    // Returns whether the key was the slideshow's.
    _slideshowKeydown(event) {
      if (!this.slideshowEnabled || !this.open) return false;
      if (event.key === 'Escape' && this.slideshowPlaying) {
        event.preventDefault();
        this.stopSlideshow();
        return true;
      }
      if (event.key !== ' ' || event.ctrlKey || event.metaKey || event.altKey) return false;
      const target = event.target;
      if (target && target.closest && target.closest('button, a, input, textarea, select, video, audio, [contenteditable]')) {
        return false;
      }
      if (!this.slideshowPlaying && !this.canSlideshow()) return false;
      event.preventDefault();
      this.toggleSlideshow();
      return true;
    },

    _listenSlideshow() {
      // Leaving full screen (the browser's own Escape) ends the slideshow.
      this._on(document, 'fullscreenchange', () => {
        if (this._slideshowFullscreen && !document.fullscreenElement) {
          this._slideshowFullscreen = false;
          this.stopSlideshow();
        }
      });
      this._on(document, 'visibilitychange', () => {
        if (!this.slideshowPlaying) return;
        if (document.hidden) this._clearSlideshowStep();
        else if (!this.viewerLoading) this._onShown(document.getElementById(this._viewerPanelId()));
      });
      // An opener may ask for the slideshow (the album's play button): this
      // listener runs after the shell's, which has opened the file.
      this._on(window, this._openEvent, (e) => {
        if (e.detail && e.detail.slideshow) this.startSlideshow();
      });
    },
  };
};
