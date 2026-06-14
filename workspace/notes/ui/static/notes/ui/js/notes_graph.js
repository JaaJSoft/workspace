// Notes graph view: force-graph integration + pure helpers.
// Classic script (no top-level import) so it loads via <script src> and the
// node:vm test loader. force-graph is imported dynamically at runtime inside
// open() (network), so loading this file is side-effect-free.

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
  const baseContent = read('text-base-content', '#9ca3af');
  const pal = {
    favorite: read('text-warning', '#f59e0b'),
    journal: read('text-success', '#22c55e'),
    regular: baseContent,
    text: baseContent, // node labels drawn on the canvas
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

function _applyColors() {
  if (!_fg) return;
  _palette = themePalette();
  _fg.nodeColor((n) => _palette[nodeColorKey(n, _state.journalUuid)] || _palette.regular);
  _fg.linkColor((l) => {
    const sId = (l.source && l.source.uuid) || l.source;
    const tId = (l.target && l.target.uuid) || l.target;
    const active = !_state.hoverId || _state.neighbors.has(sId) || _state.neighbors.has(tId);
    return active ? 'rgba(150,150,150,0.35)' : 'rgba(150,150,150,0.08)';
  });
}

// Fetch the (scope + search)-filtered graph from the API and render it. Both
// scope and search are applied server-side, so non-matching notes are simply
// not returned (rather than dimmed client-side).
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
  const resp = await fetch(url, { credentials: 'same-origin' });
  if (gen !== _openGen || !resp.ok || !_fg) return;
  const data = await resp.json();
  if (gen !== _openGen || !_fg) return;
  _fg.graphData({ nodes: data.nodes, links: data.edges });
  _applyColors();
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
      .nodeRelSize(5)
      // Draw the note name centred just below the node. Mode 'after' keeps the
      // default coloured circle (and its hover/click hit-area) and paints the
      // label on top. Font size is in graph units (the ctx is already
      // zoom-scaled), so labels grow when zoomed in and fade when zoomed out.
      .nodeCanvasObjectMode(() => 'after')
      .nodeCanvasObject((n, ctx) => {
        if (!n.name) return;
        ctx.font = '4px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillStyle = (_palette && _palette.text) || 'rgba(150,150,150,0.9)';
        ctx.fillText(n.name, n.x, n.y + 7);
      })
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

function destroy() {
  _openGen++; // invalidate any in-flight open() / _load()
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

window.notesGraph = { nodeColorKey, open, setScope, setKind, setSearch, destroy };
