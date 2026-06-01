// Programmatic alert builder. The default <inline_alert> partial is for
// server-rendered messages; this helper lets JS code construct equivalent
// alerts at runtime (e.g. after a fetch error).
const InlineAlert = {
  create({ type = 'info', message, title, dismissible = false, iconName, className = '' } = {}) {
    const icons = {
      success: 'check-circle',
      error: 'x-circle',
      warning: 'alert-triangle',
      info: 'info',
    };
    const icon = iconName || icons[type] || icons.info;

    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} text-white ${className}`;

    const iconEl = document.createElement('i');
    iconEl.setAttribute('data-lucide', icon);
    iconEl.className = 'w-5 h-5 stroke-current shrink-0';
    alertDiv.appendChild(iconEl);

    if (title) {
      const wrap = document.createElement('div');
      const h3 = document.createElement('h3');
      h3.className = 'font-bold';
      h3.textContent = title;
      const sub = document.createElement('div');
      sub.className = 'text-sm';
      sub.textContent = message;
      wrap.appendChild(h3);
      wrap.appendChild(sub);
      alertDiv.appendChild(wrap);
    } else {
      const span = document.createElement('span');
      span.textContent = message;
      alertDiv.appendChild(span);
    }

    if (dismissible) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-ghost btn-xs btn-square ml-auto';
      btn.setAttribute('aria-label', 'Dismiss');
      btn.textContent = '✕';
      btn.addEventListener('click', () => alertDiv.remove());
      alertDiv.appendChild(btn);
    }

    return alertDiv;
  },

  show(container, options) {
    const alert = this.create(options);
    if (typeof container === 'string') {
      container = document.querySelector(container);
    }
    container.appendChild(alert);
    return alert;
  },
};
