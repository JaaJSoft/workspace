/**
 * Draws the icons the server rendered, then watches for the ones Alpine adds
 * later. Loaded from base.html at the end of <body>, after lucide.js has
 * defined observeLucideIcons.
 */
document.addEventListener('DOMContentLoaded', function() {
  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
    observeLucideIcons();
  }
});
