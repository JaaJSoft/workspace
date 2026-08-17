/**
 * <chat-message-group> — the message bubble shell, one implementation for
 * both rendering paths (following <user-avatar> / <inline-alert> / <tag-chip>).
 *
 * The SHELL is: the msg-group row (alignment, avatar column, message column),
 * the bubble container with its own/other colour variants, the reply-quote
 * block, the attachment chip/preview fragments, and the footer line. Compact
 * mode is part of the shell: every density-dependent class is written as an
 * Alpine `:class` binding on `chatPrefs.compactMessageView`, so the element
 * only builds markup and Alpine keeps it reactive — the pending bubble now
 * follows compact mode like every other bubble.
 *
 * Server path (chat/ui/partials/message_group.html) writes the element with
 * slotted server-rendered content:
 *
 *   <chat-message-group own author-id="3" author-username="alice" ...>
 *     <div slot="message" id="msg-<uuid>" data-message-uuid="<uuid>" ...>
 *       ...bubble content (body, AI steps, link previews, markers)...
 *       <script type="application/json">{"media": [...], "files": [...]}</script>
 *       <div data-part="audio">...</div>
 *       <div data-part="after-bubble">...</div>
 *       <div data-part="below">...</div>
 *     </div>
 *     <span slot="footer">12:34</span>
 *   </chat-message-group>
 *
 * Per-message contract — each slot="message" child IS the bubble element
 * (so server-rendered ids, data-message-uuid and data-body survive into the
 * live DOM for scroll anchors, edits and e2e selectors). The shell:
 *   - styles it as the bubble (colour variant, compact padding),
 *   - prepends the reply quote built from its data-reply-* attributes
 *     (data-reply-uuid / -author / -preview / -thread-root / -deleted),
 *   - replaces its inline JSON script with the attachment block built from
 *     that payload ({media: [{uuid,name,type,is_image}], files:
 *     [{uuid,name,type,size}]}) — the script's position marks where the block
 *     goes; audio players ride along as data-part="audio" children and are
 *     placed between the media grid and the file chips,
 *   - wraps it in the hover-anchor wrapper and moves data-part="after-bubble"
 *     children (retry button, hover toolbar) next to it inside that wrapper,
 *     and data-part="below" children (reactions, thread footers) after it,
 *   - a data-deleted child renders as the "Message deleted" placeholder.
 * data-has-body on the child drives the body/attachment separator, exactly
 * like `{% if msg.body_html %}` used to.
 *
 * Optimistic path (chatMessagesMixin._injectOptimisticMessage) creates the
 * element at runtime and sets its properties before insertion,
 * <inline-alert>-style — attributes and children are read once at connect:
 *
 *   const group = document.createElement('chat-message-group');
 *   group.setAttribute('own', ''); group.setAttribute('pending', '');
 *   group.body = 'hello'; group.replyInfo = {...}; group.pendingFiles = [...];
 *   container.appendChild(group);
 *
 * The `pending` attribute covers the optimistic extras: reduced bubble
 * opacity, a loading spinner in place of the footer slot, non-interactive
 * quote/attachments (no viewer clicks, no save-to-files buttons).
 *
 * Alpine interplay: the element upgrades synchronously on insertion, before
 * Alpine initializes the subtree (both the initial page and alpine-ajax
 * merges insert parsed-but-uninitialized nodes), so slotted directives are
 * moved — never cloned — prior to their first evaluation, and directives the
 * shell writes (`:class`, `@click`, x-if avatar templates) initialize with
 * the rest. Re-insertions keep the already-built tree, so an alpine-ajax
 * morph or reparenting never re-runs slot handling.
 */
