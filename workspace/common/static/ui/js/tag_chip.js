/**
 * <tag-chip> — the colored pill for tags (files, notes) and labels (projects).
 *
 * One implementation for both rendering paths. Django templates write the
 * element directly; Alpine `x-for` loops bind the same attributes:
 *
 *   <tag-chip name="{{ tag.name }}" color="{{ tag.color }}" icon="{{ tag.icon }}"></tag-chip>
 *   <tag-chip :name="tag.name" :color="tag.color" removable @remove="drop(tag)"></tag-chip>
 *
 * Attributes:
 *   - name       text of the pill.
 *   - color      any CSS color. Empty renders the neutral pill.
 *   - icon       lucide icon name, rendered before the label.
 *   - size       "sm" for dense lists (board cards, backlog rows, listings).
 *   - removable  the chip carries a trailing control, so the pill gets the
 *                tighter right padding. In attribute mode the element also
 *                renders the cross and dispatches a bubbling `remove` event.
 *
 * Slot mode: a chip written with children and no `name` keeps its own
 * content as the label and only gets the pill styling. That is what the
 * projects label settings need — their chip embeds an inline rename input,
 * a color dropdown and a delete button.
 *
 * base.html loads this in <head> without `defer`, so the element upgrades
 * while the document parses: server-rendered chips never flash unstyled.
 */

// Shared palette: the swatches offered by every tag/label color picker.
// '' is the neutral pill (no color set).
window.TAG_CHIP_COLORS = [
  { name: 'None', value: '' },
  { name: 'Red', value: '#ef4444' },
  { name: 'Orange', value: '#f97316' },
  { name: 'Yellow', value: '#eab308' },
  { name: 'Green', value: '#22c55e' },
  { name: 'Blue', value: '#3b82f6' },
  { name: 'Purple', value: '#a855f7' },
  { name: 'Pink', value: '#ec4899' },
  { name: 'Cyan', value: '#06b6d4' },
];

// Pill geometry. Split out of the element so it can be unit-tested without
// a DOM, and applied with add/remove so a caller's own classes survive.
window.tagChipClasses = function tagChipClasses(size, removable) {
  const classes = [
    'inline-flex',
    'items-center',
    'gap-1',
    'rounded-full',
    'border',
    'border-base-300',
    'bg-base-100',
    'text-xs',
    'align-middle',
  ];
  if (size === 'sm') {
    classes.push('py-0', 'min-h-[20px]', removable ? 'pl-2' : 'px-2');
  } else {
    classes.push('py-0.5', 'min-h-[26px]', removable ? 'pl-2.5' : 'px-2.5');
  }
  if (removable) classes.push('pr-1');
  return classes;
};

(function defineTagChip() {
  // The line box sits low on the font's baseline, so a chip centered by
  // flex alone reads ~1px too low. text-box trims the box to the cap
  // height; browsers without it fall back to a whole-pixel nudge
  // (fractional offsets shimmer across zoom levels, so keep it integer).
  const LABEL_CLASSES =
    'relative -top-px [text-box:trim-both_cap_alphabetic] supports-[text-box:trim-both_cap_alphabetic]:top-0';

  // Inline SVG on purpose: lucide.createIcons() does not process nodes
  // Alpine clones out of an x-for template.
  const CROSS_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

  class TagChip extends HTMLElement {
    static get observedAttributes() {
      return ['name', 'color', 'icon', 'size', 'removable'];
    }

    connectedCallback() {
      if (!this._connected) {
        // A chip that arrives with content owns it. Attribute mode is the
        // default, including for Alpine chips whose `:name` may land
        // before or after this callback.
        this._slotMode = this.childNodes.length > 0 && !this.hasAttribute('name');
        this._appliedClasses = [];
        this._connected = true;
      }
      this.render();
    }

    attributeChangedCallback() {
      if (this._connected) this.render();
    }

    render() {
      const removable = this.hasAttribute('removable');

      this.classList.remove(...this._appliedClasses);
      this._appliedClasses = window.tagChipClasses(this.getAttribute('size'), removable);
      this.classList.add(...this._appliedClasses);

      const color = (this.getAttribute('color') || '').trim();
      this.style.borderColor = color;
      this.style.color = color;

      // Slot mode never rebuilds its children: they belong to the caller,
      // and Alpine may have inserted more of them (x-if content) since.
      if (this._slotMode) return;

      const name = this.getAttribute('name') || '';
      if (!this._label) {
        this._label = document.createElement('span');
        this._label.className = LABEL_CLASSES;
      }
      this._label.textContent = name;

      const children = [];
      const icon = this.getAttribute('icon');
      if (icon) {
        const iconEl = document.createElement('i');
        iconEl.setAttribute('data-lucide', icon);
        iconEl.className = 'w-3 h-3 flex-shrink-0';
        children.push(iconEl);
      }
      children.push(this._label);
      if (removable) children.push(this._removeButton(name));
      this.replaceChildren(...children);

      if (icon && typeof lucide !== 'undefined' && lucide.createIcons) {
        lucide.createIcons({ nodes: [this] });
      }
    }

    _removeButton(name) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-ghost btn-circle btn-xs min-h-0 h-5 w-5 p-0';
      button.setAttribute('aria-label', `Remove ${name}`.trim());
      button.innerHTML = CROSS_SVG;
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        this.dispatchEvent(new CustomEvent('remove', { bubbles: true }));
      });
      return button;
    }
  }

  if (!customElements.get('tag-chip')) {
    customElements.define('tag-chip', TagChip);
  }
})();
