// Local timezone formatting for <time data-localtime> elements.
// Registers window.convertLocaltimes(root) and a MutationObserver that
// formats any <time data-localtime> nodes added to the DOM later.
(function () {
  function _dateLabelLocal(d) {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const diff = (today - target) / 86400000;
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function _formatLocaltime(el) {
    const iso = el.getAttribute('datetime');
    if (!iso) return;
    const d = new Date(iso);
    if (isNaN(d)) return;
    const fmt = el.dataset.localtime || 'time';
    switch (fmt) {
      case 'time':
        el.textContent = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        break;
      case 'date':
        el.textContent = _dateLabelLocal(d);
        break;
      case 'datetime':
        el.textContent = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        break;
      case 'relative': {
        const sec = Math.floor((Date.now() - d.getTime()) / 1000);
        if (sec < 60) el.textContent = 'just now';
        else if (sec < 3600) { const m = Math.floor(sec / 60); el.textContent = m + ' minute' + (m > 1 ? 's' : '') + ' ago'; }
        else if (sec < 86400) { const h = Math.floor(sec / 3600); el.textContent = h + ' hour' + (h > 1 ? 's' : '') + ' ago'; }
        else { const dy = Math.floor(sec / 86400); el.textContent = dy + ' day' + (dy > 1 ? 's' : '') + ' ago'; }
        break;
      }
      case 'smart': {
        const now = new Date();
        const isToday = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
        el.textContent = isToday
          ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
          : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        break;
      }
      case 'full':
        el.textContent = d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) + ' · ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        break;
    }
  }

  window.convertLocaltimes = function (root) {
    (root || document).querySelectorAll('time[data-localtime]').forEach(_formatLocaltime);
  };

  // Initial conversion
  window.convertLocaltimes();

  // Observe for dynamically added elements
  new MutationObserver(function (mutations) {
    mutations.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        if (n.matches && n.matches('time[data-localtime]')) _formatLocaltime(n);
        if (n.querySelectorAll) n.querySelectorAll('time[data-localtime]').forEach(_formatLocaltime);
      });
    });
  }).observe(document.body, { childList: true, subtree: true });
})();
