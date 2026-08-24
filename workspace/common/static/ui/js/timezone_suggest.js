/**
 * Timezone bookkeeping: first sign-in auto-detects; afterwards the stored
 * setting wins and a divergent browser zone only surfaces a suggestion in
 * #tz-suggest-banner.
 */
(function() {
  const putTimezone = function(value) {
    return fetch('/api/v1/settings/core/timezone', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCSRFToken()
      },
      body: JSON.stringify({
        value: value
      }),
    });
  };
  const stored = document.documentElement.getAttribute('data-timezone') || '';
  const detected = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  if (!detected) return;
  if (!stored) {
    putTimezone(detected).catch(function() {});
    return;
  }
  if (stored === detected) return;
  if (localStorage.getItem('tz-suggest-dismissed') === detected) return;
  document.addEventListener('DOMContentLoaded', function() {
    const banner = document.getElementById('tz-suggest-banner');
    if (!banner) return;
    const zone = document.createElement('strong');
    zone.textContent = detected;
    const text = document.createElement('span');
    text.append('Your browser looks like it is in ', zone, '. Update your timezone?');
    let saving = false;
    const alert = document.createElement('inline-alert');
    alert.setAttribute('type', 'info');
    alert.setAttribute('icon', 'globe');
    const update = document.createElement('button');
    update.type = 'button';
    update.setAttribute('slot', 'actions');
    update.className = 'btn btn-xs btn-primary';
    update.textContent = 'Update';
    update.addEventListener('click', function() {
      if (saving) return;
      saving = true;
      putTimezone(detected).then(function(resp) {
        if (!resp.ok) throw new Error('timezone save failed');
        window.location.reload();
      }).catch(function() {
        saving = false;
        text.textContent = 'Could not save your timezone. Try again?';
      });
    });
    const ignore = document.createElement('button');
    ignore.type = 'button';
    ignore.setAttribute('slot', 'actions');
    ignore.className = 'btn btn-xs btn-ghost';
    ignore.textContent = 'Ignore';
    ignore.addEventListener('click', function() {
      localStorage.setItem('tz-suggest-dismissed', detected);
      // Hide the wrapper instead of dismissing the inner alert, because removing only the alert would leave the opaque wrapper as an empty floating box.
      banner.classList.add('hidden');
    });
    alert.append(text, update, ignore);
    banner.appendChild(alert);
    banner.classList.remove('hidden');
  });
})();
