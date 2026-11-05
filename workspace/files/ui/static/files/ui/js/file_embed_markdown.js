// The markdown of a file embed, with no editor dependency, so the Node unit
// tests import it directly.
//
// A note embeds a file as an image whose source is the file's content
// endpoint, on a paragraph of its own:
//
//   ![report.pdf](/api/v1/files/<uuid>/content)
//
// Any markdown renderer shows an embedded image as an image; the editor draws
// every type (file_embed.js). files/services/links.py reads the same
// destination into the note graph.

export const EMBED_NODE = 'file-embed';

const EMBED_SRC_RE = /^\/api\/v1\/files\/([^/?#]+)\/content(?:[?#].*)?$/;

// The file an embed source points at, as a lowercase UUID; null for any other
// URL. The id is checked by isValidUuid (common/static/ui/js/uuid.js), which
// every page that hosts the editor loads.
export function embedUuidFromSrc(src) {
  const m = EMBED_SRC_RE.exec(String(src || '').trim());
  return m && window.isValidUuid(m[1]) ? m[1].toLowerCase() : null;
}

export function embedSrc(uuid) {
  return '/api/v1/files/' + uuid + '/content';
}

function embedNode(image) {
  return {
    type: EMBED_NODE,
    uuid: embedUuidFromSrc(image.url),
    name: image.alt || '',
    title: image.title || '',
  };
}

function isEmbedImage(node) {
  return (node.type === 'image' || node.type === 'image-block') && !!embedUuidFromSrc(node.url);
}

function isBlank(node) {
  return node.type === 'break' || (node.type === 'text' && !node.value.trim());
}

// The embeds a paragraph stands for, or null when it holds anything else: an
// embed in the middle of a sentence stays an inline image.
function paragraphEmbeds(paragraph) {
  const children = paragraph.children || [];
  if (!children.some(isEmbedImage)) return null;
  if (!children.every((child) => isEmbedImage(child) || isBlank(child))) return null;
  return children.filter(isEmbedImage).map(embedNode);
}

// Replace, in a parsed markdown tree (mdast), every paragraph made of embeds
// alone by one file-embed node per embed. Crepe's image block plugin may
// already have turned a lone image into an image-block node; that one is
// taken over too. Mutates and returns the tree.
export function rewriteEmbeds(tree) {
  const children = tree && tree.children;
  if (!Array.isArray(children)) return tree;
  tree.children = children.flatMap((child) => {
    if (child.type === 'image-block' && isEmbedImage(child)) return [embedNode(child)];
    if (child.type === 'paragraph') {
      const embeds = paragraphEmbeds(child);
      if (embeds) return embeds;
    }
    rewriteEmbeds(child);
    return [child];
  });
  return tree;
}
