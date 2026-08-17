// Shared loading logic for the hosts of the #viewer-panel element: the
// files viewer modal, the chat attachment viewer modal and the notes
// editor pane. Spread into the host component (`...viewerPanelMixin()`)
// and bind both guards on the component root:
// `@ajax:missing="_viewerMissing($event)" @ajax:merge="_viewerMerge($event)"`.
//
// Loads go through alpine-ajax into the #viewer-panel element every viewer
// response carries (see render_viewer_panel in workspace/files/ui/viewers.py),
// so for that element only the most recently issued request merges: a slow
// earlier response can neither paint its markup nor execute its scripts.
// Viewer <script> elements execute in place inside the merged panel (fragment
// parsing keeps them live, unlike innerHTML) and leave with the next swap.
window.viewerPanelMixin = function viewerPanelMixin() {
  return {
    viewerLoading: false,
    viewerError: null,
    // Load-vs-load staleness is arbitrated by alpine-ajax (newest request
    // per target wins). This pair covers what that cannot see: a superseded
    // load's completion must not clear the spinner the winning load still
    // owns, and a load canceled by teardown (modal closed, note deleted)
    // must not merge its late response - _viewerActiveSeq trails the seq of
    // the most recently issued load, so the two diverge exactly when a
    // cancel happened after that load went out.
    _viewerStateSeq: 0,
    _viewerActiveSeq: 0,

    _viewerPanelId() { return 'viewer-panel'; },

    // Empty the panel after letting the mounted viewer dispose (Monaco and
    // Crepe listen for viewer-cleanup). The panel element itself stays in
    // the DOM: it is the id anchor the next merge replaces.
    clearViewerPanel() {
      window.dispatchEvent(new CustomEvent('viewer-cleanup'));
      const panel = document.getElementById(this._viewerPanelId());
      if (panel) panel.replaceChildren();
    },

    // Teardown for close/delete/destroy paths: cancel any in-flight load,
    // then dispose and empty the panel. Distinct from the clear inside
    // loadViewerPanel, which must NOT cancel the load it is preparing.
    teardownViewerPanel() {
      this.cancelViewerLoad();
      this.clearViewerPanel();
    },

    // alpine-ajax exposes no abort handle, so a canceled load's request
    // still completes - this makes its completion inert: _viewerMerge
    // refuses the response and the seq checks in loadViewerPanel leave the
    // flags alone.
    cancelViewerLoad() {
      ++this._viewerStateSeq;
      this.viewerLoading = false;
    },

    // Bound to @ajax:missing on the host component's root. alpine-ajax's default for
    // a 2xx response that lacks the target id (login redirect, error page)
    // is to REMOVE the live target; cancelling keeps the panel and turns the
    // response into "nothing merged" instead of a thrown RenderError.
    _viewerMissing(event) {
      if (event.detail && event.detail.target
          && event.detail.target.id === this._viewerPanelId()) {
        event.preventDefault();
      }
    },

    // Bound to @ajax:merge on the host component's root. Only the most
    // recently issued request ever reaches the merge stage, so a seq behind
    // the current one means that load was canceled after being issued - its
    // response must neither mount markup nor run scripts.
    _viewerMerge(event) {
      if (event.target && event.target.id === this._viewerPanelId()
          && this._viewerActiveSeq !== this._viewerStateSeq) {
        event.preventDefault();
      }
    },

    // Returns whether this load's response was merged into the panel.
    async loadViewerPanel(url) {
      const seq = ++this._viewerStateSeq;
      this._viewerActiveSeq = seq;
      this.clearViewerPanel();
      this.viewerError = null;
      this.viewerLoading = true;
      let merged = false;
      try {
        const render = await this.$ajax(url, {
          target: this._viewerPanelId(),
          focus: false,
        });
        if (seq !== this._viewerStateSeq) return false;
        merged = (render || []).some(Boolean);
        if (!merged) {
          // The guarded ajax:missing path above: nothing merged, panel kept.
          this.viewerError = 'Failed to load viewer';
        }
      } catch (err) {
        if (seq !== this._viewerStateSeq) return false;
        this.viewerError = err.message || 'Failed to load viewer';
      }
      this.viewerLoading = false;
      return merged;
    },
  };
};
