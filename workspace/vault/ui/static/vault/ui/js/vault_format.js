// Turning stored values into what a row shows. Both vault screens format the
// same things the same way, and a second copy is a second place for them to
// drift.
window.vaultFormat = {
  // The server sends ISO timestamps. The locale is the browser's, on purpose:
  // nothing about a vault is server-rendered, this included.
  shortDate(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '-';
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  },
};
