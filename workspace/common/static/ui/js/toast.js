/**
 * AppAlert — floating toast notifications, rendered through the shared
 * <inline-alert> element (its `toast` placement variant) into the fixed
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
 * The slide keyframes live in scripts/frontend/input.css; <inline-alert>
 * plays the exit one declared in data-animation-out when it is removed.
 *
 * Django messages: toasts.html embeds them as JSON in #django-messages-data;
 * they surface here as staggered top-right toasts on DOMContentLoaded.
 */
(function () {
  const container = document.getElementById('app-alerts-container');

  const POSITION_CLASSES = {
    'top-right': 'top-4 right-4',
    'top-left': 'top-4 left-4',
    'bottom-right': 'bottom-4 right-4',
    'bottom-left': 'bottom-4 left-4',
    'top-center': 'top-4 left-1/2 -translate-x-1/2'
  };

  const SLIDE_ANIMATIONS = {
    'top-right': { enter: 'slide-in-right', exit: 'slide-out-right' },
    'top-left': { enter: 'slide-in-left', exit: 'slide-out-left' },
    'bottom-right': { enter: 'slide-in-right', exit: 'slide-out-right' },
    'bottom-left': { enter: 'slide-in-left', exit: 'slide-out-left' },
    'top-center': { enter: 'slide-in-down', exit: 'slide-out-up' }
  };

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

    updateContainerPosition(position);

    const el = document.createElement('inline-alert');
    el.setAttribute('toast', '');
    el.setAttribute('type', type);
    el.setAttribute('message', message);
    if (title) el.setAttribute('title', title);
    if (dismissible) el.setAttribute('dismissible', '');

    const animations = SLIDE_ANIMATIONS[position] || SLIDE_ANIMATIONS['bottom-right'];
    el.style.animation = `${animations.enter} 0.3s ease-out`;
    el.dataset.animationOut = animations.exit;

    container.appendChild(el);

    if (duration > 0) {
      setTimeout(() => dismiss(el), duration);
    }

    return el;
  }

  function dismiss(alertElement) {
    if (!alertElement || !alertElement.parentNode) return;
    // <inline-alert> slides out through data-animation-out before detaching.
    alertElement.remove();
  }

  function updateContainerPosition(position) {
    const corner = POSITION_CLASSES[position] || POSITION_CLASSES['bottom-right'];
    container.className = 'fixed flex flex-col gap-2 max-w-md ' + corner;

    // Keep z-index in style to ensure it's always applied
    container.style.zIndex = '99999';
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
    // Detach without remove(), so no exit animations play.
    container.replaceChildren();
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
