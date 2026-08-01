// Local timezone formatting for <time data-localtime> elements.
// The zone comes from <html data-timezone> (the user's stored setting);
// without it, formatting falls back to the browser timezone.
// Registers window.convertLocaltimes(root), window.getUserTimeZone(),
// window.userTzDayKey(date) and a MutationObserver that formats any
// <time data-localtime> nodes added to the DOM later.
(function () {
  function getUserTimeZone() {
    return document.documentElement.getAttribute('data-timezone') || undefined;
  }

  // 'en-CA' formats as YYYY-MM-DD, giving a comparable day key in the zone.
  function _dayKey(d, tz) {
    return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(d);
  }

  function userTzDayKey(d) {
    return _dayKey(d, getUserTimeZone());
  }

  function _dateLabelLocal(d, tz) {
    const now = new Date();
    const target = _dayKey(d, tz);
    if (target === _dayKey(now, tz)) return 'Today';
    if (target === _dayKey(new Date(now.getTime() - 86400000), tz)) return 'Yesterday';
    return d.toLocaleDateString(undefined, { timeZone: tz, month: 'short', day: 'numeric' });
  }

  function _formatLocaltime(el) {
    const iso = el.getAttribute('datetime');
    if (!iso) return;
    const d = new Date(iso);
    if (isNaN(d)) return;
    const tz = getUserTimeZone();
    const fmt = el.dataset.localtime || 'time';
    switch (fmt) {
      case 'time':
        el.textContent = d.toLocaleTimeString(undefined, { timeZone: tz, hour: '2-digit', minute: '2-digit' });
        break;
      case 'date':
        el.textContent = _dateLabelLocal(d, tz);
        break;
      case 'datetime':
        el.textContent = d.toLocaleDateString(undefined, { timeZone: tz, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        break;
      case 'relative': {
        const sec = Math.floor((Date.now() - d.getTime()) / 1000);
        if (sec < 60) el.textContent = 'just now';
        else if (sec < 3600) { const m = Math.floor(sec / 60); el.textContent = m + ' minute' + (m > 1 ? 's' : '') + ' ago'; }
        else if (sec < 86400) { const h = Math.floor(sec / 3600); el.textContent = h + ' hour' + (h > 1 ? 's' : '') + ' ago'; }
        else { const dy = Math.floor(sec / 86400); el.textContent = dy + ' day' + (dy > 1 ? 's' : '') + ' ago'; }
        break;
      }
      case 'smart': {
        const isToday = _dayKey(d, tz) === _dayKey(new Date(), tz);
        el.textContent = isToday
          ? d.toLocaleTimeString(undefined, { timeZone: tz, hour: '2-digit', minute: '2-digit' })
          : d.toLocaleDateString(undefined, { timeZone: tz, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        break;
      }
      case 'full':
        el.textContent = d.toLocaleDateString(undefined, { timeZone: tz, year: 'numeric', month: 'short', day: 'numeric' }) + ' · ' + d.toLocaleTimeString(undefined, { timeZone: tz, hour: '2-digit', minute: '2-digit' });
        break;
    }
  }

  window.getUserTimeZone = getUserTimeZone;
  window.userTzDayKey = userTzDayKey;

  window.convertLocaltimes = function (root) {
    (root || document).querySelectorAll('time[data-localtime]').forEach(_formatLocaltime);
  };

  // Initial conversion
  window.convertLocaltimes();

  // Observe for dynamically added elements
  new MutationObserver(function (mutations) {
    mutations.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        if (n.matches && n.matches('time[data-localtime]')) _formatLocaltime(n);
        if (n.querySelectorAll) n.querySelectorAll('time[data-localtime]').forEach(_formatLocaltime);
      });
    });
  }).observe(document.body, { childList: true, subtree: true });
})();
