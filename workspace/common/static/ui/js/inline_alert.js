/**
 * <inline-alert> — the bordered inline message box, one implementation for
 * both rendering paths. Django templates write the element directly; runtime
 * JS creates it with document.createElement:
 *
 *   <inline-alert type="error" message="Something went wrong."></inline-alert>
 *
 *   const alert = document.createElement('inline-alert');
 *   alert.setAttribute('type', 'error');
 *   alert.setAttribute('message', 'Something went wrong.');
 *   container.appendChild(alert);
 *
 * Attributes:
 *   - type         info | success | warning | error (default info); picks
 *                  the border tint and the default icon.
 *   - message      plain-text body.
 *   - title        bold heading above the message. Consumed and removed on
 *                  first render so it doesn't double as a native tooltip.
 *   - dismissible  boolean attribute — trailing ✕ removes the alert.
 *   - icon         lucide icon name override; "none" renders no icon.
 *
 * Slot mode: an element with child content keeps it as the body, taking
 * priority over `message` (the same trick <tag-chip> uses). Wrap dynamic
 * text this way:
 *
 *   <inline-alert type="error"><span x-text="error"></span></inline-alert>
 *
 * Actions: children marked slot="actions" render in a trailing row, in both
 * modes. Style them yourself (btn btn-xs btn-ghost/primary/...); wire
 * behaviour with @click / addEventListener; add data-dismiss to also remove
 * the alert:
 *
 *   <inline-alert message="A new version is available.">
 *     <button slot="actions" class="btn btn-xs btn-primary" @click="reload()">Reload</button>
 *     <button slot="actions" class="btn btn-xs btn-ghost" data-dismiss>Ignore</button>
 *   </inline-alert>
 *
 * Attributes and children are read once, on first connect: author them
 * before inserting the element, and use slot mode (not attribute bindings)
 * for text that changes afterwards. base.html loads this at the end of
 * <body> so server-rendered elements upgrade with their children already
 * parsed; a <head> load would run connectedCallback mid-parse, before the
 * children exist, and slot/actions assembly would break. Until the element
 * is defined, base.html's `inline-alert:not(:defined)` rule hides it.
 */

// Per-type styling, shared by every alert. Exposed for tests.
window.INLINE_ALERT_TYPES = {
  success: { border: 'border-success/30', icon: 'circle-check', iconColor: 'text-success' },
  error: { border: 'border-error/30', icon: 'circle-x', iconColor: 'text-error' },
  warning: { border: 'border-warning/30', icon: 'triangle-alert', iconColor: 'text-warning' },
  info: { border: 'border-info/30', icon: 'info', iconColor: 'text-info' },
};

(function defineInlineAlert() {
  const CONTAINER_CLASSES = [
    'flex', 'items-start', 'gap-3', 'rounded-lg', 'border', 'bg-base-200/50', 'px-4', 'py-3',
  ];

  // Inline SVG on purpose (same as <tag-chip>'s remove cross): the dismiss
  // button has no text, so a data-lucide icon that fails to hydrate would
  // leave it zero-sized and unclickable.
  const CROSS_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

  class InlineAlert extends HTMLElement {
    connectedCallback() {
      // Render once: re-insertions (reparenting, alpine-ajax moves) keep
      // the already-built tree.
      if (this._rendered) return;
      this._rendered = true;
      this.render();
    }

    render() {
      const style = window.INLINE_ALERT_TYPES[this.getAttribute('type')] || window.INLINE_ALERT_TYPES.info;
      this.setAttribute('role', 'alert');
      // add() rather than a className assignment, so the author's own
      // classes (mb-4, text-xs, ...) survive.
      this.classList.add(...CONTAINER_CLASSES, style.border);

      // Partition the authored children before rebuilding.
      const actionNodes = [];
      const bodyNodes = [];
      for (const node of Array.from(this.childNodes)) {
        if (node.nodeType === 1 && node.getAttribute('slot') === 'actions') actionNodes.push(node);
        else bodyNodes.push(node);
      }

      const parts = [];

      const iconName = this.getAttribute('icon') || '';
      if (iconName !== 'none') {
        const iconEl = document.createElement('i');
        iconEl.setAttribute('data-lucide', iconName || style.icon);
        iconEl.className = `w-4 h-4 shrink-0 mt-0.5 ${style.iconColor}`;
        parts.push(iconEl);
      }

      parts.push(this._body(bodyNodes));

      if (actionNodes.length) {
        const row = document.createElement('div');
        row.className = 'flex gap-2 shrink-0';
        for (const node of actionNodes) {
          if (node.hasAttribute('data-dismiss')) node.addEventListener('click', () => this.remove());
          row.appendChild(node);
        }
        parts.push(row);
      }

      if (this.hasAttribute('dismissible')) parts.push(this._dismissButton());

      this.replaceChildren(...parts);

      // Belt and braces with the observeLucideIcons() MutationObserver:
      // render icons directly so alerts inside Alpine template clones
      // (x-if / x-for) don't depend on the observer having seen them.
      if (typeof lucide !== 'undefined' && lucide.createIcons) {
        lucide.createIcons({ nodes: [this] });
      }
    }

    _body(bodyNodes) {
      // Whitespace-only text nodes are formatting, not slot content.
      const hasSlotContent = bodyNodes.some(
        (node) => node.nodeType === 1 || (node.textContent || '').trim() !== '',
      );
      if (hasSlotContent) {
        const wrap = document.createElement('div');
        wrap.className = 'flex-1 text-sm text-base-content/80';
        for (const node of bodyNodes) wrap.appendChild(node);
        return wrap;
      }

      const message = this.getAttribute('message') || '';
      const title = this.getAttribute('title');
      if (title) {
        // Drop the attribute so it doesn't double as a native tooltip.
        this.removeAttribute('title');
        const wrap = document.createElement('div');
        wrap.className = 'flex-1';
        const titleEl = document.createElement('p');
        titleEl.className = 'text-sm font-semibold text-base-content';
        titleEl.textContent = title;
        const messageEl = document.createElement('p');
        messageEl.className = 'text-sm text-base-content/70 mt-0.5';
        messageEl.textContent = message;
        wrap.appendChild(titleEl);
        wrap.appendChild(messageEl);
        return wrap;
      }

      const span = document.createElement('span');
      span.className = 'flex-1 text-sm text-base-content/80';
      span.textContent = message;
      return span;
    }

    _dismissButton() {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'shrink-0 mt-0.5 text-base-content/40 hover:text-base-content/70 transition-colors';
      btn.setAttribute('aria-label', 'Dismiss');
      btn.innerHTML = CROSS_SVG;
      btn.addEventListener('click', () => this.remove());
      return btn;
    }
  }

  if (!customElements.get('inline-alert')) {
    customElements.define('inline-alert', InlineAlert);
  }
})();
