// File embeds for the Milkdown Crepe editor: a block node that shows a file
// of the Files module inside a note - an image, a PDF, a player, or a card
// linking to it.
//
// The node stores the file's uuid and the name the author saw, never the
// file: what it draws is asked of the server for the person reading the note
// (POST /api/v1/files/embeds), so a reader who may not open the file gets a
// placeholder, and an edited or replaced file shows up as it is now.
//
// Like wikilink_autocomplete.js, this module never imports the vendored
// bundle: the caller hands it the Milkdown composables and ProseMirror's
// Plugin class from its one import, so they share its internals.
import { EMBED_NODE, embedSrc, embedUuidFromSrc, rewriteEmbeds } from './file_embed_markdown.js';

const RESOLVE_URL = '/api/v1/files/embeds';
// The endpoint's cap (services/embeds.py MAX_EMBEDS).
const RESOLVE_BATCH = 200;

// Asks the server about the files of every embed drawn in the same tick in
// one request, and remembers each answer for the life of the editor. Without
// a session (a public share page) nothing is asked: every embed is
// unavailable, which is what the endpoint would answer anyway.
export function createEmbedResolver({ enabled = true, csrfToken = () => '' } = {}) {
  const answers = new Map();
  let pending = new Map();
  let flushScheduled = false;

  async function request(uuids) {
    const resp = await fetch(RESOLVE_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ uuids }),
    });
    if (!resp.ok) throw new Error('embed resolution failed: ' + resp.status);
    return resp.json();
  }

  async function flush() {
    flushScheduled = false;
    const batch = pending;
    pending = new Map();
    const uuids = Array.from(batch.keys());
    for (let i = 0; i < uuids.length; i += RESOLVE_BATCH) {
      const chunk = uuids.slice(i, i + RESOLVE_BATCH);
      let data = null;
      try {
        data = await request(chunk);
      } catch (e) {
        data = null;
      }
      for (const uuid of chunk) {
        const answer = data && data[uuid];
        // A failed request is not cached: the next draw of the embed asks again.
        if (!answer) answers.delete(uuid);
        batch.get(uuid)(answer || { status: 'error' });
      }
    }
  }

  function get(uuid) {
    if (!enabled) return Promise.resolve({ status: 'unavailable' });
    if (answers.has(uuid)) return answers.get(uuid);
    const promise = new Promise((resolve) => pending.set(uuid, resolve));
    answers.set(uuid, promise);
    if (!flushScheduled) {
      flushScheduled = true;
      setTimeout(flush, 0);
    }
    return promise;
  }

  return { get };
}

// ── Drawing ─────────────────────────────────────────────────────────────

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function icon(name, className) {
  const i = el('i', className || 'file-embed-icon');
  i.setAttribute('data-lucide', name);
  return i;
}

function link(href, label, iconName, { download = false } = {}) {
  const a = el('a', 'file-embed-action');
  a.href = href;
  a.title = label;
  a.setAttribute('aria-label', label);
  if (download) {
    a.setAttribute('download', '');
  } else {
    a.target = '_blank';
    a.rel = 'noopener';
  }
  a.appendChild(icon(iconName));
  return a;
}

function noteTitle(name) {
  return (name || '').replace(/\.md$/i, '');
}

function sizeLabel(size) {
  if (size == null || typeof window.formatFileSize !== 'function') return '';
  return window.formatFileSize(size);
}

// The line under an inline embed, or the whole of a card: what the file is,
// and the ways to open it.
function caption(info, { iconName, title, meta, actions = true } = {}) {
  const row = el('div', 'file-embed-caption');
  row.appendChild(icon(iconName || info.icon || 'file'));
  const text = el('div', 'file-embed-text');
  text.appendChild(el('span', 'file-embed-name', title || info.name));
  if (meta) text.appendChild(el('span', 'file-embed-meta', meta));
  row.appendChild(text);
  if (actions && info.content_url) {
    row.appendChild(link(info.content_url, gettext('Open in a new tab'), 'external-link'));
  }
  if (actions && info.download_url) {
    row.appendChild(link(info.download_url, gettext('Download'), 'download', { download: true }));
  }
  return row;
}

