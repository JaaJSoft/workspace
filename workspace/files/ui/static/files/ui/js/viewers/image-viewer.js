window.imageViewerData = function imageViewerData() {
  return {
    zoomLevel: 1,
    rotation: 0,

    init() {
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') {
          lucide.createIcons();
        }
      });
    },

    zoom(delta) {
      this.zoomLevel = Math.max(0.1, Math.min(5, this.zoomLevel + delta));
    },

    rotate(degrees) {
      this.rotation = (this.rotation + degrees) % 360;
    },

    reset() {
      this.zoomLevel = 1;
      this.rotation = 0;
    }
  };
};
