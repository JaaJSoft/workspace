// Turning stored values into what a row shows. Both vault screens format the
// same things the same way, and a second copy is a second place for them to
// drift.
window.vaultFormat = {
  // The server sends ISO timestamps.
  shortDate(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '-';
    const locale = typeof window.getUserLocale === 'function' ? window.getUserLocale() : undefined;
    return date.toLocaleDateString(locale, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  },
};