function placeholder(iconName, title, message, variant) {
  const card = el('div', 'file-embed-card file-embed-placeholder' + (variant ? ' file-embed-' + variant : ''));
  card.appendChild(icon(iconName));
  const text = el('div', 'file-embed-text');
  if (title) text.appendChild(el('span', 'file-embed-name', title));
  text.appendChild(el('span', 'file-embed-meta', message));
  card.appendChild(text);
  return card;
}

function fileCard(info) {
  const card = el('div', 'file-embed-card');
  card.appendChild(caption(info, { meta: sizeLabel(info.size) }));
  return card;
}

function noteCard(info, uuid) {
  const card = el('div', 'file-embed-card');
  const row = el('a', 'file-embed-caption file-embed-note-link');
  row.href = '/notes?file=' + uuid;
  row.appendChild(icon('notebook-pen'));
  const text = el('div', 'file-embed-text');
  text.appendChild(el('span', 'file-embed-name', noteTitle(info.name)));
  text.appendChild(el('span', 'file-embed-meta', gettext('Note')));
  row.appendChild(text);
  card.appendChild(row);
  return card;
}

// A blurred preview stays blurred by the page's own rules (input.css), which
// read the level off data-sensitive.
function media(info, element) {
  const frame = el('div', 'file-embed-media');
  frame.setAttribute('data-sensitive', String(info.sensitive || 0));
  frame.appendChild(element);
  return frame;
}

function imageEmbed(info, onBroken) {
  const box = el('figure', 'file-embed-figure');
  const img = el('img', 'file-embed-image');
  img.alt = info.name;
  img.loading = 'lazy';
  img.decoding = 'async';
  // A format the browser cannot draw (HEIC, RAW, TIFF) falls back to the
  // thumbnail the server made of it, then to a card.
  let triedThumbnail = false;
  img.addEventListener('error', () => {
    if (!triedThumbnail && info.thumbnail_url) {
      triedThumbnail = true;
      img.src = info.thumbnail_url;
      return;
    }
    onBroken();
  });
  img.src = info.content_url;
  box.appendChild(media(info, img));
  box.appendChild(caption(info));
  return box;
}

function pdfEmbed(info) {
  const box = el('figure', 'file-embed-figure');
  const frame = el('iframe', 'file-embed-pdf');
  frame.src = info.content_url;
  frame.title = info.name;
  frame.loading = 'lazy';
  box.appendChild(frame);
  box.appendChild(caption(info));
  return box;
}

function playerEmbed(info, tag) {
  const box = el('figure', 'file-embed-figure');
  const player = el(tag, 'file-embed-' + tag);
  player.controls = true;
  player.preload = 'metadata';
  if (tag === 'video' && info.thumbnail_url) player.poster = info.thumbnail_url;
  player.src = info.content_url;
  box.appendChild(tag === 'video' ? media(info, player) : player);
  box.appendChild(caption(info));
  return box;
}

// The element to show for one embed. *info* is the server's answer, or
// {status: 'loading'} while it is being asked; *name* is what the note itself
// says, the only name shown for a file the reader cannot reach.
export function renderEmbed(info, { uuid, name, onBroken, onRetry } = {}) {
  const status = info && info.status;
  if (status === 'loading') {
    const card = el('div', 'file-embed-card file-embed-placeholder file-embed-loading');
    card.appendChild(el('span', 'loading loading-spinner loading-sm'));
    const text = el('div', 'file-embed-text');
    text.appendChild(el('span', 'file-embed-name', name || gettext('Loading…')));
    card.appendChild(text);
    return card;
  }
  if (status === 'error') {
    const card = placeholder('alert-circle', name, gettext("Couldn't load this file."), 'error');
    if (onRetry) {
      const retry = el('button', 'file-embed-action', gettext('Retry'));
      retry.type = 'button';
      retry.addEventListener('click', onRetry);
      card.appendChild(retry);
    }
    return card;
  }
  if (status === 'trashed') {
    return placeholder('trash-2', info.name, gettext('This file is in the trash.'), 'trashed');
  }
  if (status === 'quarantined') {
    return placeholder('shield-alert', info.name, gettext('The malware scanner blocked this file.'), 'blocked');
  }
  if (status !== 'ok') {
    return placeholder(
      'lock',
      name,
      gettext("This file was deleted, or you don't have access to it."),
      'unavailable'
    );
  }
  switch (info.kind) {
    case 'image':
      return imageEmbed(info, onBroken || (() => {}));
    case 'pdf':
      return pdfEmbed(info);
    case 'audio':
    case 'video':
      return playerEmbed(info, info.kind);
    case 'note':
      return noteCard(info, uuid);
    default:
      return fileCard(info);
  }
}

