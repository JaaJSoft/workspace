// Client-side mirror of the `filesize` template filter
// (workspace/common/templatetags/ui_filters.py). Same units, divisor and
// precision so client-computed values (selection totals, pending uploads)
// render exactly like server-rendered sizes. Keep both in sync.
function formatFileSize(bytes) {
  let size = Number(bytes) || 0;
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (size < 1024) {
      return unit === 'B' ? `${size} ${unit}` : `${size.toFixed(1)} ${unit}`;
    }
    size /= 1024;
  }
  return `${size.toFixed(1)} PB`;
}
