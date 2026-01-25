window.textViewerMethods = function textViewerMethods() {
  return {
    handleKeydown(event) {
      if (!this.isEditing) return;

      // Ctrl+S / Cmd+S to save
      if ((event.ctrlKey || event.metaKey) && event.key === 's') {
        event.preventDefault();
        this.save();
      }

      // ESC to cancel
      if (event.key === 'Escape') {
        event.preventDefault();
        this.cancelEdit();
      }
    },

    startEdit() {
      this.isEditing = true;
    },

    async save() {
      if (this.saving) return;

      this.saving = true;

      try {
        const blob = new Blob([this.content], { type: 'text/plain' });
        const formData = new FormData();
        formData.append('content', blob, this.fileName || 'file.txt');

        const csrfToken = document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1];

        const response = await fetch(`/api/v1/files/${this.fileUuid}`, {
          method: 'PATCH',
          headers: { 'X-CSRFToken': csrfToken },
          body: formData
        });

        if (!response.ok) throw new Error('Failed to save');

        this.originalContent = this.content;
        this.isEditing = false;

        if (window.AppAlert) {
          window.AppAlert.success('File saved successfully');
        }
      } catch (error) {
        console.error('Failed to save file:', error);
        if (window.AppAlert) {
          window.AppAlert.error('Failed to save: ' + error.message);
        }
      } finally {
        this.saving = false;
      }
    },

    cancelEdit() {
      this.content = this.originalContent;
      this.isEditing = false;
    }
  };
};
