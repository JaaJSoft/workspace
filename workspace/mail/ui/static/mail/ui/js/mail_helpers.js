/* ── Mail contact card popover ─────────────────────────────── */

function _cleanName(raw) {
  if (!raw || typeof raw !== 'string') return '';
  return raw.replace(/^[^a-zA-Z\u00C0-\u024F]+|[^a-zA-Z\u00C0-\u024F]+$/g, '').trim();
}

// Rendered cards by URL, kept 30s like the user card. Adding a contact
// clears it: every card of that address changes at once.
const _mailCardCache = new Map();
const _MAIL_CARD_CACHE_TTL = 30000;

function _mailCardUrl(name, email) {
  return '/mail/contact-card?email=' + encodeURIComponent(email) + '&name=' + encodeURIComponent(name);
}

async function _mailCardLoad(popover, url) {
  const cached = _mailCardCache.get(url);
  let html = cached && cached.at + _MAIL_CARD_CACHE_TTL > Date.now() ? cached.html : null;
  if (html === null) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) return;
    html = await res.text();
    _mailCardCache.set(url, { html, at: Date.now() });
  }
  if (popover.dataset.url === url) _setPopoverContent(popover, html);
}

function _mailCardSpinner(popover) {
  popover.textContent = '';
  const wrap = document.createElement('div');
  wrap.className = 'p-4 w-72 flex justify-center';
  const spinner = document.createElement('span');
  spinner.className = 'loading loading-spinner loading-sm';
  wrap.appendChild(spinner);
  popover.appendChild(wrap);
}

async function _mailCardAction(event, popover) {
  const button = event.target.closest('[data-mail-card-action]');
  const card = event.target.closest('[data-mail-contact-card]');
  if (!button || !card) return;
  const email = card.dataset.email;
  const action = button.dataset.mailCardAction;
  if (action === 'copy') {
    navigator.clipboard.writeText(email);
    const label = button.querySelector('[data-label]');
    label.textContent = 'Copied!';
    setTimeout(function() { label.textContent = 'Copy email'; }, 1500);
  } else if (action === 'compose') {
    if (document.getElementById('mail-compose-dialog')) {
      document.dispatchEvent(new CustomEvent('mail:compose', { detail: { to: email } }));
    } else {
      window.location.href = '/mail?compose=' + encodeURIComponent(email);
    }
  } else if (action === 'add') {
    button.disabled = true;
    const body = card.dataset.userId
      ? { user_id: Number(card.dataset.userId) }
      : { email, name: card.dataset.name };
    try {
      const res = await fetch('/api/v1/people/promote', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(res.status);
      _mailCardCache.clear();
      await _mailCardLoad(popover, popover.dataset.url);
    } catch (e) {
      button.disabled = false;
      AppAlert.error('Could not add to contacts');
    }
  }
}

/**
 * Show the contact card of an address after a 500ms hover. The card is
 * rendered by the server: the address book's person when it knows the
 * address, otherwise an offer to add it.
 * @param {HTMLElement} wrapper
 * @param {string|object} nameOrAddr - display name string, or {name, email} object
 * @param {string} [email] - email string (if first arg is a name string)
 */
window._mailCardShow = function(wrapper, nameOrAddr, email) {
  // Support both _mailCardShow(el, {name, email}) and _mailCardShow(el, name, email)
  let name;
  if (nameOrAddr && typeof nameOrAddr === 'object') {
    name = nameOrAddr.name;
    email = nameOrAddr.email;
  } else {
    name = nameOrAddr;
  }
  email = (email && typeof email === 'string') ? email.trim() : '';
  if (!email) return;
  const url = _mailCardUrl(_cleanName(name), email);
  window._mailCardCancelHide(wrapper);
  const existing = wrapper._mailCardPopover;
  if (existing && existing.style.display !== 'none' && existing.style.opacity === '1') return;
  if (wrapper._showTimeout) clearTimeout(wrapper._showTimeout);

  wrapper._showTimeout = setTimeout(function() {
    wrapper._showTimeout = null;
    let popover = wrapper._mailCardPopover;
    if (!popover) {
      popover = document.createElement('div');
      popover.className = 'fixed z-[9999] bg-base-100 rounded-xl shadow-lg ring-1 ring-base-300';
      popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
      popover.style.opacity = '0';
      popover.addEventListener('mouseenter', function() { window._mailCardCancelHide(wrapper); });
      popover.addEventListener('mouseleave', function() { window._mailCardScheduleHide(wrapper); });
      popover.addEventListener('click', function(e) { _mailCardAction(e, popover); });
      document.body.appendChild(popover);
      wrapper._mailCardPopover = popover;
    }
    popover.dataset.url = url;
    _mailCardSpinner(popover);
    _mailCardLoad(popover, url);

    const pos = _computePopoverPosition(wrapper);
    popover.style.left = pos.left + 'px';
    popover.style.top = pos.top + 'px';
    wrapper._placement = pos.placement;

    popover.style.display = '';
    popover.style.transition = 'none';
    _applyPopoverTransform(popover, pos.placement, false);
    void popover.offsetHeight;
    popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
    _applyPopoverTransform(popover, pos.placement, true);
  }, 500);
};

window._mailCardScheduleHide = function(wrapper) {
  if (wrapper._showTimeout) { clearTimeout(wrapper._showTimeout); wrapper._showTimeout = null; }
  // Cancel any previously queued hide/close so rapid mouseleave events don't
  // stack up and cause flicker.
  if (wrapper._hideTimeout) { clearTimeout(wrapper._hideTimeout); wrapper._hideTimeout = null; }
  if (wrapper._closeTimeout) { clearTimeout(wrapper._closeTimeout); wrapper._closeTimeout = null; }
  wrapper._hideTimeout = setTimeout(function() {
    const popover = wrapper._mailCardPopover;
    if (popover) {
      _applyPopoverTransform(popover, wrapper._placement || 'bottom', false);
      wrapper._closeTimeout = setTimeout(function() { popover.style.display = 'none'; }, 150);
    }
  }, 200);
};

window._mailCardCancelHide = function(wrapper) {
  if (wrapper._hideTimeout) { clearTimeout(wrapper._hideTimeout); wrapper._hideTimeout = null; }
  if (wrapper._closeTimeout) { clearTimeout(wrapper._closeTimeout); wrapper._closeTimeout = null; }
  const popover = wrapper._mailCardPopover;
  if (popover && popover.style.display !== 'none') {
    _applyPopoverTransform(popover, wrapper._placement || 'bottom', true);
  }
};

