// Promise-based replacements for window.alert / window.confirm / window.prompt.
// Uses the <dialog> partials defined in workspace/common/templates/ui/partials/dialogs.html.
const AppDialog = {
  _setIcon(iconEl, icon, iconClass) {
    while (iconEl.firstChild) iconEl.removeChild(iconEl.firstChild);
    if (icon) {
      const i = document.createElement('i');
      i.setAttribute('data-lucide', icon);
      i.className = 'w-5 h-5';
      iconEl.appendChild(i);
      iconEl.className = `flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center ${iconClass || 'bg-base-200 text-base-content'}`;
    } else {
      iconEl.className = 'hidden';
    }
  },

  confirm({ title = 'Confirm', message = 'Are you sure?', okLabel = 'OK', cancelLabel = 'Cancel', okClass = 'btn-primary', icon = '', iconClass = '' } = {}) {
    return new Promise((resolve) => {
      const dialog = document.getElementById('app-dialog-confirm');
      const iconEl = document.getElementById('app-dialog-confirm-icon');
      const titleEl = document.getElementById('app-dialog-confirm-title');
      const messageEl = document.getElementById('app-dialog-confirm-message');
      const okBtn = document.getElementById('app-dialog-confirm-ok');
      const cancelBtn = document.getElementById('app-dialog-confirm-cancel');

      this._setIcon(iconEl, icon, iconClass);
      titleEl.textContent = title;
      messageEl.textContent = message;
      okBtn.textContent = okLabel;
      cancelBtn.textContent = cancelLabel;
      okBtn.className = `btn btn-sm ${okClass}`;

      const cleanup = () => {
        okBtn.removeEventListener('click', onOk);
        dialog.removeEventListener('close', onClose);
        dialog.removeEventListener('keydown', onKeydown);
      };

      const onOk = () => {
        cleanup();
        dialog.close();
        resolve(true);
      };

      const onClose = () => {
        cleanup();
        resolve(false);
      };

      const onKeydown = (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          e.stopPropagation();
          onOk();
        }
      };

      okBtn.addEventListener('click', onOk);
      dialog.addEventListener('close', onClose);
      dialog.addEventListener('keydown', onKeydown);
      dialog.showModal();
      okBtn.focus();
    });
  },

  prompt({ title = 'Input', message = '', value = '', placeholder = '', okLabel = 'OK', cancelLabel = 'Cancel', okClass = 'btn-primary', icon = '', iconClass = '', inputSize = 'md' } = {}) {
    return new Promise((resolve) => {
      const dialog = document.getElementById('app-dialog-prompt');
      const iconEl = document.getElementById('app-dialog-prompt-icon');
      const titleEl = document.getElementById('app-dialog-prompt-title');
      const messageEl = document.getElementById('app-dialog-prompt-message');
      const input = document.getElementById('app-dialog-prompt-input');
      const textarea = document.getElementById('app-dialog-prompt-textarea');
      const okBtn = document.getElementById('app-dialog-prompt-ok');
      const cancelBtn = document.getElementById('app-dialog-prompt-cancel');

      this._setIcon(iconEl, icon, iconClass);
      titleEl.textContent = title;
      messageEl.textContent = message;
      messageEl.style.display = message ? '' : 'none';

      const useTextarea = inputSize === 'textarea';
      const activeInput = useTextarea ? textarea : input;
      const hiddenInput = useTextarea ? input : textarea;

      activeInput.classList.remove('hidden');
      hiddenInput.classList.add('hidden');

      activeInput.value = value;
      activeInput.placeholder = placeholder;

      if (!useTextarea) {
        input.className = `input input-bordered w-full mt-4 ${inputSize === 'sm' ? 'input-sm' : ''}`;
      }

      okBtn.textContent = okLabel;
      cancelBtn.textContent = cancelLabel;
      okBtn.className = `btn btn-sm ${okClass}`;

      const cleanup = () => {
        okBtn.removeEventListener('click', onOk);
        dialog.removeEventListener('close', onClose);
        activeInput.removeEventListener('keydown', onKeydown);
      };

      const onOk = () => {
        cleanup();
        const val = activeInput.value;
        dialog.close();
        resolve(val);
      };

      const onClose = () => {
        cleanup();
        resolve(null);
      };

      const onKeydown = (e) => {
        if (e.key === 'Enter' && !useTextarea) {
          e.preventDefault();
          e.stopPropagation();
          onOk();
        }
      };

      okBtn.addEventListener('click', onOk);
      dialog.addEventListener('close', onClose);
      activeInput.addEventListener('keydown', onKeydown);
      dialog.showModal();
      activeInput.focus();
      activeInput.select();
    });
  },

  message({ title = 'Message', message = 'Done.', okLabel = 'OK', icon = '', iconClass = '' } = {}) {
    return new Promise((resolve) => {
      const dialog = document.getElementById('app-dialog-message');
      const iconEl = document.getElementById('app-dialog-message-icon');
      const titleEl = document.getElementById('app-dialog-message-title');
      const messageEl = document.getElementById('app-dialog-message-message');
      const okBtn = document.getElementById('app-dialog-message-ok');

      this._setIcon(iconEl, icon, iconClass);
      titleEl.textContent = title;
      messageEl.textContent = message;
      okBtn.textContent = okLabel;

      const onClose = () => {
        dialog.removeEventListener('close', onClose);
        messageEl.textContent = '';
        resolve();
      };

      dialog.addEventListener('close', onClose);
      dialog.showModal();
    });
  },

  error({ title = 'Error', message = 'An error occurred.', okLabel = 'OK' } = {}) {
    return this.message({ title, message, okLabel, icon: 'circle-alert', iconClass: 'bg-error/10 text-error' });
  },

  select({ title = 'Select', message = '', options = [], value = '', okLabel = 'OK', cancelLabel = 'Cancel', okClass = 'btn-primary', icon = '', iconClass = '' } = {}) {
    return new Promise((resolve) => {
      const dialog = document.getElementById('app-dialog-select');
      const iconEl = document.getElementById('app-dialog-select-icon');
      const titleEl = document.getElementById('app-dialog-select-title');
      const messageEl = document.getElementById('app-dialog-select-message');
      const select = document.getElementById('app-dialog-select-input');
      const okBtn = document.getElementById('app-dialog-select-ok');
      const cancelBtn = document.getElementById('app-dialog-select-cancel');

      this._setIcon(iconEl, icon, iconClass);
      titleEl.textContent = title;
      messageEl.textContent = message;
      messageEl.style.display = message ? '' : 'none';

      while (select.firstChild) select.removeChild(select.firstChild);
      for (const opt of options) {
        const o = document.createElement('option');
        o.value = opt.value !== undefined ? opt.value : opt.label;
        o.textContent = opt.label || opt.value;
        if (o.value === value) o.selected = true;
        select.appendChild(o);
      }

      okBtn.textContent = okLabel;
      cancelBtn.textContent = cancelLabel;
      okBtn.className = `btn btn-sm ${okClass}`;

      const cleanup = () => {
        okBtn.removeEventListener('click', onOk);
        dialog.removeEventListener('close', onClose);
      };

      const onOk = () => {
        cleanup();
        const val = select.value;
        dialog.close();
        resolve(val);
      };

      const onClose = () => {
        cleanup();
        resolve(null);
      };

      okBtn.addEventListener('click', onOk);
      dialog.addEventListener('close', onClose);
      dialog.showModal();
      select.focus();
    });
  },

  folderPicker({ title, message, okLabel, cancelLabel, okClass, icon, iconClass } = {}) {
    return new Promise((resolve) => {
      window.dispatchEvent(new CustomEvent('folder-picker:open', {
        detail: {
          options: { title, message, okLabel, cancelLabel, okClass, icon, iconClass },
          resolve,
        },
      }));
    });
  },

  filePicker({ title, message, okLabel, cancelLabel, okClass, icon, iconClass, multiple } = {}) {
    return new Promise((resolve) => {
      window.dispatchEvent(new CustomEvent('file-picker:open', {
        detail: {
          options: { title, message, okLabel, cancelLabel, okClass, icon, iconClass, multiple },
          resolve,
        },
      }));
    });
  }
};
