
/**
 * User card popover: the card that floats next to an avatar on hover, built
 * on the popover primitives of ui/js/popover.js.
 *
 * The avatar itself is the <user-avatar> element (ui/js/user_avatar.js); it
 * calls _userCardShow/_userCardScheduleHide from here when given the `card`
 * attribute. The fetch, cache and timers are ui/js/card_popover.js.
 */

/* ── Patch user card status from Alpine presence store ────────── */

const _statusConfig = {
  bot:     { dot: 'bg-secondary', label: 'text-secondary', showAgo: false },
  online:  { dot: 'bg-success',  label: 'text-success',  showAgo: false },
  away:    { dot: 'bg-warning',  label: 'text-warning',  showAgo: true  },
  busy:    { dot: 'bg-error',    label: 'text-error',    showAgo: false },
  offline: { dot: 'bg-base-300', label: 'text-base-content/40', showAgo: true  },
};

/** The presence status as the interface language writes it. */
window.presenceStatusLabel = function presenceStatusLabel(status) {
  switch (status) {
    case 'bot': return gettext('Bot');
    case 'online': return gettext('Online');
    case 'away': return gettext('Away');
    case 'busy': return gettext('Busy');
    default: return gettext('Offline');
  }
};

function _patchCardStatus(container) {
  const el = container.querySelector('[data-user-card-status]');
  if (!el) return;
  const userId = parseInt(el.dataset.userId, 10);
  if (!userId || typeof Alpine === 'undefined') return;

  const status = Alpine.store('presence').statusOf(userId);
  const cfg = _statusConfig[status] || _statusConfig.offline;

  const dot = el.querySelector('[data-status-dot]');
  const label = el.querySelector('[data-status-label]');
  const ago = el.querySelector('[data-status-ago]');

  if (dot) {
    dot.className = 'w-2 h-2 rounded-full shrink-0 ' + cfg.dot;
  }
  if (label) {
    label.className = 'font-medium ' + cfg.label;
    label.textContent = window.presenceStatusLabel(status);
  }
  if (ago) {
    const lastSeen = el.dataset.lastSeen;
    ago.textContent = cfg.showAgo && lastSeen ? window.formatLastSeenAgo(lastSeen) : '';
  }
}

/* ── User card popover ─────────────────────────────────────────── */
const _userCard = window.createHoverCard({ className: 'user-card-popover', onRender: _patchCardStatus });

window._userCardShow = function(wrapper, userId) {
  _userCard.show(wrapper, '/users/' + userId + '/card');
};

window._userCardScheduleHide = function(wrapper) {
  _userCard.scheduleHide(wrapper);
};

/** Tear down the card of an avatar leaving the page: the popover lives on <body>. */
window._userCardDetach = function(wrapper) {
  _userCard.detach(wrapper);
};
