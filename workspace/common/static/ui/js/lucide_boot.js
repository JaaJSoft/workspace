/**
 * Draws the icons the server rendered, then watches for the ones Alpine adds
 * later. Must load after lucide.js, which defines observeLucideIcons.
 */
document.addEventListener('DOMContentLoaded', function() {
  if (typeof lucide !== 'undefined') {
    initLucideIcons();
    observeLucideIcons();
  }
});
