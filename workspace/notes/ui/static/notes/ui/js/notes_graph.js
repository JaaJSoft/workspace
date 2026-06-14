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

// Escape a string for safe use as HTML. force-graph's nodeLabel renders its
// return value as HTML in the hover tooltip, and node names are user-controlled
// (in scope=all they are authored by OTHER users), so they MUST be escaped to
// avoid stored XSS.
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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
  const pal = {
    favorite: read('text-warning', '#f59e0b'),
    journal: read('text-success', '#22c55e'),
    regular: read('text-base-content', '#9ca3af'),
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
let _openGen = 0;
let _state = { journalUuid: null, search: '', onNodeClick: null, hoverId: null, neighbors: new Set() };

function _matches(node, q) {
  if (!q) return true;
  return (node.name || '').toLowerCase().includes(q.toLowerCase());
}

function _withAlpha(color, a) {
  // color is an rgb(...) string from getComputedStyle; degrade to rgba.
  const m = color.match(/(\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return color;
  return `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${a})`;
}

function _applyColors() {
  if (!_fg) return;
  const pal = themePalette();
  // Re-assigning the accessors triggers a repaint even after the sim settles.
  _fg.nodeColor((n) => {
    const base = pal[nodeColorKey(n, _state.journalUuid)] || pal.regular;
    const dim = _state.search && !_matches(n, _state.search);
    return dim ? _withAlpha(base, 0.15) : base;
  });
  _fg.linkColor((l) => {
    const sId = (l.source && l.source.id) || l.source;
    const tId = (l.target && l.target.id) || l.target;
    const active = !_state.hoverId || _state.neighbors.has(sId) || _state.neighbors.has(tId);
    return active ? 'rgba(150,150,150,0.35)' : 'rgba(150,150,150,0.08)';
  });
}

async function open(container, opts) {
  const gen = ++_openGen;
  _state.journalUuid = (opts && opts.journalUuid) || null;
  _state.onNodeClick = (opts && opts.onNodeClick) || null;
  _container = container;
  const scope = (opts && opts.scope) || 'mine';

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
      .nodeId('id')
      .nodeLabel((n) => escapeHtml(n.name))
      .nodeRelSize(5)
      .onNodeClick((n) => {
        if (_state.onNodeClick) _state.onNodeClick(n.id);
      })
      .onNodeHover((n) => {
        _state.hoverId = n ? n.id : null;
        _state.neighbors = new Set(n ? [n.id] : []);
        if (n && _fg) {
          _fg.graphData().links.forEach((l) => {
            const sId = (l.source && l.source.id) || l.source;
            const tId = (l.target && l.target.id) || l.target;
            if (sId === n.id) _state.neighbors.add(tId);
            if (tId === n.id) _state.neighbors.add(sId);
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
  await setScope(scope, gen);
}

async function setScope(scope, gen) {
  if (gen === undefined) gen = _openGen;
  const url = '/api/v1/files/graph?type=markdown&scope=' + encodeURIComponent(scope);
  const resp = await fetch(url, { credentials: 'same-origin' });
  if (gen !== _openGen || !resp.ok || !_fg) return;
  const data = await resp.json();
  if (gen !== _openGen || !_fg) return;
  _fg.graphData({ nodes: data.nodes, links: data.edges });
  _applyColors();
}

function setSearch(q) {
  _state.search = q || '';
  _applyColors();
}

function destroy() {
  _openGen++; // invalidate any in-flight open() / setScope()
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
  _state = { journalUuid: null, search: '', onNodeClick: null, hoverId: null, neighbors: new Set() };
}

window.NotesGraph = { nodeColorKey, escapeHtml, open, setScope, setSearch, destroy };
