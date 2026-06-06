// Client-side mirror of Django's built-in `filesizeformat` template filter,
// for values computed in the browser (selection totals, pending uploads) that
// the server can't render. Output matches the en-us rendering of the filter
// (the project's fixed LANGUAGE_CODE, no LocaleMiddleware), including the
// non-breaking space Django inserts via avoid_wrapping.
function formatFileSize(bytes) {
  // Math.trunc mirrors the filter's int() conversion (truncation toward zero)
  let size = Math.trunc(Number(bytes)) || 0;
  const NBSP = '\u00a0';
  if (size < 1024) {
    return `${size}${NBSP}${size === 1 ? 'byte' : 'bytes'}`;
  }
  for (const unit of ['KB', 'MB', 'GB', 'TB']) {
    size /= 1024;
    if (size < 1024) {
      return `${size.toFixed(1)}${NBSP}${unit}`;
    }
  }
  return `${(size / 1024).toFixed(1)}${NBSP}PB`;
}
