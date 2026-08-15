/**
 * <user-avatar> — a user's picture, with an initials fallback, an optional
 * presence ring and dot, and an optional hover card.
 *
 * One implementation for both rendering paths. Django templates write the
 * element directly; Alpine binds the same attributes:
 *
 *   <user-avatar user-id="{{ user.id }}" username="{{ user.username }}" size="sm" presence></user-avatar>
 *   <user-avatar :user-id="member.id" :username="member.username" size="sm" card></user-avatar>
 *
 * Attributes:
 *   - user-id    numeric id. Drives the image URL, the fallback colour and
 *                the presence lookup. Absent for an anonymous placeholder.
 *   - username   alt text, and the source of the initial.
 *   - size       a key of USER_AVATAR_SIZES (default "md").
 *   - presence   opt in to the status ring and dot.
 *   - card       show the user card popover on hover.
 *   - ring       decorative ring, only drawn when there is no user-id
 *                (the anonymous navbar avatar).
 *
 * Geometry invariant — the reason the image and the initials cannot drift
 * apart: the initials are ALWAYS rendered, and the image sits on top of them,
 * absolutely positioned within the same box. A missing picture removes the
 * image and reveals what was already there; no class is swapped, no box is
 * resized, no daisyUI `placeholder` mode is entered. A row of avatars stays
 * aligned whether or not each user uploaded a picture.
 *
 * The size lives on the HOST element, not on an inner wrapper, so a flex or
 * grid parent measures the avatar itself. That is what keeps the element
 * aligned inside buttons, dropdown rows and overlapping stacks without the
 * caller wrapping it in a sizing div.
 *
 * base.html loads this in <head> without `defer`, so the element upgrades
 * while the document parses: server-rendered avatars never flash unstyled.
 */

/* ── Deterministic initials-fallback colors ───────────────────── */

// Mirrors AVATAR_PALETTE in scripts/seed_demo.py (the Tailwind *-500 RGB
// values); keep both lists in lockstep so demo-generated avatars and the
// initials fallback read as one family.
const AVATAR_COLORS = [
  'bg-red-500', 'bg-orange-500', 'bg-yellow-500', 'bg-green-500',
  'bg-emerald-500', 'bg-cyan-500', 'bg-blue-500', 'bg-indigo-500',
  'bg-violet-500', 'bg-purple-500', 'bg-fuchsia-500', 'bg-pink-500',
];

/**
 * Stable background class for a user's initials avatar.
 *
 * @param {number|string} userId
 * @returns {string} one of AVATAR_COLORS, or 'bg-neutral' for invalid input
 */
window.userAvatarColorClass = function (userId) {
  const id = typeof userId === 'number' ? userId : Number.parseInt(userId, 10);
  if (!Number.isInteger(id)) return 'bg-neutral';
  const n = AVATAR_COLORS.length;
  return AVATAR_COLORS[((id % n) + n) % n];
};

// The named scale. `box` goes on the host so the element measures correctly
// in any layout; `text` sizes the fallback initial. Listed as literal strings
// so Tailwind's scanner ships them (the config globs static/**/*.js).
window.USER_AVATAR_SIZES = {
  '2xs': { box: 'w-5 h-5', text: 'text-[10px]' },
  xs: { box: 'w-6 h-6', text: 'text-xs' },
  sm: { box: 'w-8 h-8', text: 'text-xs' },
  md: { box: 'w-10 h-10', text: 'text-sm' },
  lg: { box: 'w-16 h-16', text: 'text-xl' },
  xl: { box: 'w-24 h-24', text: 'text-2xl' },
};

// Host geometry, split out so it can be unit-tested without a DOM and applied
// with add/remove so a caller's own classes survive a re-render.
window.userAvatarHostClasses = function userAvatarHostClasses(size) {
  const step = window.USER_AVATAR_SIZES[size] || window.USER_AVATAR_SIZES.md;
  // `shrink-0` because an avatar in a flex row must never be squashed by a
  // long name next to it; `align-middle` because the host is inline-flex and
  // would otherwise sit on the text baseline inside a line box.
  return ['relative', 'inline-flex', 'shrink-0', 'align-middle', ...step.box.split(' ')];
};

/**
 * Markup for a <user-avatar>, for the few places that still build a chunk of
 * HTML as a string (an optimistic chat bubble, an x-html expression). Prefer
 * writing the element in the template; use this only where there is no
 * template to write it in.
 *
 * @param {number|string} userId
 * @param {string} username
 * @param {{size?: string, presence?: boolean, card?: boolean}} [options]
 * @returns {string}
 */
window.userAvatarTag = function userAvatarTag(userId, username, options) {
  const opts = options || {};
  const size = opts.size || 'md';
  const flags = (opts.presence ? ' presence' : '') + (opts.card ? ' card' : '');
  return (
    `<user-avatar user-id="${escapeHtml(userId)}" username="${escapeHtml(username)}"` +
    ` size="${escapeHtml(size)}"${flags}></user-avatar>`
  );
};

