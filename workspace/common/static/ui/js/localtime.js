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

  function _tzParts(d, tz) {
    const dtf = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    });
    const parts = {};
    for (const p of dtf.formatToParts(d)) parts[p.type] = p.value;
    return parts;
  }

  function _tzOffsetMs(tz, date) {
    const p = _tzParts(date, tz);
    return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - date.getTime();
  }

  // 'YYYY-MM-DD[THH:MM[:SS]]' wall-clock in tz -> ISO instant. Without tz,
  // falls back to the browser zone (matching new Date(naive) semantics).
  function wallClockToIso(naive, tz) {
    if (!naive) return null;
    if (!tz) return new Date(naive.length === 10 ? naive + 'T00:00' : naive).toISOString();
    const [datePart, timePart] = naive.split('T');
    const [y, mo, d] = datePart.split('-').map(Number);
    const [h = 0, mi = 0, s = 0] = (timePart || '00:00').split(':').map(Number);
    const guess = Date.UTC(y, mo - 1, d, h, mi, s);
    let ts = guess - _tzOffsetMs(tz, new Date(guess));
    // Second pass fixes instants near DST transitions where the first
    // offset was read on the wrong side of the switch; nonexistent times
    // resolve forward, ambiguous ones to the later (post-switch) instant.
    ts = guess - _tzOffsetMs(tz, new Date(ts));
    return new Date(ts).toISOString();
  }

  // ISO instant -> 'YYYY-MM-DDTHH:MM' wall-clock in tz (datetime-local value).
  function isoToWallClock(iso, tz) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    if (!tz) {
      const pad = (n) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    const p = _tzParts(d, tz);
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
  }

  window.getUserTimeZone = getUserTimeZone;
  window.userTzDayKey = userTzDayKey;
  window.wallClockToIso = wallClockToIso;
  window.isoToWallClock = isoToWallClock;

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
