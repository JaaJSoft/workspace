// Composer input behavior: keyboard shortcuts, emoji picker, mention
// autocomplete, file attachments (upload + paste + drag-drop), typing
// indicator, save-attachment-to-files action.
window.chatInputMixin = function chatInputMixin() {
  return {
    // ── Pending file uploads ─────────────────────────────────
    pendingFiles: [],
    pendingPickedFiles: [],
    isDraggingOver: false,
    _dragCounter: 0,

    // ── Typing indicator ─────────────────────────────────────
    typingUsers: {},
    _lastTypingSent: 0,
    _typingHideTimer: null,

    // ── Emoji picker ─────────────────────────────────────────
    emojiPickerVisible: false,
    emojiPickerMode: null,       // 'input' | 'reaction'
    emojiPickerTargetMsg: null,  // message UUID for reaction mode
    emojiPickerX: 0,
    emojiPickerY: 0,

    // ── Mention autocomplete ─────────────────────────────────
    mentionActive: false,
    mentionQuery: '',
    mentionResults: [],
    mentionHighlight: -1,
    mentionStartPos: -1,

    // ── Autoresize + emoji insert ────────────────────────────
    insertEmoji(emoji) {
      const ta = this.getMessageInput();
      if (!ta) {
        this.messageBody += emoji;
        return;
      }
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      this.messageBody = this.messageBody.slice(0, start) + emoji + this.messageBody.slice(end);
      this.$nextTick(() => {
        const pos = start + emoji.length;
        ta.setSelectionRange(pos, pos);
        ta.focus();
      });
    },

    openEmojiPicker(mode, event, msgUuid) {
      if (this.emojiPickerVisible && this.emojiPickerMode === mode && this.emojiPickerTargetMsg === msgUuid) {
        this.closeEmojiPicker();
        return;
      }

      this.emojiPickerMode = mode;
      this.emojiPickerTargetMsg = msgUuid || null;

      // Position relative to the trigger button
      const btn = event.currentTarget;
      const rect = btn.getBoundingClientRect();
      const pickerWidth = 320;
      const pickerHeight = 340;

      let x = rect.left;
      let y;

      if (mode === 'input') {
        // Open above the button
        y = rect.top - pickerHeight - 8;
      } else {
        // Open below the hover toolbar
        y = rect.bottom + 8;
      }

      // Keep within viewport
      if (x + pickerWidth > window.innerWidth) {
        x = window.innerWidth - pickerWidth - 8;
      }
      if (x < 8) x = 8;
      if (y < 8) {
        y = rect.bottom + 8;
      }
      if (y + pickerHeight > window.innerHeight) {
        y = rect.top - pickerHeight - 8;
      }

      this.emojiPickerX = x;
      this.emojiPickerY = y;
      this.emojiPickerVisible = true;
    },

    closeEmojiPicker() {
      this.emojiPickerVisible = false;
      this.emojiPickerMode = null;
      this.emojiPickerTargetMsg = null;
    },

    // ── Input keyboard shortcuts ─────────────────────────────
    handleInputKeydown(e) {
      const ta = this.getMessageInput();

      // ── Mention autocomplete navigation ──
      // Only intercept nav and selection keys when there are results to act on.
      // Without this guard, ArrowDown/Up would compute (n+1) % 0 -> NaN, and
      // Enter/Tab would be swallowed even though there's nothing to insert
      // (so a regular Enter wouldn't send the message).
      if (this.mentionActive && this.mentionResults.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          this.mentionHighlight = (this.mentionHighlight + 1) % this.mentionResults.length;
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          this.mentionHighlight = this.mentionHighlight <= 0
            ? this.mentionResults.length - 1
            : this.mentionHighlight - 1;
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          if (this.mentionHighlight >= 0 && this.mentionHighlight < this.mentionResults.length) {
            this.insertMention(this.mentionResults[this.mentionHighlight]);
          }
          return;
        }
      }
      // Escape always dismisses the dropdown if it's open, even when empty.
      if (this.mentionActive && e.key === 'Escape') {
        e.preventDefault();
        this.closeMentionDropdown();
        return;
      }

      // Enter (without shift) → send / save edit
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (this.botTyping && this.isBotConversation(this.activeConversation)) return;
        this.sendOrEdit();
        return;
      }

      // Escape → close emoji picker first
      if (e.key === 'Escape' && this.emojiPickerVisible) {
        this.closeEmojiPicker();
        return;
      }

      // Escape → cancel reply, cancel edit, or blur
      if (e.key === 'Escape') {
        if (this.replyingTo) {
          this.cancelReply();
        } else if (this.editingMessageUuid) {
          this.cancelEdit();
        } else {
          ta?.blur();
        }
        return;
      }

      // Arrow Up when input is empty → edit last own message
      if (e.key === 'ArrowUp' && !this.messageBody) {
        this.editLastOwnMessage();
        return;
      }

      const isMod = e.ctrlKey || e.metaKey;

      // Ctrl/Cmd+B → bold
      if (isMod && e.key === 'b') {
        e.preventDefault();
        this.wrapSelection('**');
        return;
      }

      // Ctrl/Cmd+I → italic
      if (isMod && e.key === 'i') {
        e.preventDefault();
        this.wrapSelection('*');
        return;
      }

      // Ctrl/Cmd+E → inline code
      if (isMod && e.key === 'e') {
        e.preventDefault();
        this.wrapSelection('`');
        return;
      }

      // Ctrl/Cmd+Shift+X → strikethrough
      if (isMod && e.shiftKey && e.key === 'X') {
        e.preventDefault();
        this.wrapSelection('~~');
        return;
      }
    },

    wrapSelection(marker) {
      const ta = this.getMessageInput();
      if (!ta) return;
      ta.focus();

      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const text = this.messageBody;
      const selected = text.slice(start, end);

      if (selected) {
        // Wrap selected text
        const wrapped = marker + selected + marker;
        this.messageBody = text.slice(0, start) + wrapped + text.slice(end);
        this.$nextTick(() => {
          ta.setSelectionRange(start + marker.length, end + marker.length);
          ta.focus();
        });
      } else {
        // Insert empty markers with cursor between them
        this.messageBody = text.slice(0, start) + marker + marker + text.slice(end);
        this.$nextTick(() => {
          const pos = start + marker.length;
          ta.setSelectionRange(pos, pos);
          ta.focus();
        });
      }
    },

    insertLink() {
      const ta = this.getMessageInput();
      if (!ta) return;
      ta.focus();

      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const text = this.messageBody;
      const selected = text.slice(start, end);

      if (selected) {
        // Use selected text as the link text
        const link = `[${selected}](url)`;
        this.messageBody = text.slice(0, start) + link + text.slice(end);
        this.$nextTick(() => {
          // Select "url" for quick replacement
          const urlStart = start + selected.length + 3; // [text](
          const urlEnd = urlStart + 3; // url
          ta.setSelectionRange(urlStart, urlEnd);
          ta.focus();
        });
      } else {
        // Insert template and select "text"
        const link = '[text](url)';
        this.messageBody = text.slice(0, start) + link + text.slice(end);
        this.$nextTick(() => {
          // Select "text" for quick replacement
          ta.setSelectionRange(start + 1, start + 5);
          ta.focus();
        });
      }
    },

    // ── Mention autocomplete ─────────────────────────────────
    handleMentionInput() {
      const ta = this.getMessageInput();
      if (!ta) return;
      const pos = ta.selectionStart;
      const text = ta.value.substring(0, pos);

      // Find the last '@' that starts a mention (preceded by start-of-string or whitespace)
      const match = text.match(/(?:^|\s)@(\w*)$/);
      if (match) {
        this.mentionActive = true;
        this.mentionQuery = match[1].toLowerCase();
        this.mentionStartPos = pos - match[1].length - 1; // position of '@'
        this.filterMentionResults();
      } else {
        this.closeMentionDropdown();
      }
    },

    filterMentionResults() {
      if (!this.activeConversation?.members) {
        this.mentionResults = [];
        return;
      }
      const q = this.mentionQuery;
      let results = [];

      // Add @everyone option for group conversations
      if (this.activeConversation.kind === 'group') {
        if (!q || 'everyone'.startsWith(q)) {
          results.push({ username: 'everyone', first_name: 'Notify', last_name: 'everyone', id: null });
        }
      }

      // Filter conversation members (exclude self)
      for (const m of this.activeConversation.members) {
        if (m.user.id === this.currentUserId) continue;
        const u = m.user;
        const searchStr = `${u.username} ${u.first_name || ''} ${u.last_name || ''}`.toLowerCase();
        if (!q || searchStr.includes(q)) {
          results.push({ username: u.username, first_name: u.first_name, last_name: u.last_name, id: u.id });
        }
      }

      this.mentionResults = results.slice(0, 8);
      this.mentionHighlight = results.length > 0 ? 0 : -1;
    },

    insertMention(user) {
      const ta = this.getMessageInput();
      if (!ta) return;
      const before = ta.value.substring(0, this.mentionStartPos);
      const after = ta.value.substring(ta.selectionStart);
      const mention = `@${user.username} `;
      this.messageBody = before + mention + after;
      this.closeMentionDropdown();
      this.$nextTick(() => {
        const newPos = before.length + mention.length;
        ta.setSelectionRange(newPos, newPos);
        ta.focus();
      });
    },

    closeMentionDropdown() {
      this.mentionActive = false;
      this.mentionQuery = '';
      this.mentionResults = [];
      this.mentionHighlight = -1;
      this.mentionStartPos = -1;
    },

    // ── File upload (composer) ────────────────────────────────
    openFileDialog() {
      this.$refs.fileInput?.click();
    },

    handleFileSelect(e) {
      const files = e.target.files;
      if (files?.length) this.addFiles(files);
      e.target.value = '';
    },

    addFiles(fileList) {
      const existing = new Set(this.pendingFiles.map(f => f.name + f.size));
      for (const f of fileList) {
        if (existing.has(f.name + f.size)) continue;
        // Generate preview URL for images and videos
        if (f.type.startsWith('image/') || f.type.startsWith('video/')) {
          f._preview = URL.createObjectURL(f);
        }
        this.pendingFiles.push(f);
      }
    },

    removeFile(idx) {
      const file = this.pendingFiles[idx];
      if (file?._preview) URL.revokeObjectURL(file._preview);
      this.pendingFiles.splice(idx, 1);
    },

    formatFileSize(bytes) {
      if (!bytes) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB'];
      let val = bytes;
      for (const unit of units) {
        if (val < 1024) return unit === 'B' ? `${val} B` : `${val.toFixed(1)} ${unit}`;
        val /= 1024;
      }
      return `${val.toFixed(1)} TB`;
    },

    isImageFile(file) {
      return file.type?.startsWith('image/');
    },

    isVideoFile(file) {
      return file.type?.startsWith('video/');
    },

    handleDragEnter(e) {
      if (!e.dataTransfer?.types?.includes('Files')) return;
      this._dragCounter++;
      this.isDraggingOver = true;
    },

    handleDragOver(e) {
      e.dataTransfer.dropEffect = 'copy';
    },

    handleDragLeave(e) {
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

    async saveAttachmentToFiles(attachmentUuid) {
      const folder = await AppDialog.folderPicker({
        title: 'Save to Files',
        message: 'Choose a destination folder.',
        okLabel: 'Save',
        okClass: 'btn-warning',
        icon: 'folder-down',
        iconClass: 'bg-warning/10 text-warning',
      });
      if (!folder) return;

      try {
        const body = {};
        if (folder.uuid) body.folder_id = folder.uuid;
        const resp = await fetch(`/api/v1/chat/attachments/${attachmentUuid}/save-to-files`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          credentials: 'same-origin',
          body: JSON.stringify(body),
        });
        if (resp.ok) {
          AppDialog.message({ title: 'Saved', message: 'File saved to your Files.', icon: 'check-circle', iconClass: 'bg-success/10 text-success' });
        } else {
          const data = await resp.json().catch(() => ({}));
          AppDialog.error({ message: data.detail || 'Failed to save file.' });
        }
      } catch (e) {
        console.error('Failed to save attachment to files', e);
      }
    },

    async attachFromWorkspace() {
      const files = await AppDialog.filePicker({
        title: 'Attach from Workspace',
        message: 'Select files to attach to the message.',
        okLabel: 'Attach',
        okClass: 'btn-info',
        icon: 'hard-drive',
        iconClass: 'bg-info/10 text-info',
        multiple: true,
      });
      if (!files || files.length === 0) return;
      if (!this.pendingPickedFiles) this.pendingPickedFiles = [];
      const existing = new Set(this.pendingPickedFiles.map(f => f.uuid));
      for (const f of files) {
        if (!existing.has(f.uuid)) {
          this.pendingPickedFiles.push(f);
        }
      }
    },

    removePickedFile(idx) {
      if (this.pendingPickedFiles) {
        this.pendingPickedFiles.splice(idx, 1);
      }
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

    // ── Typing indicator ─────────────────────────────────────
    sendTypingSignal() {
      if (!this.activeConversation) return;
      const now = Date.now();
      if (now - this._lastTypingSent < 3000) return;
      this._lastTypingSent = now;
      fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/typing`, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCSRFToken() },
        credentials: 'same-origin',
      }).catch(() => {});
    },

    activeTypingUsers() {
      if (!this.activeConversation) return [];
      return this.typingUsers[this.activeConversation.uuid] || [];
    },

    typingText() {
      const users = this.activeTypingUsers();
      if (users.length === 0) return '';
      if (users.length === 1) return `${users[0].display_name} is typing`;
      if (users.length === 2) return `${users[0].display_name} and ${users[1].display_name} are typing`;
      return `${users[0].display_name} and ${users.length - 1} others are typing`;
    },
  };
};