(function defineUserAvatar() {
  // Every connected instance, so one Alpine effect can restyle all of them
  // when the presence store changes. Instances register on connect and
  // deregister on disconnect, which matters under alpine-ajax swaps.
  const instances = new Set();

  function presenceStore() {
    return typeof Alpine !== 'undefined' && Alpine.store ? Alpine.store('presence') : null;
  }

  class UserAvatar extends HTMLElement {
    static get observedAttributes() {
      return ['user-id', 'username', 'size', 'presence', 'card', 'ring'];
    }

    connectedCallback() {
      if (!this._connected) {
        this._appliedClasses = [];
        this._connected = true;
      }
      this.render();
      instances.add(this);
      this._syncCard();
    }

    disconnectedCallback() {
      instances.delete(this);
      // The card popover is appended to <body>, so it outlives the avatar
      // unless it is torn down here (alpine-ajax swaps, x-if teardown).
      const popover = this._userCardPopover;
      if (popover && popover.parentNode) popover.parentNode.removeChild(popover);
    }

    attributeChangedCallback() {
      if (this._connected) {
        this.render();
        this._syncCard();
      }
    }

    get userId() {
      const raw = (this.getAttribute('user-id') || '').trim();
      return raw === '' ? null : raw;
    }

    render() {
      const size = this.getAttribute('size');
      const step = window.USER_AVATAR_SIZES[size] || window.USER_AVATAR_SIZES.md;
      const userId = this.userId;
      const username = this.getAttribute('username') || '';
      const initial = (username || '?').trim().charAt(0).toUpperCase() || '?';
      const withPresence = this.hasAttribute('presence') && userId !== null;

      this.classList.remove(...this._appliedClasses);
      this._appliedClasses = window.userAvatarHostClasses(size);
      this.classList.add(...this._appliedClasses);

      const face = document.createElement('span');
      face.className = [
        'relative',
        'w-full',
        'h-full',
        'rounded-full',
        'overflow-hidden',
        'flex',
        'items-center',
        'justify-center',
        'text-white',
        step.text,
        window.userAvatarColorClass(userId),
      ].join(' ');
      if (withPresence) {
        // Ring SHAPE is static, only its COLOUR is patched from the presence
        // store. A ring whose width lived in the reactive part would vanish
        // entirely whenever the store was unavailable, leaving a dot with no
        // matching ring.
        face.classList.add('ring-2', 'ring-offset-base-100', 'ring-offset-1', 'ring-base-300');
      } else if (this.hasAttribute('ring') && userId === null) {
        face.classList.add('ring-1', 'ring-primary/40', 'ring-offset-base-100', 'ring-offset-1');
      }

      const label = document.createElement('span');
      label.className = 'leading-none select-none';
      label.textContent = initial;
      face.appendChild(label);

      if (userId !== null) {
        const img = document.createElement('img');
        img.src = `/api/v1/users/${encodeURIComponent(userId)}/avatar`;
        img.alt = username;
        img.loading = 'lazy';
        img.decoding = 'async';
        img.className = 'absolute inset-0 w-full h-full object-cover';
        // Removing the image uncovers the initials that are already in place.
        img.addEventListener('error', function onError() {
          img.remove();
        });
        face.appendChild(img);
      }

      const children = [face];

      if (withPresence) {
        const dot = document.createElement('span');
        dot.className =
          'absolute bottom-0 right-0 block w-2.5 h-2.5 rounded-full ring-2 ring-base-100 bg-base-300';
        dot.setAttribute('data-presence-dot', '');
        children.push(dot);
      }

      this._face = face;
      this.replaceChildren(...children);
      this.applyPresence(presenceStore());
    }

    /** Patch the ring and dot colours from the presence store. */
    applyPresence(store) {
      if (!store || !this.hasAttribute('presence')) return;
      const userId = this.userId;
      if (userId === null || !this._face) return;

      const ring = store.ringClass(userId);
      const dotColor = store.dotClass(userId);

      if (this._ringClass !== ring) {
        if (this._ringClass) this._face.classList.remove(this._ringClass);
        this._face.classList.add(ring);
        this._ringClass = ring;
      }
      const dot = this.querySelector('[data-presence-dot]');
      if (dot && this._dotClass !== dotColor) {
        if (this._dotClass) dot.classList.remove(this._dotClass);
        dot.classList.add(dotColor);
        this._dotClass = dotColor;
      }
    }

    /**
     * Wire (or unwire) the hover card. Native listeners rather than Alpine, so
     * the card works identically whether the element was server-rendered or
     * cloned out of an x-for template.
     */
    _syncCard() {
      const wanted = this.hasAttribute('card') && this.userId !== null;
      if (wanted === !!this._cardWired) return;

      if (wanted) {
        this._onEnter = () => window._userCardShow(this, this.userId);
        this._onLeave = () => window._userCardScheduleHide(this);
        this.addEventListener('mouseenter', this._onEnter);
        this.addEventListener('mouseleave', this._onLeave);
        this._cardWired = true;
      } else {
        this.removeEventListener('mouseenter', this._onEnter);
        this.removeEventListener('mouseleave', this._onLeave);
        this._cardWired = false;
      }
    }
  }

  // One effect for every avatar on the page. Reading ringClass/dotClass inside
  // it subscribes to the store's buckets, so a presence snapshot restyles all
  // connected avatars at once.
  function trackPresence() {
    if (typeof Alpine === 'undefined' || !Alpine.effect) return;
    Alpine.effect(() => {
      const store = presenceStore();
      if (!store) return;
      instances.forEach((el) => el.applyPresence(store));
    });
  }
  document.addEventListener('alpine:initialized', trackPresence);

  if (!customElements.get('user-avatar')) {
    customElements.define('user-avatar', UserAvatar);
  }
})();
