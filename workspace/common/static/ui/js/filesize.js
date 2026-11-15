// Client-side mirror of Django's built-in `filesizeformat` template filter,
// for values computed in the browser (selection totals, pending uploads) that
// the server can't render. It reuses the filter's msgids, so a size reads the
// same on both sides ("1,5 Kio" in French), with the non-breaking space
// Django inserts via avoid_wrapping.
function formatFileSize(bytes) {
  // Math.trunc mirrors the filter's int() conversion (truncation toward zero)
  let size = Math.trunc(Number(bytes)) || 0;
  const NBSP = ' ';
  if (size < 1024) {
    const label = interpolate(ngettext('%(size)s byte', '%(size)s bytes', size), { size }, true);
    return label.replace(' ', NBSP);
  }
  // Django's number_format: one decimal, the language's separator, no grouping.
  const locale = (window.getUserLocale && window.getUserLocale()) || 'en';
  const number = (value) => value.toLocaleString(locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    useGrouping: false,
  });
  const units = [gettext('%s KB'), gettext('%s MB'), gettext('%s GB'), gettext('%s TB')];
  for (const unit of units) {
    size /= 1024;
    if (size < 1024) {
      return interpolate(unit, [number(size)]).replace(' ', NBSP);
    }
  }
  return interpolate(gettext('%s PB'), [number(size / 1024)]).replace(' ', NBSP);
}
