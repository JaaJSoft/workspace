// The Map page (photos/ui/map.html): the located photos of a library on a
// map, grouped while they crowd at the zoom shown, and beside it the grid of
// those in the area on screen (photos/ui/partials/map_photos.html).
//
// Every point comes down at once (GET /api/v1/photos/map) and Supercluster
// groups them in the browser: a zoom or a pan regroups in a few
// milliseconds, with no request. Only the grid asks the server, once the
// map has settled.

// A point, as services/library_map.py lists it.
const MAP_POINT = { uuid: 0, lat: 1, lng: 2, month: 3, video: 4, sensitive: 5, thumbnail: 6 };
// Photos closer than this on screen, in px, share a marker: about the
// width of one, so markers barely overlap.
const LIBRARY_MAP_RADIUS = 64;
// Leaflet's tile size. Supercluster measures its radius against its own
// tile extent (512 by default), which would halve the radius on screen.
const LIBRARY_MAP_TILE_EXTENT = 256;
// The closest the map opens on when the whole library fits in one place.
const LIBRARY_MAP_FIT_ZOOM = 15;
// How long markers take to split from their group or merge back, in ms.
const LIBRARY_MAP_SPLIT_MS = 320;
// Past this many markers coming or going at once, they appear in place.
const LIBRARY_MAP_ANIMATED_MAX = 300;
// The grid and the URL follow the map once it has rested this long, in ms.
const LIBRARY_MAP_FOLLOW_MS = 250;
const LIBRARY_MAP_MONTH_FORMAT = new Intl.DateTimeFormat('en', {
  month: 'short', year: 'numeric', timeZone: 'UTC',
});

const LIBRARY_MAP_IMAGE_ICON = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" '
  + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/>'
  + '<path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>';
const LIBRARY_MAP_PLAY_ICON = '<svg viewBox="0 0 24 24" width="10" height="10" aria-hidden="true">'
  + '<path d="M7 4.5v15l13-7.5z" fill="currentColor"/></svg>';

function libraryMapWrapLongitude(lng) {
  if (lng >= -180 && lng <= 180) return lng;
  return ((((lng + 180) % 360) + 360) % 360) - 180;
}

