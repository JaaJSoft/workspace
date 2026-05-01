/* ── Mail contact card popover ─────────────────────────────── */

/**
 * Build a mail contact card DOM node (no innerHTML — all safe DOM methods).
 * @param {string} name - Contact display name (may be empty)
 * @param {string} email - Contact email address
 * @returns {HTMLElement}
 */
function _cleanName(raw) {
  if (!raw || typeof raw !== 'string') return '';
  return raw.replace(/^[^a-zA-Z\u00C0-\u024F]+|[^a-zA-Z\u00C0-\u024F]+$/g, '').trim();
}

function _initial(str) {
  if (!str) return '?';
  const m = str.match(/[a-zA-Z\u00C0-\u024F]/);
  return m ? m[0].toUpperCase() : str[0].toUpperCase();
}

function _buildMailCard(name, email) {
  name = _cleanName(name);
  email = (email && typeof email === 'string') ? email.trim() : '';

  const root = document.createElement('div');
  root.className = 'p-3 w-64';

  // Avatar + info row
  const row = document.createElement('div');
  row.className = 'flex items-center gap-3 mb-2';

  const avatarWrap = document.createElement('div');
  avatarWrap.className = 'avatar placeholder';
  const avatarInner = document.createElement('div');
  avatarInner.className = 'w-10 h-10 bg-warning/15 text-warning rounded-full flex items-center justify-center font-semibold';
  avatarInner.textContent = _initial(name || email);
  avatarWrap.appendChild(avatarInner);
  row.appendChild(avatarWrap);

  const info = document.createElement('div');
  info.className = 'min-w-0 flex-1';
  const nameEl = document.createElement('div');
  nameEl.className = 'font-semibold text-sm truncate';
  nameEl.textContent = name || email;
  info.appendChild(nameEl);
  if (name) {
    const emailEl = document.createElement('div');
    emailEl.className = 'text-xs text-base-content/50 truncate';
    emailEl.textContent = email;
    info.appendChild(emailEl);
  }
  row.appendChild(info);
  root.appendChild(row);

  // Action buttons
  const actions = document.createElement('div');
  actions.className = 'flex gap-1';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'btn btn-ghost btn-xs flex-1 gap-1';
  const copyIcon = document.createElement('i');
  copyIcon.setAttribute('data-lucide', 'copy');
  copyIcon.className = 'w-3 h-3';
  copyBtn.appendChild(copyIcon);
  copyBtn.appendChild(document.createTextNode(' Copy email'));
  copyBtn.addEventListener('click', function() {
    navigator.clipboard.writeText(email);
    copyBtn.textContent = 'Copied!';
    setTimeout(function() {
      copyBtn.textContent = '';
      copyBtn.appendChild(copyIcon);
      copyBtn.appendChild(document.createTextNode(' Copy email'));
    }, 1500);
  });
  actions.appendChild(copyBtn);

  const sendBtn = document.createElement('button');
  sendBtn.className = 'btn btn-ghost btn-xs flex-1 gap-1';
  const sendIcon = document.createElement('i');
  sendIcon.setAttribute('data-lucide', 'send');
  sendIcon.className = 'w-3 h-3';
  sendBtn.appendChild(sendIcon);
  sendBtn.appendChild(document.createTextNode(' Send email'));
  sendBtn.addEventListener('click', function() {
    // If already on the mail page, dispatch a custom event to open compose
    const composeDialog = document.getElementById('mail-compose-dialog');
    if (composeDialog) {
      document.dispatchEvent(new CustomEvent('mail:compose', { detail: { to: email } }));
    } else {
      // Navigate to mail with compose query param
      window.location.href = '/mail?compose=' + encodeURIComponent(email);
    }
  });
  actions.appendChild(sendBtn);

  root.appendChild(actions);
  return root;
}

/**
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
      document.body.appendChild(popover);
      wrapper._mailCardPopover = popover;
    }
    popover.textContent = '';
    popover.appendChild(_buildMailCard(name, email));

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

