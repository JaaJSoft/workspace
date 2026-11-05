// File embeds for the Milkdown Crepe editor: a block node that shows a file
// of the Files module inside a note.
//
// The node stores the file's uuid and the name the author saw, never the
// file. What it shows is a <file-preview> (common/static/ui/js/file_preview.js)
// the server draws for the person reading the note: a reader who may not
// open the file gets a placeholder, an edited or replaced file shows up as it
// is now, and a click opens it in the page's file viewer.
//
// Like wikilink_autocomplete.js, this module never imports the vendored
// bundle: the caller hands it the Milkdown composables and ProseMirror's
// Plugin class from its one import, so they share its internals.
import { EMBED_NODE, embedSrc, embedUuidFromSrc, rewriteEmbeds } from './file_embed_markdown.js';

// ── The node view ───────────────────────────────────────────────────────

// The preview keeps the clicks on its own controls (an audio player); the
// rest of the block selects and drags the node, and opens the viewer.
const INTERACTIVE = 'a, button, audio, input';

function embedView(previewSrc) {
  return (initialNode) => {
    let node = initialNode;
    const dom = document.createElement('div');
    dom.className = 'file-embed';

    function draw() {
      dom.setAttribute('data-file-embed', node.attrs.uuid);
      const preview = document.createElement('file-preview');
      preview.setAttribute('uuid', node.attrs.uuid);
      preview.setAttribute('name', node.attrs.name);
      preview.setAttribute('files', '');
      if (previewSrc) preview.setAttribute('src', previewSrc);
      dom.replaceChildren(preview);
    }

    draw();

    return {
      dom,
      update(updated) {
        if (updated.type !== node.type) return false;
        const changed = updated.attrs.uuid !== node.attrs.uuid;
        node = updated;
        if (changed) draw();
        return true;
      },
      stopEvent(event) {
        const target = event.target;
        return !!(target && target.closest && target.closest(INTERACTIVE));
      },
      // The preview loads and draws itself: none of it is a change of the
      // document.
      ignoreMutation() {
        return true;
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
//   createFileEmbed({ $nodeSchema, $remark, $view, $prose, Plugin, previewSrc, dropped })(crepe.editor)
// previewSrc is Files' preview batch endpoint; without it (a page without a
// session) every embed shows its placeholder. dropped is window.fileRefsDrag
// (file_refs_drag.js) where rows can be dragged into the note; without it,
// drops are left to the editor.
export function createFileEmbed({ $nodeSchema, $remark, $view, $prose, Plugin, previewSrc, dropped } = {}) {
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

  const view = $view(schema.node, () => embedView(previewSrc));

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
