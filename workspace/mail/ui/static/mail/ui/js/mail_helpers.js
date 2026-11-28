/* ── Mail contact card popover ─────────────────────────────── */

function _cleanName(raw) {
  if (!raw || typeof raw !== 'string') return '';
  return raw.replace(/^[^a-zA-Z\u00C0-\u024F]+|[^a-zA-Z\u00C0-\u024F]+$/g, '').trim();
}

// Adding a contact refreshes the card: it changes every card of that address
// at once, so the whole cache goes.
const _mailCard = window.createHoverCard({
  loadingClass: 'w-72',
  onClick: function(event, anchor) { _mailCardAction(event, anchor); },
});

function _mailCardUrl(name, email) {
  return '/mail/contact-card?email=' + encodeURIComponent(email) + '&name=' + encodeURIComponent(name);
}

async function _mailCardAction(event, anchor) {
  const button = event.target.closest('[data-mail-card-action]');
  const card = event.target.closest('[data-mail-contact-card]');
  if (!button || !card) return;
  const email = card.dataset.email;
  const action = button.dataset.mailCardAction;
  if (action === 'copy') {
    navigator.clipboard.writeText(email);
    const label = button.querySelector('[data-label]');
    label.textContent = gettext('Copied!');
    setTimeout(function() { label.textContent = gettext('Copy email'); }, 1500);
  } else if (action === 'compose') {
    if (document.getElementById('mail-compose-dialog')) {
      document.dispatchEvent(new CustomEvent('mail:compose', { detail: { to: email } }));
    } else {
      window.location.href = '/mail?compose=' + encodeURIComponent(email);
    }
  } else if (action === 'add') {
    button.disabled = true;
    const body = card.dataset.userId
      ? { user: Number(card.dataset.userId) }
      : { email, name: card.dataset.name };
    try {
      const res = await fetch('/api/v1/people/promote', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(res.status);
      await _mailCard.refresh(anchor);
    } catch (e) {
      button.disabled = false;
      AppAlert.error(gettext('Could not add to contacts'));
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
  _mailCard.show(wrapper, _mailCardUrl(_cleanName(name), email));
};

window._mailCardScheduleHide = function(wrapper) {
  _mailCard.scheduleHide(wrapper);
};
