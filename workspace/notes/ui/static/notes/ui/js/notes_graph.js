// Notes graph view: force-graph integration + pure helpers.
// Classic script (no top-level import) so it loads via <script src> and the
// node:vm test loader. force-graph is imported dynamically at runtime inside
// open() (network), so loading this file is side-effect-free.

// Base node radius unit; the actual radius scales with the node's degree (see
// _nodeRadius), matching force-graph's own sqrt(val) * nodeRelSize sizing so the
// drawn circle, the physics collision and the pointer area all agree.
const NODE_REL = 4;

// Upper bound for the auto-frame / reset-view zoom. A small graph is zoomed in
// only up to this (readable, ~2x natural) instead of being blown up to fill the
// canvas; large graphs still zoom out below 1 to fit. Tune here if needed.
const MAX_ZOOM = 2;

// Color "type" of a node, by precedence favorite > journal > regular.
// journalUuid is the user's Journal folder UUID (notes preference); a node is a
// journal note when its parent folder is that journal folder.
function nodeColorKey(node, journalUuid) {
  if (node && node.is_favorite) return 'favorite';
  if (journalUuid && node && node.parent === journalUuid) return 'journal';
  return 'regular';
}

// Resolve a colorKey to an actual CSS color from the active daisyUI theme, so
// the graph follows light/dark. Reads computed colors off a detached probe.
function themePalette() {
  const probe = document.createElement('span');
  probe.style.display = 'none';
  document.body.appendChild(probe);
  const read = (cls, fallback) => {
    probe.className = cls;
    const c = getComputedStyle(probe).color;
    return c || fallback;
  };
  const readBg = (cls, fallback) => {
    probe.className = cls;
    const c = getComputedStyle(probe).backgroundColor;
    return c || fallback;
  };
  const baseContent = read('text-base-content', '#9ca3af');
  const pal = {
    favorite: read('text-warning', '#f59e0b'),
    journal: read('text-success', '#22c55e'),
    regular: baseContent,
    text: baseContent, // node labels drawn on the canvas
    // Thin outline drawn in the background colour, so nodes stay crisp where
    // links or other nodes pass behind them (the Obsidian "cut-out" look).
    ring: readBg('bg-base-100', 'rgba(255,255,255,0.85)'),
  };
  probe.remove();
  return pal;
}

// Single module-level instance (force-graph object is heavy; keep it OUT of any
// Alpine reactive scope). _openGen invalidates in-flight async work when the
// view is left or re-opened, so a slow import/fetch can't revive a torn-down
// graph.
let _fg = null;
let _container = null;
let _resizeObserver = null;
let _palette = null;
let _openGen = 0;
let _fitPending = false; // one-shot: zoom-to-fit once the next layout settles
let _state = {
  scope: 'mine',
  kind: 'all',
  search: '',
  journalUuid: null,
  notesRoot: null,
  onNodeClick: null,
  hoverId: null,
  neighbors: new Set(),
};

// Radius for a node, derived from its degree (stored in node.val by _load).
// Mirrors force-graph's sqrt(val) * nodeRelSize so visuals and physics agree.
function _nodeRadius(n) {
  return Math.sqrt(Math.max(1, n.val || 1)) * NODE_REL;
}

// Faint dot grid drawn behind the graph, in graph coordinates so it pans/zooms
// with the scene. gap is kept constant in *screen* pixels (26 / globalScale),
// which also bounds the loop to ~canvasWidth/26 dots regardless of zoom level.
function _drawGrid(ctx, globalScale) {
  if (!_fg || !_container) return;
  const w = _container.clientWidth;
  const h = _container.clientHeight;
  if (!w || !h || !globalScale) return;
  let tl, br;
  try {
    tl = _fg.screen2GraphCoords(0, 0);
    br = _fg.screen2GraphCoords(w, h);
  } catch (e) {
    return;
  }
  if (!tl || !br) return;
  const gap = 26 / globalScale;
  const dotR = 0.9 / globalScale;
  ctx.save();
  ctx.fillStyle = 'rgba(128,128,128,0.10)';
  for (let x = Math.floor(tl.x / gap) * gap; x <= br.x; x += gap) {
    for (let y = Math.floor(tl.y / gap) * gap; y <= br.y; y += gap) {
      ctx.beginPath();
      ctx.arc(x, y, dotR, 0, 2 * Math.PI);
      ctx.fill();
    }
  }
  ctx.restore();
}

// Zoom level that fits a bboxW x bboxH (graph units) cloud inside a viewW x viewH
// canvas with `pad` px margin, capped at MAX_ZOOM so a small graph is zoomed in
// only moderately (not blown up to fill the canvas, which felt super-zoomed) and
// large graphs still zoom out below 1 to fit. Floored at 0.05.
function fitZoom(bboxW, bboxH, viewW, viewH, pad) {
  const bw = bboxW || 1;
  const bh = bboxH || 1;
  return Math.max(0.05, Math.min(MAX_ZOOM, (viewW - 2 * pad) / bw, (viewH - 2 * pad) / bh));
}

