// Dual-source attachment input shared by the chat composer, the mail compose
// dialog and the projects task panel: stages local uploads (with image/video
// previews) alongside workspace files picked via AppDialog.filePicker, and
// appends both to the consumer's FormData at submit time.
//
// Spread into the host component (`...attachmentInputMixin({...})`) and pair
// with the ui/partials/attachment_chips.html + attachment_menu.html partials.
// Immediate-mode consumers (the task panel) override attachmentsAdded() to
// flush staged files to the server as soon as they land; composers leave it
// as the no-op and submit the staged lists with the message.
window.attachmentInputMixin = function attachmentInputMixin(opts = {}) {
  const pickerMessage = opts.pickerMessage || 'Select files to attach.';

  return {
    pendingFiles: [],
    pendingPickedFiles: [],
    isDraggingOver: false,
    _dragCounter: 0,

    openFileDialog() {
      this.$refs.fileInput?.click();
    },

    handleFileSelect(e) {
      const files = e.target.files;
      if (files?.length) this.addFiles(files);
      e.target.value = '';
    },

    addFiles(fileList) {
      const existing = new Set(this.pendingFiles.map((f) => f.name + f.size));
      let added = false;
      for (const f of fileList) {
        if (existing.has(f.name + f.size)) continue;
        if (f.type.startsWith('image/') || f.type.startsWith('video/')) {
          f._preview = URL.createObjectURL(f);
        }
        this.pendingFiles.push(f);
        added = true;
      }
      if (added) this.attachmentsAdded();
    },

    removeFile(idx) {
      const file = this.pendingFiles[idx];
      if (file?._preview) URL.revokeObjectURL(file._preview);
      this.pendingFiles.splice(idx, 1);
    },

    removePickedFile(idx) {
      this.pendingPickedFiles.splice(idx, 1);
    },

    isImageFile(file) {
      return file.type?.startsWith('image/');
    },

    isVideoFile(file) {
      return file.type?.startsWith('video/');
    },

    async attachFromWorkspace() {
      const files = await AppDialog.filePicker({
        title: 'Attach from Workspace',
        message: pickerMessage,
        okLabel: 'Attach',
        okClass: 'btn-info',
        icon: 'hard-drive',
        iconClass: 'bg-info/10 text-info',
        multiple: true,
      });
      if (!files || files.length === 0) return;
      const existing = new Set(this.pendingPickedFiles.map((f) => f.uuid));
      let added = false;
      for (const f of files) {
        if (existing.has(f.uuid)) continue;
        this.pendingPickedFiles.push(f);
        added = true;
      }
      if (added) this.attachmentsAdded();
    },

    // Hook, not a lifecycle method: called after addFiles/attachFromWorkspace
    // stage something new. Override it in immediate-mode consumers.
    attachmentsAdded() {},

    hasPendingAttachments() {
      return this.pendingFiles.length > 0 || this.pendingPickedFiles.length > 0;
    },

    clearAttachments() {
      for (const f of this.pendingFiles) {
        if (f._preview) URL.revokeObjectURL(f._preview);
      }
      this.pendingFiles = [];
      this.pendingPickedFiles = [];
    },

    appendAttachmentsTo(formData, filesKey = 'files', uuidsKey = 'file_uuids') {
      for (const f of this.pendingFiles) formData.append(filesKey, f);
      for (const wf of this.pendingPickedFiles) formData.append(uuidsKey, wf.uuid);
    },

    handleDragEnter(e) {
      if (!e.dataTransfer?.types?.includes('Files')) return;
      this._dragCounter++;
      this.isDraggingOver = true;
    },

    handleDragOver(e) {
      e.dataTransfer.dropEffect = 'copy';
    },

    handleDragLeave() {
      this._dragCounter--;
      if (this._dragCounter <= 0) {
        this._dragCounter = 0;
        this.isDraggingOver = false;
      }
    },

    handleDrop(e) {
      this._dragCounter = 0;
      this.isDraggingOver = false;
      const files = e.dataTransfer?.files;
      if (files?.length) this.addFiles(files);
    },

    handlePaste(e) {
      const items = e.clipboardData?.items;
      if (!items) return;
      const files = [];
      for (const item of items) {
        if (item.kind === 'file') {
          const f = item.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length > 0) {
        e.preventDefault();
        this.addFiles(files);
      }
    },

    // "Save to Files" for an already-delivered attachment: prompt for a
    // destination folder, then POST {folder_id} to the module's endpoint.
    async promptSaveAttachmentToFiles(url) {
      const folder = await AppDialog.folderPicker({
        title: 'Save to Files',
        message: 'Choose a destination folder.',
        okLabel: 'Save',
        okClass: 'btn-warning',
        icon: 'folder-down',
        iconClass: 'bg-warning/10 text-warning',
      });
      if (!folder) return;

      const body = {};
      if (folder.uuid) body.folder_id = folder.uuid;
      try {
        const resp = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          credentials: 'same-origin',
          body: JSON.stringify(body),
        });
        if (resp.ok) {
          AppDialog.message({
            title: 'Saved',
            message: 'Attachment saved to Files.',
            icon: 'check-circle',
            iconClass: 'bg-success/10 text-success',
          });
        } else {
          const data = await resp.json().catch(() => ({}));
          AppDialog.error({ message: data.detail || 'Failed to save attachment.' });
        }
      } catch (e) {
        AppDialog.error({ message: 'Failed to save attachment.' });
      }
    },
  };
};
