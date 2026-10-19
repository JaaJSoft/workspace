// Shared state and loading for the file properties side panel, hosted by the
// files browser and the photos timeline. Spread into the host component
// (`...propertiesPanelMixin()`) and include files/ui/partials/properties_panel.html
// inside it: the panel's content is /files/properties/<uuid>, swapped into
// #properties-content through alpine-ajax.
//
// The panel shows in the address as ?properties=<uuid>: opening it adds a
// history entry, so back closes it - on a phone, where it covers the page,
// that is the only way out the user reaches for. The host calls
// followPropertiesUrl() from its init() and on every `nav:restore`.
//
// The content partial expects more of its host than this mixin: tagsMixin()
// for its tag dropdown (which works on `selectedFile`), and the share modal
// and comments scripts on the page.
window.propertiesPanelMixin = function propertiesPanelMixin() {
  const PARAM = 'properties';

  function addressed() {
    return new URLSearchParams(window.location.search).get(PARAM);
  }

  return {
    showPropertiesPanel: false,
    propertiesUuid: null,
    propertiesNodeType: 'file',
    propertiesLoading: false,
    propertiesError: null,
    // Current panel target for the tags mixin ({ uuid, tags }), seeded by
    // the properties partial's x-init when the file is taggable.
    selectedFile: null,

    // Optional host hook, run on each open.
    _onPropertiesOpen() {},

    // `updateUrl` false leaves the history alone: a back or forward that
    // already landed on the entry, or a reload of the file on show.
    openPropertiesPanel(uuid, nodeType, updateUrl = true) {
      // Asking again for the file already shown closes the panel.
      if (this.showPropertiesPanel && this.propertiesUuid === uuid) {
        this.closePropertiesPanel(updateUrl);
        return;
      }

      const wasOpen = this.showPropertiesPanel;
      this.propertiesUuid = uuid;
      this.propertiesNodeType = nodeType || 'file';
      this.propertiesError = null;
      this.propertiesLoading = true;
      this.showPropertiesPanel = true;
      // Reset the tags target — the incoming partial reseeds it (taggable
      // files only), so a previous file's tags can't leak into this one.
      this.selectedFile = null;
      this._onPropertiesOpen();

      // Opening adds an entry; moving the open panel to another file only
      // rewrites it.
      if (updateUrl) {
        const url = new URL(window.location);
        url.searchParams.set(PARAM, uuid);
        if (wasOpen) window.navHistory.replace(url, window.navHistory.state());
        else window.navHistory.push(url);
      }

      const onError = () => { this.propertiesError = 'Failed to load properties'; };
      const onAfter = () => { this.propertiesLoading = false; };
      this.$el.addEventListener('ajax:error', onError, { once: true });
      this.$el.addEventListener('ajax:after', onAfter, { once: true });
      this.$ajax(`/files/properties/${uuid}`, { target: 'properties-content' });
    },

    // Re-fetch the file on show, after a change made elsewhere (a share, a
    // rename). Going through openPropertiesPanel with the same uuid would
    // close the panel instead.
    reloadPropertiesPanel() {
      if (!this.showPropertiesPanel || !this.propertiesUuid) return;
      const uuid = this.propertiesUuid;
      this.propertiesUuid = null;
      this.openPropertiesPanel(uuid, this.propertiesNodeType, false);
    },

    // Bound to the viewer's `viewer-file-shown`: an open panel moves to the
    // file the viewer stepped to, or closes on one it may not describe.
    followViewer(detail) {
      if (!this.showPropertiesPanel || !detail || detail.uuid === this.propertiesUuid) return;
      if (detail.properties) this.openPropertiesPanel(detail.uuid, 'file');
      else this.closePropertiesPanel();
    },

    // Whether the address names the file on show: a listing swapped back in
    // by a back or forward may keep the panel its entry had open.
    propertiesAddressed() {
      return this.showPropertiesPanel && addressed() === this.propertiesUuid;
    },

    // The panel follows ?properties, writing no history.
    followPropertiesUrl() {
      const uuid = addressed();
      if (uuid && isValidUuid(uuid)) {
        if (this.propertiesAddressed()) return;
        const row = document.querySelector(`[data-uuid="${uuid}"][data-node-type]`);
        this.openPropertiesPanel(uuid, row ? row.dataset.nodeType : 'file', false);
      } else if (this.showPropertiesPanel) {
        this.closePropertiesPanel(false);
      }
    },

    closePropertiesPanel(updateUrl = true) {
      this.showPropertiesPanel = false;
      this.propertiesUuid = null;
      this.propertiesError = null;
      this.selectedFile = null;

      // A navigation that took the panel's place already wrote its own
      // address, without the parameter.
      if (!updateUrl || addressed() === null) return;
      const url = new URL(window.location);
      url.searchParams.delete(PARAM);
      window.navHistory.returnTo(url, window.navHistory.state());
    },
  };
};
