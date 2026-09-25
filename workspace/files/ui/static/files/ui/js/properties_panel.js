// Shared state and loading for the file properties side panel, hosted by the
// files browser and the photos timeline. Spread into the host component
// (`...propertiesPanelMixin()`) and include files/ui/partials/properties_panel.html
// inside it: the panel's content is /files/properties/<uuid>, swapped into
// #properties-content through alpine-ajax.
//
// The content partial expects more of its host than this mixin: tagsMixin()
// for its tag dropdown (which works on `selectedFile`), and the share modal
// and comments scripts on the page.
window.propertiesPanelMixin = function propertiesPanelMixin() {
  return {
    showPropertiesPanel: false,
    propertiesUuid: null,
    propertiesNodeType: 'file',
    propertiesLoading: false,
    propertiesError: null,
    // Current panel target for the tags mixin ({ uuid, tags }), seeded by
    // the properties partial's x-init when the file is taggable.
    selectedFile: null,

    openPropertiesPanel(uuid, nodeType) {
      // Asking again for the file already shown closes the panel.
      if (this.showPropertiesPanel && this.propertiesUuid === uuid) {
        this.closePropertiesPanel();
        return;
      }

      this.propertiesUuid = uuid;
      this.propertiesNodeType = nodeType || 'file';
      this.propertiesError = null;
      this.propertiesLoading = true;
      this.showPropertiesPanel = true;
      // Reset the tags target — the incoming partial reseeds it (taggable
      // files only), so a previous file's tags can't leak into this one.
      this.selectedFile = null;

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
      this.openPropertiesPanel(uuid, this.propertiesNodeType);
    },

    closePropertiesPanel() {
      this.showPropertiesPanel = false;
      this.propertiesUuid = null;
      this.propertiesError = null;
      this.selectedFile = null;
    },
  };
};
