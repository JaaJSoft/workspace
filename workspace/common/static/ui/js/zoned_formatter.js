// One Intl.DateTimeFormat per explicit timezone. Building a formatter costs
// far more than formatting with it, and a listing asks for one per row, so
// each call site keeps its own cache: zonedFormatter(locale, options)
// returns (tz) => formatter, memoised on the zone. Without a zone the
// formatter binds the browser zone at construction, which can change while
// the page is open, so that case is never cached.
// Registers window.zonedFormatter(locale, options).
(function () {
  window.zonedFormatter = function zonedFormatter(locale, options) {
    const formatters = new Map();
    return function (tz) {
      if (!tz) return new Intl.DateTimeFormat(locale, options);
      let formatter = formatters.get(tz);
      if (!formatter) {
        formatter = new Intl.DateTimeFormat(locale, { ...options, timeZone: tz });
        formatters.set(tz, formatter);
      }
      return formatter;
    };
  };
})();
