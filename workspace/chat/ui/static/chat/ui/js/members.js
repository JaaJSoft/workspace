// Conversation management actions: members, group avatar (with cropper),
// rename / description / clear / leave.
window.chatMembersMixin = function chatMembersMixin() {
  return {
    // ── Add member dialog state ──────────────────────────────
    addMemberSearchQuery: '',
    addMemberResults: [],
    addMemberSelected: [],
    addMemberLoading: false,
    addMemberShowDropdown: false,
    addMemberHighlight: -1,
    addMemberSaving: false,

    // ── Group avatar cropper state ───────────────────────────
    _cropper: null,
    _cropFile: null,
    _cropUploading: false,

    _linkCopied: false,

    // ── Conversation rename / description / clear / leave ───
    async renameConversation() {
      if (!this.activeConversation) return;
      const isBot = this.activeConversation.is_bot_conversation;
      if (!isBot && this.activeConversation.kind !== 'group') return;
      const current = this.activeConversation.title || '';
      const title = await AppDialog.prompt({
        title: isBot ? 'Rename conversation' : 'Rename group',
        message: isBot ? 'Enter a new name for this conversation:' : 'Enter a new name for this group:',
        value: current,
        placeholder: isBot ? 'Conversation name' : 'Group name',
        okLabel: 'Rename',
      });
      if (title === null) return;
      const trimmed = title.trim();
      if (!trimmed || trimmed === current) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
          body: JSON.stringify({ title: trimmed }),
        });
        if (resp.ok) {
          this.activeConversation.title = trimmed;
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) conv.title = trimmed;
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to rename conversation', e);
      }
    },

    async editDescription() {
      if (!this.activeConversation) return;
      const current = this.activeConversation.description || '';
      const description = await AppDialog.prompt({
        title: 'Edit description',
        message: 'Enter a description for this conversation:',
        value: current,
        placeholder: 'Add a description...',
        okLabel: 'Save',
        inputSize: 'textarea',
      });
      if (description === null) return;
      const trimmed = description.trim();
      if (trimmed === current) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
          body: JSON.stringify({ description: trimmed }),
        });
        if (resp.ok) {
          this.activeConversation.description = trimmed;
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) conv.description = trimmed;
        }
      } catch (e) {
        console.error('Failed to update description', e);
      }
    },


    async clearConversation() {
      if (!this.activeConversation) return;
      const ok = await AppDialog.confirm({
        title: 'Clear conversation',
        message: 'This will permanently delete all messages and media in this conversation. This cannot be undone.',
        okLabel: 'Clear all',
        okClass: 'btn-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/clear`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (resp.ok) {
          await this._refreshCurrentMessages();
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to clear conversation', e);
      }
    },

    async leaveConversation() {
      if (!this.activeConversation) return;
      const ok = await AppDialog.confirm({
        title: 'Leave conversation',
        message: 'Are you sure you want to leave this conversation?',
        okLabel: 'Leave',
        okClass: 'btn-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (resp.ok || resp.status === 204) {
          this.conversations = this.conversations.filter(c => c.uuid !== this.activeConversation.uuid);
          this.activeConversation = null;
          this.showInfoPanel = false;
          history.pushState({}, '', '/chat');
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to leave conversation', e);
      }
    },

    // ── Member management (group only) ────────────────────────
    async addMembersToConversation() {
      if (!this.activeConversation || this.activeConversation.kind !== 'group') return;
      this.addMemberSelected = [];
      this.addMemberSearchQuery = '';
      this.addMemberResults = [];
      this.addMemberShowDropdown = false;
      this.addMemberHighlight = -1;
      this.$refs.addMemberDialog.showModal();
      this.$nextTick(() => {
        this.$refs.addMemberSearchInput?.focus();
      });
    },

    async searchUsersForAdd() {
      const q = (this.addMemberSearchQuery || '').trim();
      if (q.length < 2) {
        this.addMemberResults = [];
        this.addMemberShowDropdown = false;
        return;
      }
      this.addMemberLoading = true;
      try {
        const resp = await fetch(`/api/v1/users/search?q=${encodeURIComponent(q)}&limit=10`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          const data = await resp.json();
          const existingIds = new Set((this.activeConversation.members || []).map(m => m.user.id));
          const selectedIds = new Set(this.addMemberSelected.map(u => u.id));
          this.addMemberResults = (data.results || []).filter(
            u => u.id !== this.currentUserId && !existingIds.has(u.id) && !selectedIds.has(u.id)
          );
          this.addMemberHighlight = -1;
          this.addMemberShowDropdown = true;
        }
      } catch (e) {
        console.error('User search failed', e);
      }
      this.addMemberLoading = false;
    },

    handleAddMemberKeydown(e) {
      const results = this.addMemberResults;
      const dropdownOpen = this.addMemberShowDropdown && results.length > 0;

      if (e.key === 'ArrowDown' && dropdownOpen) {
        e.preventDefault();
        this.addMemberHighlight = (this.addMemberHighlight + 1) % results.length;
      } else if (e.key === 'ArrowUp' && dropdownOpen) {
        e.preventDefault();
        this.addMemberHighlight = this.addMemberHighlight <= 0 ? results.length - 1 : this.addMemberHighlight - 1;
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (dropdownOpen && this.addMemberHighlight >= 0 && this.addMemberHighlight < results.length) {
          this.selectAddMember(results[this.addMemberHighlight]);
          this.$refs.addMemberSearchInput?.focus();
        } else if (this.addMemberSelected.length > 0 && !this.addMemberSearchQuery?.trim()) {
          this.confirmAddMembers();
        }
      }
    },

    selectAddMember(user) {
      if (!this.addMemberSelected.find(u => u.id === user.id)) {
        this.addMemberSelected.push(user);
      }
      this.addMemberSearchQuery = '';
      this.addMemberResults = [];
      this.addMemberHighlight = -1;
      this.addMemberShowDropdown = false;
    },

    removeAddMember(userId) {
      this.addMemberSelected = this.addMemberSelected.filter(u => u.id !== userId);
    },

    async confirmAddMembers() {
      if (!this.addMemberSelected.length || !this.activeConversation) return;
      this.addMemberSaving = true;
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/members`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
          body: JSON.stringify({ user_ids: this.addMemberSelected.map(u => u.id) }),
        });
        if (resp.ok) {
          const updated = await resp.json();
          this.activeConversation.members = updated.members;
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) conv.members = updated.members;
          this.$refs.addMemberDialog.close();
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to add members', e);
      }
      this.addMemberSaving = false;
    },

    async removeMember(userId) {
      if (!this.activeConversation) return;
      const member = this.activeConversation.members?.find(m => m.user.id === userId);
      const name = member ? this.memberDisplayName(member) : 'this member';
      const ok = await AppDialog.confirm({
        title: 'Remove member',
        message: `Remove ${name} from this group?`,
        okLabel: 'Remove',
        okClass: 'btn-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/members/${userId}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (resp.ok || resp.status === 204) {
          this.activeConversation.members = this.activeConversation.members.filter(m => m.user.id !== userId);
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) conv.members = this.activeConversation.members;
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to remove member', e);
      }
    },

    // ── Group avatar (with cropper) ──────────────────────────
    uploadGroupAvatar(fileInput) {
      if (!this.activeConversation || this.activeConversation.kind !== 'group') return;
      const file = fileInput.files?.[0];
      if (!file) return;
      this._cropFile = file;

      const reader = new FileReader();
      reader.onload = (e) => {
        this.$refs.cropperImage.src = e.target.result;
        this.$refs.cropperDialog.showModal();

        this.$nextTick(() => {
          if (this._cropper) {
            this._cropper.destroy();
          }
          this._cropper = new Cropper(this.$refs.cropperImage, {
            aspectRatio: 1,
            viewMode: 1,
            movable: true,
            zoomable: true,
            rotatable: false,
            scalable: false,
            guides: true,
            center: true,
            highlight: false,
            background: true,
          });
        });
      };
      reader.readAsDataURL(file);
      fileInput.value = '';
    },

    async confirmAvatarCrop() {
      if (!this._cropper || !this._cropFile || !this.activeConversation) return;
      this._cropUploading = true;

      const data = this._cropper.getData(true);
      const formData = new FormData();
      formData.append('image', this._cropFile);
      formData.append('crop_x', data.x);
      formData.append('crop_y', data.y);
      formData.append('crop_w', data.width);
      formData.append('crop_h', data.height);

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/avatar`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
          body: formData,
        });
        if (resp.ok) {
          const bust = String(Date.now());
          this.activeConversation.has_avatar = true;
          this.activeConversation._avatar_bust = bust;
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) { conv.has_avatar = true; conv._avatar_bust = bust; }
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to upload group avatar', e);
      } finally {
        this._cropUploading = false;
        this.$refs.cropperDialog.close();
        if (this._cropper) {
          this._cropper.destroy();
          this._cropper = null;
        }
        this._cropFile = null;
      }
    },

    cancelAvatarCrop() {
      this.$refs.cropperDialog.close();
      if (this._cropper) {
        this._cropper.destroy();
        this._cropper = null;
      }
      this._cropFile = null;
    },

    async removeGroupAvatar() {
      if (!this.activeConversation || this.activeConversation.kind !== 'group') return;

      const ok = await AppDialog.confirm({
        title: 'Remove avatar',
        message: 'Remove the group avatar?',
        okLabel: 'Remove',
        okClass: 'btn-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/avatar`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (resp.ok || resp.status === 200) {
          this.activeConversation.has_avatar = false;
          this.activeConversation._avatar_bust = null;
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) { conv.has_avatar = false; conv._avatar_bust = null; }
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to remove group avatar', e);
      }
    },

    copyConversationLink(uuid) {
      const id = uuid || this.activeConversation?.uuid;
      if (!id) return;
      const url = `${window.location.origin}/chat/${id}`;
      navigator.clipboard.writeText(url).then(() => {
        this._linkCopied = true;
        setTimeout(() => { this._linkCopied = false; }, 2000);
      });
    },
  };
};