(function defineChatMessageGroup() {
  const COMPACT_BUBBLE_PAD = "chatPrefs.compactMessageView ? 'px-2.5 py-1' : 'px-3 py-1.5'";
  const ATTACHMENT_URL = '/api/v1/chat/attachments/';

  function el(tag, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function icon(name, className) {
    const i = el('i', className);
    i.setAttribute('data-lucide', name);
    return i;
  }

  // Server UUIDs are interpolated into Alpine expressions (scrollToMessage,
  // saveAttachmentToFiles). They come from our own DB, but gate on shape
  // anyway so a malformed payload can never smuggle script into a directive.
  function safeUuid(value) {
    return typeof value === 'string' && /^[0-9a-fA-F-]{1,36}$/.test(value) ? value : null;
  }

  class ChatMessageGroup extends HTMLElement {
    connectedCallback() {
      // Render once: re-insertions (alpine-ajax moves, reparenting) keep the
      // already-built tree.
      if (this._rendered) return;
      this._rendered = true;
      this.render();
      // Belt and braces with observeLucideIcons(): hydrate built icons
      // directly so they don't depend on the observer having seen them.
      if (typeof lucide !== 'undefined' && lucide.createIcons) {
        const icons = this.querySelectorAll('[data-lucide]:not(svg)');
        if (icons.length) lucide.createIcons({ nodes: icons });
      }
    }

    get _own() { return this.hasAttribute('own'); }
    get _pending() { return this.hasAttribute('pending'); }
    get _prefix() { return this.getAttribute('id-prefix') || 'msg'; }

    render() {
      const own = this._own;
      this.classList.add('msg-group', own ? 'msg-group-end' : 'msg-group-start', 'flex', 'gap-2');
      if (own) this.classList.add('flex-row-reverse');
      this.setAttribute(':class', "chatPrefs.compactMessageView ? 'mb-1.5' : 'mb-3'");

      // Partition the authored children before rebuilding.
      const messages = [];
      const footerNodes = [];
      for (const node of Array.from(this.children)) {
        const slot = node.getAttribute('slot');
        if (slot === 'message') messages.push(node);
        else if (slot === 'footer') footerNodes.push(node);
      }

      const column = el('div', `flex flex-col ${own ? 'items-end' : 'items-start'} gap-0.5 min-w-0 max-w-[75%]`);
      if (!own) column.appendChild(this._header());

      if (this._pending) {
        column.appendChild(this._pendingMessage());
      } else {
        for (const bubble of messages) this._mountMessage(column, bubble);
      }

      column.appendChild(this._footer(footerNodes));
      this.replaceChildren(this._avatarColumn(), column);
    }

    // ── Group chrome ─────────────────────────────────────────

    _avatarColumn() {
      const wrap = el('div', 'flex-shrink-0 mt-auto');
      wrap.setAttribute(':class', "chatPrefs.compactMessageView ? 'w-6' : 'w-8'");
      const userId = this.getAttribute('author-id') || '';
      const username = this.getAttribute('author-username') || '';
      // Avatar size follows density; x-if (not x-show) so only one
      // <user-avatar> is live at a time.
      for (const [expr, size] of [
        ['!chatPrefs.compactMessageView', 'sm'],
        ['chatPrefs.compactMessageView', 'xs'],
      ]) {
        const tpl = document.createElement('template');
        tpl.setAttribute('x-if', expr);
        tpl.innerHTML = window.userAvatarTag(userId, username, { size, presence: true, card: true });
        wrap.appendChild(tpl);
      }
      return wrap;
    }

    _header() {
      const row = el('div', 'flex items-center gap-1.5 px-1 text-xs');
      const name = el('span', 'font-semibold text-base-content');
      name.textContent = this.getAttribute('author-name') || this.getAttribute('author-username') || '';
      row.appendChild(name);
      const authorId = Number.parseInt(this.getAttribute('author-id'), 10);
      if (Number.isInteger(authorId)) {
        const badge = el('span', 'badge badge-xs badge-secondary gap-0.5');
        badge.setAttribute('x-show', `isBotMessage({author: {id: ${authorId}}})`);
        badge.setAttribute('x-cloak', '');
        badge.textContent = 'AI';
        row.appendChild(badge);
      }
      return row;
    }

    _footer(footerNodes) {
      const row = el('div', 'flex items-center gap-1 px-1');
      if (this._pending) {
        row.appendChild(el('span', 'loading loading-dots loading-xs text-base-content/40'));
      } else {
        for (const node of footerNodes) {
          node.removeAttribute('slot');
          row.appendChild(node);
        }
      }
      return row;
    }

    // ── Server-rendered messages ─────────────────────────────

    _mountMessage(column, bubble) {
      bubble.removeAttribute('slot');
      bubble.setAttribute(':class', COMPACT_BUBBLE_PAD);

      if (bubble.hasAttribute('data-deleted')) {
        bubble.classList.add('msg-bubble', 'rounded-2xl', 'text-sm', 'italic', 'bg-base-200', 'text-base-content/40');
        bubble.textContent = 'Message deleted';
        column.appendChild(bubble);
        return;
      }

      bubble.classList.add(
        'msg-bubble', 'rounded-2xl', 'text-sm', 'text-base-content',
        this._own ? 'bg-info/15' : 'bg-base-200',
      );

      // Pull the non-bubble parts out before styling settles.
      const afterBubble = [];
      const below = [];
      const audios = [];
      let attachmentsData = null;
      let attachmentsAnchor = null;
      for (const node of Array.from(bubble.children)) {
        if (node.matches('script[type="application/json"]')) {
          try {
            attachmentsData = JSON.parse(node.textContent);
          } catch {
            attachmentsData = null;
          }
          attachmentsAnchor = node;
          continue;
        }
        const part = node.getAttribute('data-part');
        if (part === 'after-bubble') { afterBubble.push(node); node.remove(); }
        else if (part === 'below') { below.push(node); node.remove(); }
        else if (part === 'audio') { audios.push(node); node.remove(); }
      }

      // The JSON script marks where the attachment block goes (after link
      // previews, before the edited/pinned markers).
      if (attachmentsAnchor) {
        const media = (attachmentsData?.media || []).map((m) => ({
          uuid: m.uuid,
          name: m.name,
          type: m.type,
          isImage: !!m.is_image,
          src: ATTACHMENT_URL + m.uuid,
        }));
        const files = attachmentsData?.files || [];
        attachmentsAnchor.replaceWith(this._attachmentsFragment({
          media,
          files,
          audios,
          hasBody: bubble.hasAttribute('data-has-body'),
        }));
      }

      const replyUuid = safeUuid(bubble.getAttribute('data-reply-uuid'));
      if (replyUuid) {
        bubble.prepend(this._quote({
          uuid: replyUuid,
          threadRoot: safeUuid(bubble.getAttribute('data-reply-thread-root')),
          deleted: bubble.hasAttribute('data-reply-deleted'),
          author: bubble.getAttribute('data-reply-author') || '',
          preview: bubble.getAttribute('data-reply-preview') || '',
          interactive: true,
        }));
      }

      const wrap = el('div', 'relative group/msg hover:z-20 max-w-full');
      wrap.appendChild(bubble);
      for (const node of afterBubble) wrap.appendChild(node);
      column.appendChild(wrap);
      for (const node of below) column.appendChild(node);
    }

    // ── Pending (optimistic) message ─────────────────────────

    _pendingMessage() {
      const wrap = el('div', 'relative group/msg hover:z-20 max-w-full');
      const bubble = el('div', 'msg-bubble rounded-2xl text-sm bg-info/15 text-base-content opacity-70');
      bubble.setAttribute(':class', COMPACT_BUBBLE_PAD);

      if (this.replyInfo) {
        bubble.appendChild(this._quote({
          author: this.replyInfo.author || '',
          preview: this.replyInfo.body || '',
          interactive: false,
        }));
      }

      const body = typeof this.body === 'string' ? this.body : '';
      if (body) {
        const bodyEl = el('div', 'msg-body prose prose-sm max-w-[36rem] break-words');
        body.split('\n').forEach((line, i) => {
          if (i) bodyEl.appendChild(document.createElement('br'));
          bodyEl.appendChild(document.createTextNode(line));
        });
        bubble.appendChild(bodyEl);
      }

      const pendingFiles = this.pendingFiles || [];
      if (pendingFiles.length) {
        const media = [];
        const files = [];
        for (const f of pendingFiles) {
          // A local object URL is what makes a preview possible; without one
          // the file falls back to the generic chip.
          if (f.type && f.type.startsWith('image/') && f._preview) {
            media.push({ name: f.name, isImage: true, src: f._preview });
          } else if (f.type && f.type.startsWith('video/') && f._preview) {
            media.push({ name: f.name, isImage: false, src: f._preview });
          } else {
            files.push({ name: f.name, size: f.size });
          }
        }
        bubble.appendChild(this._attachmentsFragment({
          media, files, audios: [], hasBody: !!body,
        }));
      }

      wrap.appendChild(bubble);
      return wrap;
    }

    // ── Shared fragments ─────────────────────────────────────

    _quote({ uuid, threadRoot, deleted, author, preview, interactive }) {
      const own = this._own;
      const base = 'flex gap-2 my-1.5 rounded-lg px-2 py-1 no-underline';
      const surface = own ? 'bg-info/15' : 'bg-base-300/50';
      let root;
      if (interactive) {
        root = el('a', `${base} cursor-pointer transition-colors ${surface} ${own ? 'hover:bg-info/25' : 'hover:bg-base-300'}`);
        root.setAttribute('href', `#${this._prefix}-${uuid}`);
        // The quoted message's thread root rides along so a quote pointing
        // into a thread opens the panel instead of paging the main flow back
        // forever looking for a reply that is not there.
        // x-on:, not the @ shorthand: setAttribute rejects '@' in a name.
        const args = threadRoot ? `'${uuid}', '${threadRoot}'` : `'${uuid}'`;
        root.setAttribute('x-on:click.prevent', `scrollToMessage(${args})`);
      } else {
        root = el('div', `${base} ${surface}`);
      }
      root.appendChild(el('div', 'w-0.5 flex-shrink-0 rounded-full bg-info'));
      const text = el('div', 'min-w-0 flex-1');
      if (deleted) {
        const span = el('span', 'text-xs italic opacity-50');
        span.textContent = 'Message deleted';
        text.appendChild(span);
      } else {
        const authorEl = el('span', 'text-xs font-semibold text-info');
        authorEl.textContent = author;
        const previewEl = el('p', 'text-xs text-base-content/70 truncate');
        previewEl.textContent = preview;
        text.append(authorEl, previewEl);
      }
      root.appendChild(text);
      return root;
    }

    _attachmentsFragment({ media, files, audios, hasBody }) {
      const frag = document.createDocumentFragment();
      if (!media.length && !files.length && !audios.length) return frag;
      if (hasBody) {
        frag.appendChild(el('div', `border-t ${this._own ? 'border-info/30' : 'border-base-300'} my-1.5`));
      }
      const box = el('div', `flex flex-col gap-1.5 mb-1.5${hasBody ? '' : ' mt-1.5'}`);
      if (media.length === 1) box.appendChild(this._singleMedia(media[0]));
      else if (media.length > 1) box.appendChild(this._mediaGrid(media));
      for (const audio of audios) box.appendChild(audio);
      for (const file of files) box.appendChild(this._fileChip(file));
      frag.appendChild(box);
      return frag;
    }

    _viewerOpener(node, item) {
      node.addEventListener('click', () => {
        window.dispatchEvent(new CustomEvent('open-chat-attachment-viewer', {
          detail: { uuid: item.uuid, name: item.name, type: item.type },
        }));
      });
    }

    _stampAttachment(node, item) {
      node.setAttribute('data-attachment-uuid', item.uuid);
      node.setAttribute('data-attachment-name', item.name || '');
      node.setAttribute('data-attachment-type', item.type || '');
    }

    _saveButton(item, { grid = false, chip = false } = {}) {
      const btn = el('button', chip
        ? 'btn btn-ghost btn-xs btn-square opacity-0 group-hover/att:opacity-100 transition-opacity flex-shrink-0'
        : 'absolute top-1 right-1 btn btn-xs btn-square bg-base-100/80 hover:bg-base-100 border-base-300 shadow-sm opacity-0 group-hover/att:opacity-100 transition-opacity');
      // .stop inside the mosaic: the whole cell opens the viewer on click.
      // x-on:, not the @ shorthand: setAttribute rejects '@' in a name.
      btn.setAttribute(grid ? 'x-on:click.stop' : 'x-on:click', `saveAttachmentToFiles('${item.uuid}')`);
      btn.setAttribute('title', 'Save to Files');
      btn.appendChild(icon('folder-down', chip ? 'w-3.5 h-3.5' : 'w-3 h-3'));
      return btn;
    }

    _mediaImg(item, className) {
      const img = el('img', className);
      img.src = item.src;
      img.alt = item.name || '';
      img.loading = 'lazy';
      img.decoding = 'async';
      return img;
    }

    _mediaVideo(item, { grid }) {
      const video = el('video', grid ? 'w-full h-full object-cover' : 'max-h-64 max-w-full rounded-lg object-contain');
      video.src = item.src;
      video.preload = 'metadata';
      const overlay = el('div', 'absolute inset-0 flex items-center justify-center bg-black/20 hover:bg-black/30 transition-colors');
      const circle = el('div', `${grid ? 'w-10 h-10' : 'w-12 h-12'} rounded-full bg-base-100/80 flex items-center justify-center`);
      circle.appendChild(icon('play', grid ? 'w-5 h-5' : 'w-6 h-6'));
      overlay.appendChild(circle);
      return [video, overlay];
    }

    _singleMedia(item) {
      const interactive = !this._pending && safeUuid(item.uuid);
      const wrap = el('div', 'relative group/att inline-block');
      if (interactive) this._stampAttachment(wrap, item);
      const zone = el('div', interactive ? 'block cursor-pointer' : 'block');
      if (interactive) this._viewerOpener(zone, item);
      if (item.isImage) {
        zone.appendChild(this._mediaImg(item,
          `max-h-64 max-w-full rounded-lg object-contain cursor-pointer hover:opacity-90 transition-opacity${this._pending ? ' opacity-60' : ''}`));
      } else {
        const box = el('div', `relative max-h-64 max-w-full rounded-lg overflow-hidden${this._pending ? ' opacity-60' : ''}`);
        box.append(...this._mediaVideo(item, { grid: false }));
        zone.appendChild(box);
      }
      wrap.appendChild(zone);
      if (interactive) wrap.appendChild(this._saveButton(item));
      return wrap;
    }

    _mediaGrid(media) {
      const grid = el('div', `grid gap-1 w-64 sm:w-80 max-w-full rounded-lg overflow-hidden ${media.length >= 5 ? 'grid-cols-3' : 'grid-cols-2'}`);
      media.forEach((item, index) => {
        const interactive = !this._pending && safeUuid(item.uuid);
        // Three items: the first spans the full row, the two others share it.
        const shape = media.length === 3 && index === 0 ? 'col-span-2 aspect-[2/1]' : 'aspect-square';
        const cell = el('div', `relative group/att overflow-hidden ${shape}${interactive ? ' cursor-pointer' : ''}${this._pending ? ' opacity-60' : ''}`);
        if (interactive) {
          this._stampAttachment(cell, item);
          this._viewerOpener(cell, item);
        }
        if (item.isImage) {
          cell.appendChild(this._mediaImg(item, 'w-full h-full object-cover hover:opacity-90 transition-opacity'));
        } else {
          cell.append(...this._mediaVideo(item, { grid: true }));
        }
        if (interactive) cell.appendChild(this._saveButton(item, { grid: true }));
        grid.appendChild(cell);
      });
      return grid;
    }

    _fileChip(item) {
      const own = this._own;
      const interactive = !this._pending && safeUuid(item.uuid);
      const row = el('div', `flex items-center gap-0.5${interactive ? ' group/att' : ''} min-w-0`);
      if (interactive) this._stampAttachment(row, item);
      const body = el('div', [
        'flex items-center gap-2 p-2 rounded-lg',
        own ? 'bg-info/15' : 'bg-base-300/50',
        interactive ? `${own ? 'hover:bg-info/25' : 'hover:bg-base-300'} transition-colors` : '',
        'min-w-0 flex-1',
        interactive ? 'cursor-pointer' : '',
      ].filter(Boolean).join(' '));
      if (interactive) this._viewerOpener(body, item);
      body.appendChild(icon('file', 'w-4 h-4 flex-shrink-0'));
      const name = el('span', 'truncate text-xs font-medium');
      name.textContent = item.name || '';
      body.appendChild(name);
      if (item.size) {
        const size = el('span', 'text-[0.65rem] opacity-60 flex-shrink-0');
        size.textContent = formatFileSize(item.size);
        body.appendChild(size);
      }
      if (interactive) body.appendChild(icon('eye', 'w-3.5 h-3.5 flex-shrink-0 opacity-60'));
      row.appendChild(body);
      if (interactive) row.appendChild(this._saveButton(item, { chip: true }));
      return row;
    }
  }

  if (!customElements.get('chat-message-group')) {
    customElements.define('chat-message-group', ChatMessageGroup);
  }
})();
