/**
 * <file-preview> — a file shown inside something else: a note that embeds
 * it, a chat message, a task. One element for every module.
 *
 * The server draws the preview (each file viewer's render_preview, in the
 * files module); this element only asks for it, through the batch endpoint
 * of the module that owns the file, and opens the full viewer on a click.
 *
 *   <file-preview src="<batch endpoint>" uuid="<uuid>" name="report.pdf"></file-preview>
 *
 * Attributes:
 *   - src          the owning module's preview batch endpoint. It answers
 *                  ?uuids=<uuid>,...&targets=<id>,... with each preview
 *                  wrapped in the slot id it was asked for, and leaves out a
 *                  file the user may not open. Without src the element shows
 *                  its placeholder and asks nothing (a page without a session).
 *   - uuid         the file.
 *   - name         what the host calls the file: shown while loading, and in
 *                  the placeholder of a file that cannot be shown.
 *   - open-event   the window event a click dispatches, with the detail the
 *                  file viewer reads ({uuid, name, type, galleryId, ...});
 *                  defaults to the closest [data-file-preview-event].
 *   - viewer-url, download-url, files
 *                  carried into that detail (viewerUrl, downloadUrl, isFile)
 *                  for a viewer that would not resolve them on its own.
 *   - bare         the media alone, without the caption under it.
 *   - fit="cover"  fill the box the host gives it (a grid cell).
 *
 * The element reflects where it stands in `state`: loading, ready, or
 * unavailable. Elements connected in the same task share one request per
 * src, through alpine-ajax: each preview merges into its own slot. A file
 * already drawn on the page is drawn again from that markup, with no request.
 *
 * base.html loads this in <head>, before Alpine: elements connected before
 * Alpine starts wait for it.
 */
