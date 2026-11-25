/**
 * Note (file) card popover - hover a graph node or an editor note-link to
 * preview a file's title, tags and first content line.
 *
 * One shared popover on the card_popover.js engine: the anchor may be a DOM
 * element or a synthetic { getBoundingClientRect() }, since the graph is
 * canvas-drawn and its nodes are not DOM elements.
 */

const _noteCard = window.createHoverCard({ className: 'note-card-popover', minSpace: 240, shared: true });

/**
 * Extract the target file UUID from a note-link href (/notes?file=<uuid>).
 * Pure (no DOM). Returns the canonical uuid string, or null when absent.
 */
function noteCardFileUuidFromHref(href) {
  if (!href) return null;
  const m = /[?&]file=([^&#]+)/.exec(href);
  return m && window.isValidUuid(m[1]) ? m[1] : null;
}
window.noteCardFileUuidFromHref = noteCardFileUuidFromHref;

/** Show the card of file `uuid` next to `anchor`, after the hover delay. */
window._noteCardShow = function(anchor, uuid) {
  _noteCard.show(anchor, '/files/' + uuid + '/card');
};

window._noteCardScheduleHide = function() {
  _noteCard.scheduleHide();
};
