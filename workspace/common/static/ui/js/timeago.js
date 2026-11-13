// Shared relative-time formatting. Single source of truth for "time ago"
// strings on the client, mirroring workspace.common.dates.time_ago on the
// server - keep the two in sync so server- and client-rendered timestamps
// on the same page match.
// Registers window.formatTimeAgo(value) and window.formatLastSeenAgo(value).
(function () {
  // 'en-US' pins the English month abbreviations the server's %b emits.
  const _datePartsFormatter = window.zonedFormatter('en-US', { year: 'numeric', month: 'short', day: '2-digit' });

  function _dateParts(d, tz) {
    const parts = {};
    _datePartsFormatter(tz).formatToParts(d).forEach((p) => { parts[p.type] = p.value; });
    return parts;
  }

  // Any other interface language takes the browser's short date in that
  // language ('1 juil.', '1 juil. 2025'), the order the server's translated
  // "M d" / "M d, Y" formats follow. Django and Intl abbreviate a few months
  // differently ('fév.' / 'févr.'), so the two labels can differ by a letter.
  const _localizedFormatters = new Map();
  function _localizedDate(d, tz, locale, withYear) {
    const key = locale + (withYear ? '|y' : '');
    let formatter = _localizedFormatters.get(key);
    if (!formatter) {
      const options = { day: 'numeric', month: 'short' };
      if (withYear) options.year = 'numeric';
      formatter = window.zonedFormatter(locale, options);
      _localizedFormatters.set(key, formatter);
    }
    return formatter(tz).format(d);
  }

  // 'just now' (< 1 min), '5m ago', '2h ago', '3d ago' (< 1 week), then the
  // absolute date - 'Feb 01' within the current year, 'Feb 01, 2025'
  // otherwise - evaluated in the user's timezone and language.
  window.formatTimeAgo = function formatTimeAgo(value, nowMs) {
    if (!value) return '';
    const d = value instanceof Date ? value : new Date(value);
    if (isNaN(d)) return '';
    const now = nowMs === undefined ? Date.now() : nowMs;
    const sec = Math.floor((now - d.getTime()) / 1000);
    if (sec < 60) return gettext('just now');
    if (sec < 3600) return interpolate(gettext('%(count)sm ago'), { count: Math.floor(sec / 60) }, true);
    if (sec < 86400) return interpolate(gettext('%(count)sh ago'), { count: Math.floor(sec / 3600) }, true);
    if (sec < 604800) return interpolate(gettext('%(count)sd ago'), { count: Math.floor(sec / 86400) }, true);
    const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
    const then = _dateParts(d, tz);
    const sameYear = then.year === _dateParts(new Date(now), tz).year;
    const locale = window.getUserLocale ? window.getUserLocale() : undefined;
    if (locale && !locale.toLowerCase().startsWith('en')) {
      return _localizedDate(d, tz, locale, !sameYear);
    }
    const label = then.month + ' ' + then.day;
    return sameYear ? label : label + ', ' + then.year;
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
