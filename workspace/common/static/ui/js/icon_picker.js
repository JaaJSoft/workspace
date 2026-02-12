/**
 * Shared icon picker Alpine.js component.
 *
 * Usage:
 *   x-data="iconPicker(uuid, initialIcon, initialColor, apiEndpoint, onSaved)"
 *
 * @param {string} uuid        - The entity UUID
 * @param {string} initialIcon - Current icon name (default: 'folder')
 * @param {string} initialColor- Current color class (default: 'text-warning')
 * @param {string} apiEndpoint - PATCH endpoint, e.g. '/api/v1/files/' or '/api/v1/mail/folders/'
 * @param {function} onSaved   - Optional callback(icon, color) after successful save
 */
window.iconPicker = function iconPicker(uuid, initialIcon, initialColor, apiEndpoint, onSaved) {
  return {
    uuid: uuid,
    selectedIcon: initialIcon || 'folder',
    selectedColor: initialColor || 'text-warning',
    saving: false,
    saved: false,
    saveTimeout: null,

    init() {
      setTimeout(() => lucide.createIcons(), 100);
    },

    icons: [
      'folder', 'folder-open', 'briefcase', 'archive', 'box',
      'book', 'bookmark', 'heart', 'star', 'flag',
      'home', 'building', 'camera', 'music', 'video',
      'image', 'file-text', 'code', 'database', 'server',
      'cloud', 'download', 'upload', 'settings', 'wrench',
      'lock', 'unlock', 'shield', 'key', 'user',
      'users', 'mail', 'send', 'inbox', 'calendar',
      'clock', 'zap', 'rocket', 'gift', 'shopping-bag',
      'circle-dollar-sign', 'credit-card', 'gamepad-2', 'graduation-cap', 'trophy'
    ],

    colors: [
      { name: 'Yellow', class: 'text-warning' },
      { name: 'Blue', class: 'text-info' },
      { name: 'Green', class: 'text-success' },
      { name: 'Red', class: 'text-error' },
      { name: 'Purple', class: 'text-secondary' },
      { name: 'Pink', class: 'text-pink-500' },
      { name: 'Orange', class: 'text-orange-500' },
      { name: 'Cyan', class: 'text-cyan-500' },
      { name: 'Gray', class: 'text-base-content/60' },
    ],

    selectIcon(icon) {
      this.selectedIcon = icon;
      this.save();
      this.updatePreviewIcon();
    },

    selectColor(color) {
      this.selectedColor = color;
      this.save();
      this.updatePreviewIcon();
    },

    updatePreviewIcon() {
      const container = this.$refs.previewIcon;
      if (!container) return;

      while (container.firstChild) {
        container.removeChild(container.firstChild);
      }

      const icon = document.createElement('i');
      icon.setAttribute('data-lucide', this.selectedIcon);
      icon.className = 'w-8 h-8 ' + this.selectedColor;
      container.appendChild(icon);

      this.$nextTick(() => {
        lucide.createIcons({ nodes: container.querySelectorAll('[data-lucide]') });
      });
    },

    async save() {
      if (this.saveTimeout) clearTimeout(this.saveTimeout);
      this.saved = false;

      this.saveTimeout = setTimeout(async () => {
        this.saving = true;
        try {
          const response = await fetch(`${apiEndpoint}${this.uuid}`, {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': this.getCsrfToken()
            },
            body: JSON.stringify({
              icon: this.selectedIcon,
              color: this.selectedColor
            })
          });

          if (response.ok) {
            this.saved = true;
            if (typeof onSaved === 'function') {
              onSaved(this.selectedIcon, this.selectedColor);
            }
            setTimeout(() => { this.saved = false; }, 2000);
          }
        } catch (error) {
          console.error('Failed to save icon:', error);
        } finally {
          this.saving = false;
        }
      }, 300);
    },

    getCsrfToken() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
             document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1];
    }
  };
};
