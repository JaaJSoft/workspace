// The map under a photo's coordinates in the Photo section of the properties
// panel (photos/ui/partials/properties_section.html), on every page hosting
// the panel. alpine-ajax swaps the section in on each selection, so the map
// lives exactly as long as it: built in init(), removed in destroy().

// A pin drawn in the module colour; Leaflet's own marker is a PNG.
const PHOTO_MAP_PIN = '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">'
  + '<path d="M12 1.5a7.5 7.5 0 0 0-7.5 7.5c0 5.6 7.5 13.5 7.5 13.5s7.5-7.9 7.5-13.5A7.5 7.5 0 0 0 12 1.5z" '
  + 'fill="currentColor" stroke="white" stroke-width="1.5"/>'
  + '<circle cx="12" cy="9" r="2.75" fill="white"/></svg>';

// The page's Referrer-Policy (same-origin) would send tile servers no
// Referer, which OpenStreetMap's policy blocks; the origin alone keeps the
// page's path and query, the map's view included, to ourselves.
// The library map (library_map.js) shares it.
const PHOTO_MAP_TILE_REFERRER_POLICY = 'strict-origin-when-cross-origin';

// Turns the `photos` / `show_map` setting on or off, then has the host reload
// the panel so the section renders its new state: the reload cannot start
// from in here, the swap removes this element with the request's listeners.
// Spread into photoMap(), and used alone by the "Show on a map" offer.
window.photoMapPreference = function photoMapPreference() {
  return {
    savingMapPreference: false,

    async setShowMap(enabled) {
      if (this.savingMapPreference) return;
      this.savingMapPreference = true;
      try {
        const response = await fetch('/api/v1/settings/photos/show_map', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ value: enabled }),
        });
        if (!response.ok) throw new Error(String(response.status));
      } catch (_) {
        this.savingMapPreference = false;
        window.AppAlert.error('Could not save the preference');
        return;
      }
      window.dispatchEvent(new CustomEvent('photo-map-preference', { detail: { enabled } }));
      window.dispatchEvent(new CustomEvent('properties-section-changed'));
    },
  };
};

// daisyUI declares a color-scheme on every theme: no list of dark themes to
// keep in sync with the theme picker.
function photoMapIsDark() {
  return getComputedStyle(document.documentElement).colorScheme === 'dark';
}

window.photoMap = function photoMap(config) {
  // Kept out of the component's data: Alpine would wrap the Leaflet objects
  // in reactive proxies, which breaks them.
  let map = null;
  let tiles = null;
  let shownUrl = null;
  let resizeObserver = null;
  let themeObserver = null;

  const tileUrl = () => (config.tileUrlDark && photoMapIsDark() ? config.tileUrlDark : config.tileUrl);

  return {
    ...window.photoMapPreference(),

    init() {
      if (!window.L) return;
      const position = [config.latitude, config.longitude];
      // No wheel zoom: scrolling the panel would zoom the map instead.
      map = L.map(this.$refs.canvas, { scrollWheelZoom: false }).setView(position, config.zoom);
      map.attributionControl.setPrefix(false);
      shownUrl = tileUrl();
      tiles = L.tileLayer(shownUrl, {
        attribution: config.attribution,
        maxZoom: 19,
        referrerPolicy: PHOTO_MAP_TILE_REFERRER_POLICY,
      }).addTo(map);
      L.marker(position, {
        icon: L.divIcon({
          html: PHOTO_MAP_PIN,
          className: 'text-module',
          iconSize: [28, 28],
          iconAnchor: [14, 27],
        }),
        keyboard: false,
        interactive: false,
      }).addTo(map);
      // The panel slides open around the section, after Leaflet measured it.
      resizeObserver = new ResizeObserver(() => map && map.invalidateSize());
      resizeObserver.observe(this.$refs.canvas);
      if (config.tileUrlDark) {
        // The theme toggle and the preferences page switch it in place.
        themeObserver = new MutationObserver(() => {
          const url = tileUrl();
          if (!tiles || url === shownUrl) return;
          shownUrl = url;
          tiles.setUrl(url);
        });
        themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
      }
    },

    destroy() {
      if (resizeObserver) resizeObserver.disconnect();
      if (themeObserver) themeObserver.disconnect();
      resizeObserver = null;
      themeObserver = null;
      tiles = null;
      if (map) map.remove();
      map = null;
    },
  };
};