(function () {
  // Under the server's MAX_PREVIEWS (files/ui/viewers.py).
  const BATCH_SIZE = 50;
  const SLOT_CLASS = 'file-preview-slot';
  // What a click inside a preview must leave alone: the controls it holds.
  const INTERACTIVE = 'a, button, audio, input, select, textarea';

  // What each file's preview looked like, by endpoint. A host that draws
  // the same file again (the chat redraws its whole list after a send, the
  // editor its node views) shows it at once, its media from the browser
  // cache, instead of a spinner and a request per element. Only previews
  // that open are kept: a placeholder may stop being one. A reload starts
  // afresh.
  const DRAWN_MAX = 200;
  const drawn = new Map();

  function drawnKey(src, uuid) {
    return `${src}\n${uuid}`;
  }

  function remember(key, html) {
    drawn.delete(key);
    drawn.set(key, html);
    if (drawn.size > DRAWN_MAX) drawn.delete(drawn.keys().next().value);
  }

  let counter = 0;
  // The Alpine bundle starts Alpine as soon as it sets window.Alpine, so a
  // script running after it finds it ready; one in <head> waits for it.
  let alpineReady = !!window.Alpine;
  let flushScheduled = false;
  const queues = new Map();

  document.addEventListener('alpine:initialized', () => {
    alpineReady = true;
    if (queues.size) scheduleFlush();
  });

  // A slot the answer left out is a file the user may not open, or one
  // that no longer exists. alpine-ajax's default would remove the slot and
  // throw; the element shows its placeholder instead.
  document.addEventListener('ajax:missing', (event) => {
    const target = event.detail && event.detail.target;
    if (!target || !target.classList || !target.classList.contains(SLOT_CLASS)) return;
    event.preventDefault();
    const element = target.closest('file-preview');
    if (element) element._fail();
  });

  // The URL of one batch: the files and the slots their previews go in, in
  // the same order.
  function batchUrl(src, pairs) {
    const params = new URLSearchParams();
    params.set('uuids', pairs.map((pair) => pair.uuid).join(','));
    params.set('targets', pairs.map((pair) => pair.slot).join(','));
    return src + (src.includes('?') ? '&' : '?') + params.toString();
  }

  function scheduleFlush() {
    if (flushScheduled || !alpineReady) return;
    flushScheduled = true;
    setTimeout(flush, 0);
  }

  function flush() {
    flushScheduled = false;
    const batches = Array.from(queues.entries());
    queues.clear();
    for (const [src, elements] of batches) {
      const pending = Array.from(elements).filter((element) => element.isConnected);
      for (let i = 0; i < pending.length; i += BATCH_SIZE) {
        load(src, pending.slice(i, i + BATCH_SIZE));
      }
    }
  }

  function enqueue(element) {
    const src = element.getAttribute('src');
    if (!queues.has(src)) queues.set(src, new Set());
    queues.get(src).add(element);
    scheduleFlush();
  }

  function load(src, elements) {
    for (const element of elements) element._requested = true;
    const pairs = elements.map((element) => ({ uuid: element.getAttribute('uuid'), slot: element._slotId }));
    // $ajax is an Alpine magic, reachable only through an expression; the
    // object keeps Alpine from calling the function it would otherwise get
    // back. The first element anchors the request's events.
    const ajax = window.Alpine.evaluate(elements[0], '{ send: $ajax }').send;
    ajax(batchUrl(src, pairs), { targets: pairs.map((pair) => pair.slot), focus: false })
      .then(
        () => {
          // alpine-ajax skips a target that is out of the document when the
          // answer comes: such a preview asks again once it is back.
          for (const element of elements) {
            if (element.getAttribute('state') === 'loading' && !element.isConnected) {
              element._requested = false;
            }
          }
        },
        () => {
          // A network failure merges nothing: whatever is still waiting
          // shows the placeholder rather than spin forever.
          for (const element of elements) {
            if (element.getAttribute('state') === 'loading') element._fail();
          }
        },
      );
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  class FilePreview extends HTMLElement {
    connectedCallback() {
      // Moved rather than created (an editor redrawing its node views
      // detaches and reattaches them): only a preview not asked for yet
      // goes back in the queue its disconnection took it out of.
      if (this._slotId) {
        if (this.getAttribute('state') === 'loading' && !this._requested) enqueue(this);
        return;
      }
      this._slotId = 'file-preview-' + (++counter);
      this._openable = false;
      this.addEventListener('ajax:merged', (event) => {
        if (event.target.id !== this._slotId) return;
        const html = event.target.innerHTML;
        this._ready();
        if (this._openable) {
          remember(drawnKey(this.getAttribute('src'), this.getAttribute('uuid')), html);
        }
      });
      this.addEventListener('click', (event) => this._onClick(event));
      this.addEventListener('keydown', (event) => {
        // Only the element itself: keys inside its own controls are theirs.
        if (event.target !== this || (event.key !== 'Enter' && event.key !== ' ')) return;
        this._onClick(event);
      });
      // An Alpine x-for inserts the element first and binds :uuid and
      // :name right after, in the same task.
      queueMicrotask(() => this._start());
    }

    _start() {
      const src = this.getAttribute('src');
      if (!src || !this.getAttribute('uuid')) {
        this._fail();
        return;
      }
      const slot = el('div', SLOT_CLASS);
      slot.id = this._slotId;
      const html = drawn.get(drawnKey(src, this.getAttribute('uuid')));
      if (html !== undefined) {
        slot.innerHTML = html;
        this.replaceChildren(slot);
        this._ready();
        return;
      }
      this.setAttribute('state', 'loading');
      const loading = el('div', 'file-preview-body file-preview-loading flex items-center gap-2 min-w-0');
      loading.appendChild(el('span', 'loading loading-spinner loading-xs shrink-0'));
      loading.appendChild(el('span', 'file-preview-name truncate text-sm', this.getAttribute('name') || ''));
      slot.appendChild(loading);
      this.replaceChildren(slot);
      enqueue(this);
    }

    disconnectedCallback() {
      for (const elements of queues.values()) elements.delete(this);
      this.querySelectorAll('audio, video').forEach((media) => media.pause());
    }

    _ready() {
      const body = this.querySelector('.file-preview-body');
      this._openable = !!(body && body.hasAttribute('data-preview-openable'));
      this._name = (body && body.dataset.previewName) || this.getAttribute('name') || '';
      this._type = (body && body.dataset.previewType) || '';
      // A preview holding its own player keeps that player as its control:
      // a button around it would nest one control in another.
      this._setOperable(this._openable && !this.querySelector(INTERACTIVE));
      this.setAttribute('state', 'ready');
      if (this._openWhenReady) {
        this._openWhenReady = false;
        this._open();
      }
      // A format the browser cannot draw falls back to the server thumbnail.
      this.querySelectorAll('img[data-fallback-src]').forEach((img) => {
        const fallback = () => {
          const src = img.dataset.fallbackSrc;
          if (!src) return;
          delete img.dataset.fallbackSrc;
          img.src = src;
        };
        img.addEventListener('error', fallback, { once: true });
        if (img.complete && img.naturalWidth === 0) fallback();
      });
    }

    // Reachable and operable from the keyboard, as the click is with a mouse.
    _setOperable(operable) {
      if (operable) {
        this.tabIndex = 0;
        this.setAttribute('role', 'button');
        this.setAttribute('aria-label', this._name);
      } else {
        this.removeAttribute('tabindex');
        this.removeAttribute('role');
        this.removeAttribute('aria-label');
      }
    }

    _fail() {
      this._openable = false;
      this._openWhenReady = false;
      this._setOperable(false);
      this.setAttribute('state', 'unavailable');
      const slot = el('div', SLOT_CLASS);
      const body = el('div', 'file-preview-body file-preview-placeholder file-preview-unavailable flex items-center gap-2 min-w-0');
      const icon = el('i', 'w-4 h-4 shrink-0 opacity-60');
      icon.setAttribute('data-lucide', 'lock');
      body.appendChild(icon);
      const text = el('div', 'flex flex-col min-w-0');
      const name = this.getAttribute('name');
      if (name) text.appendChild(el('span', 'file-preview-name truncate text-sm font-medium', name));
      text.appendChild(el('span', 'file-preview-meta text-xs opacity-70',
        gettext("This file was deleted, or you don't have access to it.")));
      body.appendChild(text);
      slot.appendChild(body);
      this.replaceChildren(slot);
      if (typeof window.renderLucideIcons === 'function') window.renderLucideIcons(this);
    }

    _openEvent() {
      const own = this.getAttribute('open-event');
      if (own) return own;
      const host = this.closest('[data-file-preview-event]');
      return host ? host.getAttribute('data-file-preview-event') : '';
    }

    _onClick(event) {
      if (event.target.closest(INTERACTIVE)) return;
      if (this.getAttribute('state') === 'loading') {
        // Clicked before the preview arrived: whether the file opens is not
        // known yet, so the click waits for the answer instead of being lost.
        this._openWhenReady = true;
        return;
      }
      if (this._open()) event.preventDefault();
    }

    _open() {
      const name = this._openEvent();
      if (!this._openable || !name) return false;
      window.dispatchEvent(new CustomEvent(name, { detail: this.openerDetail() }));
      return true;
    }

    // What the file viewer is opened with (file_viewer.js).
    openerDetail() {
      const gallery = this.closest('[data-attachment-gallery]');
      const detail = {
        uuid: this.getAttribute('uuid'),
        name: this._name || this.getAttribute('name') || '',
        type: this._type || '',
        galleryId: gallery ? gallery.id : undefined,
      };
      if (this.hasAttribute('viewer-url')) detail.viewerUrl = this.getAttribute('viewer-url');
      if (this.hasAttribute('download-url')) detail.downloadUrl = this.getAttribute('download-url');
      if (this.hasAttribute('files')) detail.isFile = true;
      return detail;
    }
  }

  window.filePreview = { batchUrl, BATCH_SIZE };
  if (!customElements.get('file-preview')) customElements.define('file-preview', FilePreview);
})();