// Centre on the node cloud and zoom to fit (capped, see fitZoom). Shared by the
// auto-frame on load and the reset-view button so both behave identically.
function _frameGraph(ms) {
  if (!_fg || !_container) return;
  const bbox = _fg.getGraphBbox();
  if (!bbox) return;
  const w = _container.clientWidth;
  const h = _container.clientHeight;
  if (!w || !h) return;
  const zoom = fitZoom(bbox.x[1] - bbox.x[0], bbox.y[1] - bbox.y[0], w, h, 40);
  _fg.centerAt((bbox.x[0] + bbox.x[1]) / 2, (bbox.y[0] + bbox.y[1]) / 2, ms);
  _fg.zoom(zoom, ms);
}

// Paint one node: filled circle, thin background-coloured ring, a light halo
// ring when hovered, and the name below. Non-neighbours of the hovered node are
// faded out (focus mode); their labels are dropped to declutter.
function _drawNode(n, ctx) {
  if (n.x === undefined || n.y === undefined) return;
  const r = _nodeRadius(n);
  const dimmed = _state.hoverId && !_state.neighbors.has(n.uuid);
  const hovered = n.uuid === _state.hoverId;
  const color = _palette
    ? _palette[nodeColorKey(n, _state.journalUuid)] || _palette.regular
    : '#9ca3af';
  ctx.save();
  if (dimmed) ctx.globalAlpha = 0.15;
  // Filled circle + thin background-coloured ring (crisp cut-out over links).
  ctx.beginPath();
  ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.lineWidth = Math.max(0.5, r * 0.14);
  ctx.strokeStyle = (_palette && _palette.ring) || 'rgba(255,255,255,0.85)';
  ctx.stroke();
  // Hover accent: a light coloured halo ring (a single cheap stroke, no canvas
  // shadow - the shadow was the per-frame perf cost).
  if (hovered) {
    ctx.beginPath();
    ctx.arc(n.x, n.y, r + Math.max(1, r * 0.2), 0, 2 * Math.PI);
    ctx.lineWidth = Math.max(0.75, r * 0.16);
    ctx.globalAlpha = 0.45;
    ctx.strokeStyle = color;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  if (!dimmed && n.name) {
    ctx.font = '4px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillStyle = (_palette && _palette.text) || 'rgba(150,150,150,0.9)';
    ctx.fillText(n.name, n.x, n.y + r + 1.5);
  }
  ctx.restore();
}

// A link is highlighted when not hovering, or when BOTH its endpoints are in the
// hovered node's neighbourhood (the hovered node + its direct neighbours). AND
// (not OR) means a link from a neighbour to an unrelated node is dimmed, staying
// consistent with that unrelated node being dimmed too.
function linkActive(hoverId, neighbors, sId, tId) {
  return !hoverId || (neighbors.has(sId) && neighbors.has(tId));
}

function _applyColors() {
  if (!_fg) return;
  _palette = themePalette();
  // Node fill/dim is computed per-frame in _drawNode (replace mode); here we
  // only drive link colour, which also forces a redraw so a hover restyles the
  // scene even after the engine has cooled.
  _fg.linkColor((l) => {
    const sId = (l.source && l.source.uuid) || l.source;
    const tId = (l.target && l.target.uuid) || l.target;
    return linkActive(_state.hoverId, _state.neighbors, sId, tId)
      ? 'rgba(150,150,150,0.35)'
      : 'rgba(150,150,150,0.08)';
  });
}

// Fetch the (scope + filters)-filtered graph from the API and render it.
async function _load() {
  if (!_fg) return;
  // Last-request-wins: each load claims a fresh generation up front, so if a
  // newer load (filter/search/scope change) or a view switch starts before this
  // fetch resolves, the gen checks below discard this now-stale response.
  const gen = ++_openGen;
  const params = ['type=markdown'];
  if (_state.scope === 'all') params.push('scope=all');
  // "mine" scope = the Notes folder subtree (under=); the "journal" kind narrows
  // it to the Journal subtree instead. Other kinds map to generic favorite /
  // exclude filters - journal stays a notes concept, resolved here client-side.
  let under = _state.scope === 'mine' ? _state.notesRoot : null;
  if (_state.kind === 'favorite') {
    params.push('favorites=1');
  } else if (_state.kind === 'journal') {
    under = _state.journalUuid || under;
  } else if (_state.kind === 'regular') {
    params.push('favorites=0');
    if (_state.journalUuid) {
      params.push('exclude_descendants_of=' + encodeURIComponent(_state.journalUuid));
    }
  }
  if (under) params.push('under=' + encodeURIComponent(under));
  if (_state.search) params.push('search=' + encodeURIComponent(_state.search));
  const url = '/api/v1/files/graph?' + params.join('&');
  try {
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (gen !== _openGen || !resp.ok || !_fg) return;
    const data = await resp.json();
    if (gen !== _openGen || !_fg) return;
    // Size nodes by degree: count incident edges so hubs render larger.
    const deg = {};
    (data.edges || []).forEach((e) => {
      deg[e.source] = (deg[e.source] || 0) + 1;
      deg[e.target] = (deg[e.target] || 0) + 1;
    });
    (data.nodes || []).forEach((n) => {
      n.val = 1 + (deg[n.uuid] || 0);
    });
    _fitPending = true; // frame the new layout once it settles (onEngineStop)
    _fg.graphData({ nodes: data.nodes, links: data.edges });
    _applyColors();
  } catch (e) {
    // Transient network/parse failure. setScope/setKind/setSearch call _load()
    // without awaiting, so a thrown error would be an unhandled rejection; keep
    // the last good render instead of crashing.
  }
}

async function open(container, opts) {
  const gen = ++_openGen;
  _state.scope = (opts && opts.scope) || 'mine';
  _state.kind = 'all';
  _state.search = '';
  _state.journalUuid = (opts && opts.journalUuid) || null;
  _state.notesRoot = (opts && opts.notesRoot) || null;
  _state.onNodeClick = (opts && opts.onNodeClick) || null;
  _container = container;

  const mod = await import('https://esm.sh/force-graph@1');
  if (gen !== _openGen) return; // view was left / re-opened during the import

  // force-graph >= 1.44 is a class (new ForceGraph(el)); older releases use the
  // curried factory ForceGraph()(el). Support both so a CDN version bump can't
  // silently break rendering.
  const FG = mod.default;
  const construct = (el) =>
    FG.prototype && FG.prototype.graphData ? new FG(el) : FG()(el);

  if (!_fg) {
    _fg = construct(container)
      .nodeId('uuid')
      // nodeLabel renders as HTML in the hover tooltip and names are user-
      // controlled (other users' files in scope=all), so escape via the shared
      // global escapeHtml (common/static/ui/js/html.js) to avoid stored XSS.
      .nodeLabel((n) => escapeHtml(n.name))
      .nodeRelSize(NODE_REL)
      // Custom node drawing (replace mode): degree-sized circle + thin ring +
      // label, with focus dimming and a hover halo. Replace removes the built-in
      // hit-area, so nodePointerAreaPaint below restores hover/click.
      .nodeCanvasObjectMode(() => 'replace')
      .nodeCanvasObject((n, ctx) => _drawNode(n, ctx))
      .nodePointerAreaPaint((n, color, ctx) => {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(n.x, n.y, _nodeRadius(n), 0, 2 * Math.PI);
        ctx.fill();
      })
      // Faint dot grid behind everything, panning/zooming with the scene.
      .onRenderFramePre((ctx, globalScale) => _drawGrid(ctx, globalScale))
      .onNodeClick((n) => {
        if (_state.onNodeClick) _state.onNodeClick(n.uuid);
      })
      .onNodeHover((n) => {
        _state.hoverId = n ? n.uuid : null;
        _state.neighbors = new Set(n ? [n.uuid] : []);
        if (n && _fg) {
          _fg.graphData().links.forEach((l) => {
            const sId = (l.source && l.source.uuid) || l.source;
            const tId = (l.target && l.target.uuid) || l.target;
            if (sId === n.uuid) _state.neighbors.add(tId);
            if (tId === n.uuid) _state.neighbors.add(sId);
          });
        }
        _applyColors();
        container.style.cursor = n ? 'pointer' : '';
      })
      // Frame the graph once the force layout settles after a (re)load.
      .onEngineStop(() => {
        if (_fitPending && _fg) {
          _fitPending = false;
          _frameGraph(400);
        }
      });
    _resizeObserver = new ResizeObserver(() => {
      if (_fg && _container) _fg.width(_container.clientWidth).height(_container.clientHeight);
    });
    _resizeObserver.observe(container);
  }
  _fg.width(container.clientWidth).height(container.clientHeight);
  await _load();
}

function setScope(scope) {
  _state.scope = scope;
  _load();
}

function setSearch(q) {
  _state.search = q || '';
  _load();
}

function setKind(kind) {
  _state.kind = kind || 'all';
  _load();
}

// Reset the camera: re-frame all nodes (undo manual pan/zoom).
function fitView() {
  _frameGraph(400);
}

function destroy() {
  _openGen++; // invalidate any in-flight open() / _load()
  _fitPending = false;
  if (_resizeObserver) {
    _resizeObserver.disconnect();
    _resizeObserver = null;
  }
  if (_fg) {
    _fg.graphData({ nodes: [], links: [] });
    if (_fg._destructor) _fg._destructor();
    _fg = null;
  }
  if (_container) {
    _container.replaceChildren();
    _container = null;
  }
  _state = {
    scope: 'mine',
    kind: 'all',
    search: '',
    journalUuid: null,
    notesRoot: null,
    onNodeClick: null,
    hoverId: null,
    neighbors: new Set(),
  };
}

window.notesGraph = { nodeColorKey, fitZoom, linkActive, open, setScope, setKind, setSearch, fitView, destroy };
