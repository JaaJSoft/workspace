// Moving files and folders by drag & drop.
//
// The listing hands the dragged items to start() at dragstart; from then
// on every element carrying a data-drop-folder attribute is a target: the
// folders of the listing, the breadcrumb ancestors, the sidebar entries.
// One set of listeners on the document serves them all, so a swap of
// #folder-browser never has to re-bind anything, and the drop reaches the
// browser component as a window event because the targets sit in three
// different Alpine scopes.
//
// An empty data-drop-folder is the user's root. data-drop-folder-name is
// optional and only feeds the event detail.
window.fileDragMove = (function () {
  const MIME = 'application/x-file-nodes';
  const TARGET_CLASS = 'file-drop-target';
  const SOURCE_CLASS = 'file-drag-source';
  const EVENT = 'file-move-request';

  // The drag in progress. getData() is only readable at drop time, so the
  // dragover decisions read the payload from here instead.
  let current = null;
  let sourceElements = [];
  let highlighted = null;

  // paste_into availability per folder: a boolean once answered, a
  // Promise while asked. The listing seeds it with the actions it already
  // fetched; the breadcrumbs and the sidebar are asked on first hover.
  let writable = {};

  function targetOf(node) {
    return node instanceof Element ? node.closest('[data-drop-folder]') : null;
  }

  function folderIdOf(target) {
    return target.dataset.dropFolder || null;
  }

  // A folder cannot receive itself, and the folder the items already sit
  // in is a no-op. sourceFolder is undefined when the listing is not a
  // folder (favorites, recent, a tag): every item then has its own parent
  // and the server is the one to say.
  function canDrop(drag, folderId) {
    if (!drag || !drag.items.length) return false;
    if (drag.items.some((item) => item.uuid === folderId)) return false;
    if (drag.sourceFolder !== undefined && drag.sourceFolder === folderId) return false;
    return true;
  }

  function rememberActions(actionsMap) {
    for (const [uuid, actions] of Object.entries(actionsMap || {})) {
      writable[uuid] = (actions || []).some((a) => a.id === 'paste_into');
    }
  }

  function forgetActions() {
    writable = {};
  }

  function isWritable(folderId) {
    if (folderId === null) return true;
    const known = writable[folderId];
    if (typeof known === 'boolean') return known;
    if (known === undefined && window.fileActions) {
      writable[folderId] = window.fileActions
        .fetchActions([folderId])
        .then((map) => {
          rememberActions({ [folderId]: (map && map[folderId]) || [] });
        })
        .catch(() => {
          delete writable[folderId];
        });
    }
    return false;
  }

  function highlight(target) {
    if (highlighted === target) return;
    if (highlighted) highlighted.classList.remove(TARGET_CLASS);
    highlighted = target;
    if (highlighted) highlighted.classList.add(TARGET_CLASS);
  }

  // The browser's own ghost is the grabbed row alone; a selection of
  // several items drags as a count instead.
  function setDragImage(dataTransfer, count) {
    if (!dataTransfer.setDragImage || !document.body) return;
    const badge = document.createElement('div');
    badge.className = 'badge badge-primary';
    badge.style.cssText = 'position:fixed;top:-1000px;left:0;pointer-events:none;';
    badge.textContent = `${count} items`;
    document.body.appendChild(badge);
    dataTransfer.setDragImage(badge, 12, 12);
    setTimeout(() => badge.remove(), 0);
  }

  // items: [{uuid, name, nodeType}]. Sets the payload only - the caller
  // owns effectAllowed, since a folder drag also carries the pin gesture.
  function start(dataTransfer, items, { sourceFolder, sources = [] } = {}) {
    current = { items, sourceFolder };
    dataTransfer.setData(MIME, JSON.stringify(current));
    sourceElements = Array.from(sources);
    for (const el of sourceElements) el.classList.add(SOURCE_CLASS);
    if (items.length > 1) setDragImage(dataTransfer, items.length);
  }

  function end() {
    highlight(null);
    for (const el of sourceElements) el.classList.remove(SOURCE_CLASS);
    sourceElements = [];
    current = null;
  }

  function onDragOver(event) {
    if (!current) return;
    const target = targetOf(event.target);
    if (!target) {
      highlight(null);
      return;
    }
    const folderId = folderIdOf(target);
    const allowed = canDrop(current, folderId) && isWritable(folderId);
    highlight(allowed ? target : null);
    // Must be a member of the source's effectAllowed or the browser resets
    // it to 'none' and refuses the drop; the listing declares 'copyMove'.
    event.dataTransfer.dropEffect = allowed ? 'move' : 'none';
    if (allowed) event.preventDefault();
  }

  function onDrop(event) {
    const drag = current;
    const target = targetOf(event.target);
    end();
    if (!drag || !target) return;
    const folderId = folderIdOf(target);
    if (!canDrop(drag, folderId) || !isWritable(folderId)) return;
    event.preventDefault();
    // Each item says where it came from, as the clipboard's items do: the
    // transfer only pre-checks name collisions for a file changing folder.
    window.dispatchEvent(
      new CustomEvent(EVENT, {
        detail: {
          items: drag.items.map((item) => ({ ...item, sourceFolder: drag.sourceFolder })),
          targetFolderId: folderId,
          targetName: target.dataset.dropFolderName || '',
        },
      })
    );
  }

  function isDragging() {
    return current !== null;
  }

  function isOverTarget(event) {
    return current !== null && targetOf(event.target) !== null;
  }

  document.addEventListener('dragover', onDragOver);
  document.addEventListener('drop', onDrop);
  document.addEventListener('dragend', end);
  // A refreshed listing may carry new permissions; the table seeds again.
  window.addEventListener('folder-browser-replaced', forgetActions);

  return {
    MIME,
    EVENT,
    TARGET_CLASS,
    SOURCE_CLASS,
    start,
    end,
    canDrop,
    isDragging,
    isOverTarget,
    rememberActions,
    forgetActions,
    onDragOver,
    onDrop,
  };
})();
