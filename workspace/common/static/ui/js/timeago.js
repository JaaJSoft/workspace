// Shared relative-time formatting. Single source of truth for "time ago"
// strings on the client, mirroring workspace.common.dates.time_ago on the
// server - keep the two in sync so server- and client-rendered timestamps
// on the same page match.
// Registers window.formatTimeAgo(value) and window.formatLastSeenAgo(value).
(function () {
  // 'en-US' pins the English month abbreviations the server's %b emits.
  const _DATE_PARTS_OPTIONS = { year: 'numeric', month: 'short', day: '2-digit' };

  // Building the formatter costs far more than formatting with it, and a
  // feed asks for one per <time data-localtime="relative">, so there is one
  // formatter per configured zone. Without a configured zone the formatter
  // binds the browser zone at construction, which can change while the
  // page is open, so that case is not cached.
  const _datePartsFormatters = new Map();
  function _datePartsFormatter(tz) {
    if (!tz) return new Intl.DateTimeFormat('en-US', _DATE_PARTS_OPTIONS);
    let formatter = _datePartsFormatters.get(tz);
    if (!formatter) {
      formatter = new Intl.DateTimeFormat('en-US', { timeZone: tz, ..._DATE_PARTS_OPTIONS });
      _datePartsFormatters.set(tz, formatter);
    }
    return formatter;
  }

  function _dateParts(d, tz) {
    const parts = {};
    _datePartsFormatter(tz).formatToParts(d).forEach((p) => { parts[p.type] = p.value; });
    return parts;
  }

  // 'just now' (< 1 min), '5m ago', '2h ago', '3d ago' (< 1 week), then the
  // absolute date - 'Feb 01' within the current year, 'Feb 01, 2025'
  // otherwise - evaluated in the user's timezone.
  window.formatTimeAgo = function formatTimeAgo(value, nowMs) {
    if (!value) return '';
    const d = value instanceof Date ? value : new Date(value);
    if (isNaN(d)) return '';
    const now = nowMs === undefined ? Date.now() : nowMs;
    const sec = Math.floor((now - d.getTime()) / 1000);
    if (sec < 60) return 'just now';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
    if (sec < 604800) return Math.floor(sec / 86400) + 'd ago';
    const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
    const then = _dateParts(d, tz);
    const label = then.month + ' ' + then.day;
    return then.year === _dateParts(new Date(now), tz).year ? label : label + ', ' + then.year;
  };

  // "Last seen" variant shown next to away/offline presence labels: skips
  // the first minute ("just now" contradicts the status) and prefixes a dot.
  window.formatLastSeenAgo = function formatLastSeenAgo(value, nowMs) {
    if (!value) return '';
    const d = value instanceof Date ? value : new Date(value);
    if (isNaN(d)) return '';
    const now = nowMs === undefined ? Date.now() : nowMs;
    if (now - d.getTime() < 60000) return '';
    return '· ' + window.formatTimeAgo(d, now);
  };
})();