function drawIcons(root) {
  if (typeof window.renderLucideIcons === 'function') window.renderLucideIcons(root);
}

// ── The node view ───────────────────────────────────────────────────────

const INTERACTIVE = 'a, button, audio, video, iframe';

function embedView(resolver) {
  return (initialNode) => {
    let node = initialNode;
    let destroyed = false;
    let generation = 0;
    const dom = el('div', 'file-embed');
    dom.setAttribute('data-file-embed', node.attrs.uuid);

    function show(info) {
      const props = {
        uuid: node.attrs.uuid,
        name: node.attrs.name,
        onBroken: () => show({ ...info, kind: 'file' }),
        onRetry: () => draw(),
      };
      dom.setAttribute('data-embed-status', info.status);
      if (info.kind) dom.setAttribute('data-embed-kind', info.kind);
      else dom.removeAttribute('data-embed-kind');
      dom.replaceChildren(renderEmbed(info, props));
      drawIcons(dom);
    }

    function draw() {
      const gen = ++generation;
      show({ status: 'loading' });
      resolver.get(node.attrs.uuid).then((info) => {
        if (destroyed || gen !== generation) return;
        show(info);
      });
    }

    draw();

    return {
      dom,
      update(updated) {
        if (updated.type !== node.type) return false;
        const changed = updated.attrs.uuid !== node.attrs.uuid;
        node = updated;
        if (changed) {
          dom.setAttribute('data-file-embed', node.attrs.uuid);
          draw();
        }
        return true;
      },
      // The players, links and the PDF frame keep their clicks; the rest of
      // the block selects and drags the node.
      stopEvent(event) {
        const target = event.target;
        return !!(target && target.closest && target.closest(INTERACTIVE));
      },
      // The view redraws itself when the answer lands, and Lucide swaps its
      // icons in: none of it is a change of the document.
      ignoreMutation() {
        return true;
      },
      destroy() {
        destroyed = true;
        dom.querySelectorAll('audio, video').forEach((player) => player.pause());
      },
    };
  };
}

// ── Insertion ───────────────────────────────────────────────────────────

// Where blocks dropped or typed at *$pos* go: an empty paragraph is replaced,
// one the position starts or ends gets them before or after, and one it cuts
// in two is split around them. A container that takes no block there (a
// table cell) sends them after the top-level block instead.
function insertionPoint($pos, type) {
  const parent = $pos.parent;
  if (!parent.isTextblock) {
    const index = $pos.index();
    return parent.canReplaceWith(index, index, type) ? { pos: $pos.pos } : null;
  }
  const depth = $pos.depth - 1;
  const container = $pos.node(depth);
  const index = $pos.index(depth);
  const size = parent.content.size;
  let point;
  if (size === 0) point = { pos: $pos.before(), end: $pos.after(), from: index, to: index + 1 };
  else if ($pos.parentOffset === 0) point = { pos: $pos.before(), from: index, to: index };
  else if ($pos.parentOffset === size) point = { pos: $pos.after(), from: index + 1, to: index + 1 };
  else point = { pos: $pos.pos + 1, split: true, from: index + 1, to: index + 1 };
  if (container.canReplaceWith(point.from, point.to, type)) return point;
  return { pos: $pos.after(1) };
}

