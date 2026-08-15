// Live refresh of the dashboard app-grid badges.
//
// The notifications SSE provider pushes a "notifications.count" event on
// every notification create/read, and sse.js re-dispatches it on window.
// The event only carries the global unread total - the per-module counts
// live server-side (get_unread_badges) - so the component re-fetches the
// grid fragment instead of patching counts client-side.
function dashboardBadges(refreshUrl) {
  return {
    _timer: null,
    _sawInitialCount: false,
    _onCount: null,
    _onReconnect: null,
    init() {
      this._onCount = () => {
        // The stream sends a count event right after connecting; the grid
        // was just server-rendered, so that first event carries no news.
        if (!this._sawInitialCount) {
          this._sawInitialCount = true;
          return;
        }
        this.scheduleRefresh();
      };
      // A reopened stream may have missed pushes - re-sync immediately.
      this._onReconnect = () => this.refresh();
      window.addEventListener('sse:notifications.count', this._onCount);
      window.addEventListener('sse:reconnect', this._onReconnect);
    },
    destroy() {
      window.removeEventListener('sse:notifications.count', this._onCount);
      window.removeEventListener('sse:reconnect', this._onReconnect);
      if (this._timer) {
        clearTimeout(this._timer);
        this._timer = null;
      }
    },
    // One count event fires per notification row, so a burst (mark-all-read,
    // an incoming chat stream) must coalesce into a single fragment fetch.
    scheduleRefresh() {
      if (this._timer) return;
      this._timer = setTimeout(() => {
        this._timer = null;
        this.refresh();
      }, 400);
    },
    refresh() {
      // An immediate refresh supersedes any queued debounced one.
      if (this._timer) {
        clearTimeout(this._timer);
        this._timer = null;
      }
      this.$ajax(refreshUrl, { target: 'dashboard-modules-grid' });
    },
  };
}
window.dashboardBadges = dashboardBadges;
