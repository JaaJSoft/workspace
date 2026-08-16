/**
 * AppAlert — floating toast notifications, stacked in the fixed
 * #app-alerts-container that ui/partials/toasts.html renders.
 *
 *   AppAlert.success('Saved');
 *   AppAlert.error('Failed to save');
 *   const el = AppAlert.show({ message: 'Uploading...', duration: 0 });
 *   AppAlert.dismiss(el);
 *
 * show() options: type info|success|warning|error (default info), title,
 * duration in ms (0 keeps the toast until dismissed; default 5000, errors
 * 8000), dismissible (default true), position top-right|top-left|
 * bottom-right|bottom-left|top-center (default bottom-right; there is one
 * container, so the latest toast's position wins).
 *
 * Django messages: toasts.html embeds them as JSON in #django-messages-data;
 * they surface here as staggered top-right toasts on DOMContentLoaded.
 */
(function () {
  const container = document.getElementById('app-alerts-container');

  function show(options) {
    if (!options || !options.message) {
      console.warn('AppAlert.show: message is required');
      return null;
    }

    const {
      message,
      type = 'info',
      title = null,
      duration = 5000,
      dismissible = true,
      position = 'bottom-right'
    } = options;

    // Update container position if needed
    updateContainerPosition(position);

    // Map types to DaisyUI alert classes
    const typeClasses = {
      success: 'alert-success',
      error: 'alert-error',
      warning: 'alert-warning',
      info: 'alert-info'
    };
    const alertClass = typeClasses[type] || typeClasses.info;

    const icons = {
      success: `<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`,
      error: `<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`,
      warning: `<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>`,
      info: `<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`
    };
    const icon = icons[type] || icons.info;

    const alertDiv = document.createElement('div');
    alertDiv.className = `alert ${alertClass} shadow-lg text-white`;

    // Determine animation based on position
    const animationIn = getAnimationName(position, 'in');
    const animationOut = getAnimationName(position, 'out');
    alertDiv.style.animation = `${animationIn} 0.3s ease-out`;
    alertDiv.dataset.animationOut = animationOut;

    const contentHTML = title
      ? `<div><h3 class="font-bold">${escapeHtml(title)}</h3><div class="text-sm whitespace-pre-line">${escapeHtml(message)}</div></div>`
      : `<span class="whitespace-pre-line">${escapeHtml(message)}</span>`;

    const dismissButton = dismissible
      ? `<button type="button" class="btn btn-ghost btn-xs btn-square ml-auto text-white" aria-label="Dismiss">✕</button>`
      : '';

    alertDiv.innerHTML = `
      ${icon}
      ${contentHTML}
      ${dismissButton}
    `;

    if (dismissible) {
      const btn = alertDiv.querySelector('button');
      if (btn) {
        btn.addEventListener('click', () => dismiss(alertDiv));
      }
    }

    container.appendChild(alertDiv);

    if (duration > 0) {
      setTimeout(() => dismiss(alertDiv), duration);
    }

    return alertDiv;
  }

  function getAnimationName(position, direction) {
    const animations = {
      'top-right': direction === 'in' ? 'slideInRight' : 'slideOutRight',
      'top-left': direction === 'in' ? 'slideInLeft' : 'slideOutLeft',
      'bottom-right': direction === 'in' ? 'slideInRight' : 'slideOutRight',
      'bottom-left': direction === 'in' ? 'slideInLeft' : 'slideOutLeft',
      'top-center': direction === 'in' ? 'slideInDown' : 'slideOutUp'
    };
    return animations[position] || animations['bottom-right'];
  }

  function dismiss(alertElement) {
    if (!alertElement || !alertElement.parentNode) return;

    const animationOut = alertElement.dataset.animationOut || 'slideOutRight';
    alertElement.style.animation = `${animationOut} 0.3s ease-in`;
    alertElement.addEventListener('animationend', () => {
      if (alertElement.parentNode) {
        alertElement.parentNode.removeChild(alertElement);
      }
    });
  }

  function updateContainerPosition(position) {
    container.className = 'fixed flex flex-col gap-2 max-w-md';

    const positions = {
      'top-right': 'top-4 right-4',
      'top-left': 'top-4 left-4',
      'bottom-right': 'bottom-4 right-4',
      'bottom-left': 'bottom-4 left-4',
      'top-center': 'top-4 left-1/2 -translate-x-1/2'
    };

    container.className += ' ' + (positions[position] || positions['bottom-right']);

    // Keep z-index in style to ensure it's always applied
    container.style.zIndex = '99999';
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function success(message, options) {
    return show(Object.assign({message, type: 'success'}, options || {}));
  }

  function error(message, options) {
    return show(Object.assign({message, type: 'error', duration: 8000}, options || {}));
  }

  function warning(message, options) {
    return show(Object.assign({message, type: 'warning'}, options || {}));
  }

  function info(message, options) {
    return show(Object.assign({message, type: 'info'}, options || {}));
  }

  function clearAll() {
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }
  }

  window.AppAlert = {
    show,
    success,
    error,
    warning,
    info,
    dismiss,
    clearAll
  };

  const style = document.createElement('style');
  style.textContent = `
    @keyframes slideInRight {
      from { transform: translateX(100%); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideOutRight {
      from { transform: translateX(0); opacity: 1; }
      to { transform: translateX(100%); opacity: 0; }
    }
    @keyframes slideInLeft {
      from { transform: translateX(-100%); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideOutLeft {
      from { transform: translateX(0); opacity: 1; }
      to { transform: translateX(-100%); opacity: 0; }
    }
    @keyframes slideInDown {
      from { transform: translateY(-100%); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }
    @keyframes slideOutUp {
      from { transform: translateY(0); opacity: 1; }
      to { transform: translateY(-100%); opacity: 0; }
    }
  `;
  document.head.appendChild(style);

  // Django messages handed off by toasts.html.
  function processDjangoMessages() {
    const scriptEl = document.getElementById('django-messages-data');
    if (!scriptEl) return;

    try {
      const messages = JSON.parse(scriptEl.textContent);

      // `type` is Django's message.tags (level tag + any extra tags), so an
      // exact match is tried first, then the bare level, then info.
      const typeMap = {
        'debug': 'info',
        'info': 'info',
        'success': 'success',
        'warning': 'warning',
        'error': 'error'
      };

      messages.forEach((msg, index) => {
        const type = typeMap[msg.type] || typeMap[msg.level] || 'info';

        // Stagger messages slightly
        setTimeout(() => {
          show({
            message: msg.message,
            type: type,
            duration: type === 'error' ? 8000 : 5000,
            position: 'top-right'
          });
        }, index * 300);
      });

      scriptEl.remove();
    } catch (e) {
      console.error('Failed to process Django messages:', e);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', processDjangoMessages);
  } else {
    processDjangoMessages();
  }
})();