// Insert an embed for each of *refs* ({uuid, name}), in order, at *from* -
// after deleting up to *to*, the text an autocomplete replaces. Returns false,
// dispatching nothing, when no embed can go there.
export function insertEmbeds(view, refs, from, to = from) {
  const type = view.state.schema.nodes[EMBED_NODE];
  if (!type || !refs.length) return false;
  const nodes = refs.map((ref) => type.create({ uuid: ref.uuid, name: ref.name || '' }));
  const tr = view.state.tr;
  if (to > from) tr.delete(from, to);
  const point = insertionPoint(tr.doc.resolve(from), type);
  if (!point) return false;
  if (point.split) tr.split(from);
  if (point.end) tr.replaceWith(point.pos, point.end, nodes);
  else tr.insert(point.pos, nodes);
  view.dispatch(tr.scrollIntoView());
  return true;
}

// ── The plugin ──────────────────────────────────────────────────────────

// Returns an applier: call it with the Crepe editor to register the node.
//   createFileEmbed({ $nodeSchema, $remark, $view, $prose, Plugin, resolver, dropped })(crepe.editor)
// *dropped* is window.fileRefsDrag (file_refs_drag.js) where rows can be
// dragged into the note; without it, drops are left to the editor.
export function createFileEmbed({ $nodeSchema, $remark, $view, $prose, Plugin, resolver, dropped } = {}) {
  const remark = $remark('remark-file-embed', () => () => (tree) => {
    rewriteEmbeds(tree);
  });

  const schema = $nodeSchema(EMBED_NODE, () => ({
    group: 'block',
    atom: true,
    selectable: true,
    draggable: true,
    isolating: true,
    marks: '',
    attrs: {
      uuid: { default: '' },
      name: { default: '' },
      title: { default: '' },
    },
    parseDOM: [
      {
        tag: 'div[data-file-embed]',
        getAttrs: (dom) => {
          const uuid = (dom.getAttribute('data-file-embed') || '').toLowerCase();
          // Pasted markup is not trusted to name a file: it has to be a uuid.
          if (!embedUuidFromSrc(embedSrc(uuid))) return false;
          return { uuid, name: dom.getAttribute('data-name') || '', title: '' };
        },
      },
    ],
    toDOM: (node) => ['div', { 'data-file-embed': node.attrs.uuid, 'data-name': node.attrs.name }],
    parseMarkdown: {
      match: (mdNode) => mdNode.type === EMBED_NODE,
      runner: (state, mdNode, type) => {
        state.addNode(type, { uuid: mdNode.uuid, name: mdNode.name, title: mdNode.title });
      },
    },
    toMarkdown: {
      match: (node) => node.type.name === EMBED_NODE,
      runner: (state, node) => {
        state.openNode('paragraph');
        state.addNode('image', undefined, undefined, {
          url: embedSrc(node.attrs.uuid),
          alt: node.attrs.name,
          title: node.attrs.title || undefined,
        });
        state.closeNode();
      },
    },
  }));

  const view = $view(schema.node, () => embedView(resolver));

  // A DOM handler rather than handleDrop: Crepe's drop indicator answers
  // handleDrop for every drop, empty ones included, and ProseMirror asks it
  // first.
  const drop = $prose(() => new Plugin({
    props: {
      handleDOMEvents: {
        dragover(editorView, event) {
          if (!dropped || !editorView.editable || !dropped.has(event.dataTransfer)) return false;
          // A member of the 'copy' the row declares, or the browser refuses
          // the drop without telling anyone.
          event.preventDefault();
          event.dataTransfer.dropEffect = 'copy';
          return false;
        },
        drop(editorView, event) {
          if (!dropped || !editorView.editable) return false;
          const refs = dropped.read(event.dataTransfer);
          if (!refs.length) return false;
          event.preventDefault();
          const at = editorView.posAtCoords({ left: event.clientX, top: event.clientY });
          if (at) insertEmbeds(editorView, refs, at.pos);
          editorView.focus();
          return true;
        },
      },
    },
  }));

  return (editor) => editor.use(remark).use(schema).use(view).use(drop);
}