// The pure half of the page, apart so it can be tested without a map.
window.libraryMapUtils = {
  // A month counted from year 0 (year * 12 + month - 1), as the points carry it.
  monthToken(index) {
    const year = Math.floor(index / 12);
    const month = (index % 12) + 1;
    return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`;
  },

  parseMonth(token) {
    const match = /^(\d{4})-(\d{2})$/.exec(token || '');
    if (!match) return null;
    const month = Number(match[2]);
    if (month < 1 || month > 12) return null;
    return Number(match[1]) * 12 + month - 1;
  },

  monthLabel(index) {
    return LIBRARY_MAP_MONTH_FORMAT.format(new Date(Date.UTC(Math.floor(index / 12), index % 12, 1)));
  },

  // The box the server reads (west,south,east,north), from the edges of
  // the map on screen: longitudes wrapped into [-180, 180], and every one
  // of them once the map shows a world or more.
  bbox(west, south, east, north) {
    const box = [west, Math.max(south, -90), east, Math.min(north, 90)];
    if (east - west >= 360) {
      box[0] = -180;
      box[2] = 180;
    } else {
      box[0] = libraryMapWrapLongitude(west);
      box[2] = libraryMapWrapLongitude(east);
    }
    return box;
  },

  // The months the dated points span, and how many fall in each; null
  // when none is dated.
  histogram(points) {
    let first = Infinity;
    let last = -Infinity;
    for (const point of points) {
      const month = point[MAP_POINT.month];
      if (month === null) continue;
      if (month < first) first = month;
      if (month > last) last = month;
    }
    if (first === Infinity) return null;
    const counts = new Array(last - first + 1).fill(0);
    for (const point of points) {
      const month = point[MAP_POINT.month];
      if (month !== null) counts[month - first] += 1;
    }
    return { first, last, counts };
  },

  // The number on a group's marker: 1.2k past a thousand, 12k past ten.
  countLabel(count) {
    if (count < 1000) return String(count);
    if (count < 10000) return `${(count / 1000).toFixed(1).replace(/\.0$/, '')}k`;
    return `${Math.round(count / 1000)}k`;
  },

  thumbnailUrl(point) {
    const version = point[MAP_POINT.thumbnail];
    if (version === null) return '';
    const url = `/api/v1/files/${point[MAP_POINT.uuid]}/thumbnail?size=128`;
    return version ? `${url}&v=${encodeURIComponent(version)}` : url;
  },

  // *lng* in the world copy closest to *centerLng*: past the antimeridian,
  // a marker shows on the side of the map in view.
  nearestCopy(lng, centerLng) {
    return lng + 360 * Math.round((centerLng - lng) / 360);
  },
};

window.libraryMap = function libraryMap() {
  const utils = window.libraryMapUtils;
  // Kept out of the component's data: Alpine would wrap the Leaflet objects
  // in reactive proxies, which breaks them, and proxying tens of thousands
  // of points would cost every read.
  let config = null;
  let map = null;
  let tiles = null;
  let shownTileUrl = null;
  let layer = null;
  let index = null;
  let points = [];
  // The markers on the map, by "g<cluster id>" for a group and "p<point>"
  // for a lone photo: a photo alone at two zooms keeps its marker.
  const markers = new Map();
  let shownZoom = null;
  // The markers leaving with the animation in flight, and its end.
  let animation = null;
  let followTimer = null;
  let periodFrame = null;
  let gridUrl = null;
  let resizeObserver = null;
  let themeObserver = null;

  const tileUrl = () => (config.tileUrlDark && photoMapIsDark() ? config.tileUrlDark : config.tileUrl);

  return {
    mapLoading: true,
    mapFailed: false,
    mapEmpty: false,
    // The months the period slider spans, null while the library spans
    // fewer than two; the bars above it, in % of the busiest month; and the
    // two handles, as offsets from the first month.
    periodSpan: null,
    periodBars: [],
    periodFrom: 0,
    periodTo: 0,

    init() {
      config = JSON.parse(document.getElementById('library-map-data').textContent);
      if (!window.L || !window.Supercluster) return;
      map = L.map(this.$refs.canvas, {
        zoomControl: false,
        worldCopyJump: true,
        // Low enough for a narrow map to fit a library spread over the world.
        minZoom: 1,
        maxZoom: config.maxZoom,
      });
      map.attributionControl.setPrefix(false);
      shownTileUrl = tileUrl();
      tiles = L.tileLayer(shownTileUrl, { attribution: config.attribution, maxZoom: config.maxZoom }).addTo(map);
      layer = L.layerGroup().addTo(map);
      if (config.view) {
        map.setView([config.view.lat, config.view.lng], Math.round(config.view.zoom));
      } else {
        map.setView([20, 0], 2);
      }
      map.on('zoomstart', () => this._settle());
      map.on('moveend', () => {
        this._render(true);
        this._follow();
      });
      // The properties panel opening narrows the map without a resize.
      resizeObserver = new ResizeObserver(() => map && map.invalidateSize());
      resizeObserver.observe(this.$refs.canvas);
      if (config.tileUrlDark) {
        themeObserver = new MutationObserver(() => {
          const url = tileUrl();
          if (!tiles || url === shownTileUrl) return;
          shownTileUrl = url;
          tiles.setUrl(url);
        });
        themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
      }
      this._loadPoints();
    },

    destroy() {
      clearTimeout(followTimer);
      if (periodFrame) cancelAnimationFrame(periodFrame);
      if (animation) clearTimeout(animation.timer);
      if (resizeObserver) resizeObserver.disconnect();
      if (themeObserver) themeObserver.disconnect();
      markers.clear();
      if (map) map.remove();
      map = null;
      tiles = null;
      layer = null;
      index = null;
      points = [];
      animation = null;
    },

    async _loadPoints() {
      try {
        const response = await fetch(config.pointsUrl, { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error(String(response.status));
        points = (await response.json()).points;
      } catch (_) {
        this.mapLoading = false;
        this.mapFailed = true;
        // The grid asks the server on its own: it may still answer.
        this._loadGrid();
        return;
      }
      if (!map) return;
      this._setUpPeriod();
      this._buildIndex();
      this.mapLoading = false;
      this.mapEmpty = points.length === 0;
      if (!config.view && points.length) {
        this._fit(false);
      }
      this._render(false);
      this._loadGrid();
    },

    // ── Grouping and markers ─────────────────────────────

    _shownPoints() {
      if (!this.periodActive()) return points.map((_, i) => i);
      const first = this.periodSpan.first + this.periodFrom;
      const last = this.periodSpan.first + this.periodTo;
      const shown = [];
      points.forEach((point, i) => {
        const month = point[MAP_POINT.month];
        if (month !== null && month >= first && month <= last) shown.push(i);
      });
      return shown;
    },

    // A group stands for the newest photo in it: the points come newest
    // first, so that is the lowest index.
    _buildIndex() {
      index = new Supercluster({
        radius: LIBRARY_MAP_RADIUS,
        extent: LIBRARY_MAP_TILE_EXTENT,
        maxZoom: config.maxZoom,
        map: (props) => ({ best: props.i }),
        reduce: (accumulated, props) => {
          if (props.best < accumulated.best) accumulated.best = props.best;
        },
      });
      index.load(this._shownPoints().map((i) => ({
        type: 'Feature',
        properties: { i },
        geometry: { type: 'Point', coordinates: [points[i][MAP_POINT.lng], points[i][MAP_POINT.lat]] },
      })));
    },

    // Fits the photos the period shows, or every one, clear of the controls
    // on the right and of the period slider at the bottom.
    _fit(animate = true) {
      const shown = this._shownPoints();
      if (!shown.length) return;
      const bounds = L.latLngBounds(shown.map((i) => [points[i][MAP_POINT.lat], points[i][MAP_POINT.lng]]));
      map.fitBounds(bounds, {
        paddingTopLeft: [48, 48],
        paddingBottomRight: [64, this.periodSpan ? 130 : 48],
        maxZoom: LIBRARY_MAP_FIT_ZOOM,
        animate,
      });
    },

    fitAll() {
      if (map) this._fit(true);
    },

    zoomIn() {
      if (map) map.zoomIn();
    },

    zoomOut() {
      if (map) map.zoomOut();
    },

    // What the map shows now: the groups and lone photos of the area on
    // screen (and a margin, so a short pan finds them placed), diffed
    // against the markers already there. After a zoom, the photos of a group
    // that split fly out of it, and those of groups that merge fly in.
    _render(animate) {
      if (!map || !index) return;
      this._settle();
      const zoom = Math.round(map.getZoom());
      const zoomedIn = shownZoom !== null && zoom > shownZoom;
      const zoomedOut = shownZoom !== null && zoom < shownZoom;
      shownZoom = zoom;
      const bounds = map.getBounds().pad(0.25);
      const box = utils.bbox(bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth());
      const centerLng = map.getCenter().lng;

      const wanted = new Map();
      for (const feature of index.getClusters(box, zoom)) {
        const [lng, lat] = feature.geometry.coordinates;
        const props = feature.properties;
        const group = !!props.cluster;
        const key = group ? `g${props.cluster_id}` : `p${props.i}`;
        wanted.set(key, {
          group,
          id: props.cluster_id,
          count: group ? props.point_count : 1,
          best: group ? props.best : props.i,
          latlng: L.latLng(lat, utils.nearestCopy(lng, centerLng)),
        });
      }

      const leaving = [];
      for (const [key, marker] of markers) {
        if (wanted.has(key)) continue;
        markers.delete(key);
        leaving.push(marker);
      }
      const entering = [];
      for (const [key, item] of wanted) {
        const marker = markers.get(key);
        if (marker) {
          // The same place, maybe in another world copy.
          if (!marker.getLatLng().equals(item.latlng)) marker.setLatLng(item.latlng);
          continue;
        }
        const created = this._marker(item);
        markers.set(key, created);
        entering.push(created);
      }

      const moving = animate && (zoomedIn || zoomedOut)
        && entering.length + leaving.length <= LIBRARY_MAP_ANIMATED_MAX;
      if (!moving) {
        leaving.forEach((marker) => layer.removeLayer(marker));
        entering.forEach((marker) => {
          marker.options.entering = animate;
          layer.addLayer(marker);
        });
        return;
      }
      this._animate(entering, leaving, zoomedIn);
    },

    // Zooming in, a new marker starts on the group it came out of: the
    // leaving marker nearest it on screen. Zooming out, a leaving marker
    // travels to the group that took it in. Either way the leaving ones fade.
    _animate(entering, leaving, zoomedIn) {
      const container = map.getContainer();
      const point = (marker) => map.latLngToLayerPoint(marker.getLatLng());
      const nearest = (marker, among) => {
        const from = point(marker);
        let best = null;
        let bestDistance = Infinity;
        for (const other of among) {
          const distance = from.distanceTo(point(other));
          if (distance < bestDistance) {
            best = other;
            bestDistance = distance;
          }
        }
        return best;
      };
      const flights = [];
      if (zoomedIn) {
        for (const marker of entering) {
          const origin = nearest(marker, leaving);
          if (origin) flights.push([marker, marker.getLatLng(), origin.getLatLng()]);
        }
      }
      const landings = [];
      if (!zoomedIn) {
        const targets = entering.length ? entering : Array.from(markers.values());
        for (const marker of leaving) {
          const target = nearest(marker, targets);
          if (target) landings.push([marker, target.getLatLng()]);
        }
      }
      flights.forEach(([marker, , origin]) => marker.setLatLng(origin));
      entering.forEach((marker) => {
        marker.options.entering = !zoomedIn;
        layer.addLayer(marker);
      });
      // The start positions must be laid out before the transition is on,
      // or the markers would appear at their end without travelling.
      void container.offsetWidth;
      container.classList.add('library-map-moving');
      flights.forEach(([marker, target]) => marker.setLatLng(target));
      landings.forEach(([marker, target]) => marker.setLatLng(target));
      leaving.forEach((marker) => marker.setOpacity(0));
      animation = {
        leaving,
        timer: setTimeout(() => this._settle(), LIBRARY_MAP_SPLIT_MS),
      };
    },

    // Ends the animation in flight at once: a zoom or a regroup does not
    // wait for markers still on their way.
    _settle() {
      if (!animation) return;
      clearTimeout(animation.timer);
      animation.leaving.forEach((marker) => layer && layer.removeLayer(marker));
      animation = null;
      if (map) map.getContainer().classList.remove('library-map-moving');
    },

    _marker(item) {
      const point = points[item.best];
      const element = document.createElement('div');
      element.className = item.group ? 'library-map-marker is-group' : 'library-map-marker';
      // The page's sensitive-blur rules (input.css) blur the picture.
      element.dataset.sensitive = String(point[MAP_POINT.sensitive]);
      const frame = document.createElement('div');
      frame.className = 'library-map-frame';
      const url = utils.thumbnailUrl(point);
      if (url) {
        const image = document.createElement('img');
        image.src = url;
        image.alt = '';
        image.decoding = 'async';
        image.draggable = false;
        frame.append(image);
      } else {
        frame.classList.add('is-empty');
        frame.innerHTML = LIBRARY_MAP_IMAGE_ICON;
      }
      element.append(frame);
      if (item.group) {
        const count = document.createElement('span');
        count.className = 'library-map-count';
        count.textContent = utils.countLabel(item.count);
        element.append(count);
      } else if (point[MAP_POINT.video]) {
        const play = document.createElement('span');
        play.className = 'library-map-play';
        play.innerHTML = LIBRARY_MAP_PLAY_ICON;
        element.append(play);
      }
      const size = item.group ? 60 : 52;
      const marker = L.marker(item.latlng, {
        icon: L.divIcon({
          html: element,
          className: 'library-map-icon',
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        }),
        title: item.group ? `${item.count} photos` : (point[MAP_POINT.video] ? 'Video' : 'Photo'),
        riseOnHover: true,
      });
      marker.on('add', () => {
        const icon = marker.getElement();
        if (icon && marker.options.entering) icon.classList.add('is-entering');
      });
      marker.on('click', () => this._open(item));
      return marker;
    },

    // A group zooms in until it splits; one that never will (its photos
    // share a place) opens its newest photo, the grid holding the others.
    _open(item) {
      if (item.group) {
        const zoom = index.getClusterExpansionZoom(item.id);
        if (zoom <= config.maxZoom) {
          map.flyTo(item.latlng, zoom, { duration: 0.5 });
          return;
        }
      }
      this._openPhoto(points[item.best][MAP_POINT.uuid]);
    },

    // From the grid's tile when it is loaded there, so the viewer steps
    // through the grid; from the file otherwise.
    async _openPhoto(uuid) {
      const tile = document.querySelector(`#map-photos [data-uuid="${uuid}"]`);
      let detail = null;
      if (tile) {
        detail = { uuid, name: tile.dataset.displayName, type: tile.dataset.fileType };
      } else {
        try {
          const response = await fetch(`/api/v1/files/${uuid}`);
          if (!response.ok) return;
          const file = await response.json();
          detail = { uuid, name: file.name, type: file.type };
        } catch (_) {
          return;
        }
      }
      window.dispatchEvent(new CustomEvent('open-file-viewer', { detail }));
    },

    // ── The grid and the URL ─────────────────────────────

    _follow() {
      if (this.mapLoading) return;
      clearTimeout(followTimer);
      followTimer = setTimeout(() => {
        this._writeUrl();
        this._loadGrid();
      }, LIBRARY_MAP_FOLLOW_MS);
    },

    _period() {
      if (!this.periodActive()) return null;
      return {
        from: utils.monthToken(this.periodSpan.first + this.periodFrom),
        to: utils.monthToken(this.periodSpan.first + this.periodTo),
      };
    },

    // Replaced, never pushed: moving the map is not a page to go back to.
    _writeUrl() {
      if (!map) return;
      const url = new URL(window.location.href);
      const center = map.getCenter().wrap();
      url.searchParams.set('lat', center.lat.toFixed(5));
      url.searchParams.set('lng', center.lng.toFixed(5));
      url.searchParams.set('z', String(map.getZoom()));
      const period = this._period();
      if (period) {
        url.searchParams.set('from', period.from);
        url.searchParams.set('to', period.to);
      } else {
        url.searchParams.delete('from');
        url.searchParams.delete('to');
      }
      if (url.href !== window.location.href) window.navHistory.replace(url);
    },

    _loadGrid() {
      if (!map || this.mapLoading) return;
      const bounds = map.getBounds();
      const box = utils.bbox(bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth());
      const url = new URL(config.photosUrl, window.location.origin);
      url.searchParams.set('bbox', box.map((value) => value.toFixed(5)).join(','));
      const period = this._period();
      if (period) {
        url.searchParams.set('from', period.from);
        url.searchParams.set('to', period.to);
      }
      const next = url.pathname + url.search;
      if (next === gridUrl) return;
      gridUrl = next;
      // The tiles a selection names leave with the grid.
      if (typeof this.clearSelection === 'function') this.clearSelection();
      this.$ajax(next, { target: 'map-photos', focus: false }).catch(() => {});
    },

    // ── The period ───────────────────────────────────────

    _setUpPeriod() {
      const histogram = utils.histogram(points);
      if (!histogram || histogram.first === histogram.last) {
        this.periodSpan = null;
        return;
      }
      const busiest = Math.max(...histogram.counts);
      this.periodSpan = { first: histogram.first, last: histogram.last };
      // A month with a photo keeps a sliver of a bar, however quiet.
      this.periodBars = histogram.counts.map((count) => (count ? Math.max(6, Math.round((count / busiest) * 100)) : 0));
      const span = histogram.last - histogram.first;
      let from = 0;
      let to = span;
      if (config.period) {
        const first = utils.parseMonth(config.period.from);
        const last = utils.parseMonth(config.period.to);
        if (first !== null && last !== null) {
          from = Math.min(Math.max(first - histogram.first, 0), span);
          to = Math.min(Math.max(last - histogram.first, from), span);
        }
      }
      this.periodFrom = from;
      this.periodTo = to;
    },

    periodLength() {
      return this.periodSpan ? this.periodSpan.last - this.periodSpan.first : 0;
    },

    periodActive() {
      return !!this.periodSpan && (this.periodFrom > 0 || this.periodTo < this.periodLength());
    },

    periodLabel() {
      if (!this.periodSpan) return '';
      const from = utils.monthLabel(this.periodSpan.first + this.periodFrom);
      const to = utils.monthLabel(this.periodSpan.first + this.periodTo);
      return from === to ? from : `${from} - ${to}`;
    },

    periodBarShown(offset) {
      return offset >= this.periodFrom && offset <= this.periodTo;
    },

    // The handles never cross: the one moved pushes the other along.
    onPeriodInput(handle) {
      if (handle === 'from' && this.periodFrom > this.periodTo) this.periodTo = this.periodFrom;
      if (handle === 'to' && this.periodTo < this.periodFrom) this.periodFrom = this.periodTo;
      this._applyPeriod();
    },

    resetPeriod() {
      this.periodFrom = 0;
      this.periodTo = this.periodLength();
      this._applyPeriod();
    },

    // Regrouped once a frame at most while a handle is dragged.
    _applyPeriod() {
      if (periodFrame) return;
      periodFrame = requestAnimationFrame(() => {
        periodFrame = null;
        if (!map) return;
        this._buildIndex();
        markers.forEach((marker) => layer.removeLayer(marker));
        markers.clear();
        this._render(true);
        this._follow();
      });
    },
  };
};
