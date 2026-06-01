// Programmatic alert builder. The default inline_alert partial
// (ui/partials/inline_alert.html) is for server-rendered messages; this helper
// builds the SAME markup at runtime (e.g. after a fetch error), so a JS-driven
// alert is visually identical to a server-rendered one. Keep the two in sync:
// any class/icon change here must mirror the partial and vice-versa.
const InlineAlert = {
  // Per-type styling, mirrored from inline_alert.html.
  _styles: {
    success: { border: 'border-success/30', icon: 'circle-check', iconColor: 'text-success' },
    error: { border: 'border-error/30', icon: 'circle-x', iconColor: 'text-error' },
    warning: { border: 'border-warning/30', icon: 'triangle-alert', iconColor: 'text-warning' },
    info: { border: 'border-info/30', icon: 'info', iconColor: 'text-info' },
  },

  create({ type = 'info', message, title, dismissible = false, icon = true, iconName, className = '' } = {}) {
    const style = this._styles[type] || this._styles.info;

    const alertDiv = document.createElement('div');
    alertDiv.setAttribute('role', 'alert');
    alertDiv.className =
      `flex items-start gap-3 rounded-lg border bg-base-200/50 px-4 py-3 ${className} ${style.border}`.trim();

    if (icon) {
      const iconEl = document.createElement('i');
      iconEl.setAttribute('data-lucide', iconName || style.icon);
      // Runtime-injected data-lucide icons are rendered by the global
      // observeLucideIcons() MutationObserver (see base.html / lucide.js).
      iconEl.className = `w-4 h-4 shrink-0 mt-0.5 ${style.iconColor}`;
      alertDiv.appendChild(iconEl);
    }

    if (title) {
      const wrap = document.createElement('div');
      wrap.className = 'flex-1';
      const titleEl = document.createElement('p');
      titleEl.className = 'text-sm font-semibold text-base-content';
      titleEl.textContent = title;
      const messageEl = document.createElement('p');
      messageEl.className = 'text-sm text-base-content/70 mt-0.5';
      messageEl.textContent = message;
      wrap.appendChild(titleEl);
      wrap.appendChild(messageEl);
      alertDiv.appendChild(wrap);
    } else {
      const span = document.createElement('span');
      span.className = 'flex-1 text-sm text-base-content/80';
      span.textContent = message;
      alertDiv.appendChild(span);
    }

    if (dismissible) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'shrink-0 mt-0.5 text-base-content/40 hover:text-base-content/70 transition-colors';
      btn.setAttribute('aria-label', 'Dismiss');
      const btnIcon = document.createElement('i');
      btnIcon.setAttribute('data-lucide', 'x');
      btnIcon.className = 'w-4 h-4';
      btn.appendChild(btnIcon);
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
