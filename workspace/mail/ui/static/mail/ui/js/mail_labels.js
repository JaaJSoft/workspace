// Mail labels: fetch, select, create / edit / delete (modal),
// label context menu, toggle on message via context menu, drag-drop targets.
window.mailLabelsMixin = function mailLabelsMixin() {
  return {
    // ----- Labels -----
    async fetchLabels(accountId) {
      try {
        const resp = await fetch(`/api/v1/mail/labels?account=${accountId}`, { credentials: 'same-origin' });
        if (resp.ok) {
          this.labels[accountId] = await resp.json();
        }
      } catch (e) {
        console.warn('Failed to fetch labels:', e);
      }
    },

    selectLabel(label) {
      this.unifiedInbox = false;
      this.selectedLabel = label;
      this.selectedFolder = null;
      this.selectedMessage = null;
      this.messageDetail = null;
      this._updateUrl(null, {push: this.isMobile()});
      this.currentPage = 1;
      this._closeDrawerOnMobile();
      this.loadMessages();
    },

    showLabelModal(accountId, label) {
      this.labelModal = {
        accountId: label ? label.account_id : accountId,
        uuid: label?.uuid || null,
        name: label?.name || '',
        color: label?.color || 'ghost',
        icon: label?.icon || '',
        saving: false,
        error: '',
      };
      const dlg = document.getElementById('mail-label-dialog');
      if (dlg) {
        dlg.showModal();
        this.$nextTick(() => {
          const input = dlg.querySelector('input[type="text"]');
          if (input) input.focus();
        });
      }
    },

    closeLabelModal() {
      document.getElementById('mail-label-dialog')?.close();
    },

    async saveLabelModal() {
      const m = this.labelModal;
      if (!m.name.trim() || !m.accountId) return;
      m.saving = true;
      m.error = '';
      try {
        const csrfToken = getCSRFToken();
        const isEdit = !!m.uuid;
        const url = isEdit ? `/api/v1/mail/labels/${m.uuid}` : '/api/v1/mail/labels';
        const resp = await fetch(url, {
          method: isEdit ? 'PATCH' : 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify(isEdit
            ? { name: m.name.trim(), color: m.color, icon: m.icon }
            : { account_id: m.accountId, name: m.name.trim(), color: m.color, icon: m.icon }
          ),
        });
        if (resp.ok) {
          await this.fetchLabels(m.accountId);
          this.closeLabelModal();
        } else {
          const data = await resp.json().catch(() => ({}));
          m.error = data.name?.[0] || data.detail || 'Failed to save label.';
        }
      } catch (e) {
        m.error = 'Network error.';
      } finally {
        m.saving = false;
      }
    },

    async deleteLabelConfirm() {
      const m = this.labelModal;
      if (!m.uuid) return;
      if (!confirm(`Delete label "${m.name}"? Messages won't be deleted.`)) return;
      try {
        const csrfToken = getCSRFToken();
        const resp = await fetch(`/api/v1/mail/labels/${m.uuid}`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrfToken },
        });
        if (resp.ok || resp.status === 204) {
          if (this.selectedLabel?.uuid === m.uuid) {
            this.selectedLabel = null;
          }
          await this.fetchLabels(m.accountId);
          this.closeLabelModal();
        }
      } catch (e) {
        console.warn('Delete label failed:', e);
      }
    },

    openLabelContextMenu(event, label) {
      event.preventDefault();
      const menu = document.getElementById('label-context-menu');
      if (!menu) return;

      this.labelCtx.label = label;
      this.labelCtx.open = true;

      this.$nextTick(() => {
        const rect = menu.getBoundingClientRect();
        let x = event.clientX;
        let y = event.clientY;
        if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 10;
        if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 10;
        this.labelCtx.x = x;
        this.labelCtx.y = y;

      });
    },

    labelCtxAction(action) {
      const label = this.labelCtx.label;
      this.labelCtx.open = false;
      if (!label) return;

      if (action === 'edit') {
        this.showLabelModal(null, label);
      } else if (action === 'delete') {
        this.showLabelModal(null, label);
        this.$nextTick(() => this.deleteLabelConfirm());
      }
    },

    // ----- Toggle label on a message (context menu) -----
    async toggleMsgLabel(label) {
      const ids = this._getMsgCtxTargetIds();
      if (!ids.length) return;
      const hasLabel = this._msgCtxHasLabel(label.uuid);
      const adding = !hasLabel;

      // Optimistic UI update
      for (const msgId of ids) {
        const msg = this.messages.find(m => m.uuid === msgId);
        if (!msg) continue;
        if (!msg.labels) msg.labels = [];
        if (adding) {
          if (!msg.labels.some(l => l.uuid === label.uuid)) {
            msg.labels.push({ uuid: label.uuid, name: label.name, color: label.color, icon: label.icon });
            if (!msg.is_read) label.unread_count = (label.unread_count || 0) + 1;
          }
        } else {
          msg.labels = msg.labels.filter(l => l.uuid !== label.uuid);
          if (!msg.is_read) label.unread_count = Math.max(0, (label.unread_count || 0) - 1);
        }
      }

      const method = adding ? 'POST' : 'DELETE';
      try {
        await Promise.all(ids.map(msgId =>
          this._fetch(`/api/v1/mail/messages/${msgId}/labels`, {
            method,
            body: { label_ids: [label.uuid] },
          })
        ));
      } catch (e) {
        console.warn('Toggle label failed:', e);
      }
    },

    // ----- Drag & drop (labels) -----
    onLabelDragOver(event, label) {
      event.dataTransfer.dropEffect = 'copy';
      this.dragOverLabel = label;
    },

    onLabelDragLeave(event, label) {
      if (this.dragOverLabel?.uuid === label.uuid) {
        this.dragOverLabel = null;
      }
    },

    async onLabelDrop(event, label) {
      this.dragOverLabel = null;
      try {
        const raw = event.dataTransfer.getData('text/plain');
        const ids = JSON.parse(raw);
        if (!Array.isArray(ids) || !ids.length) return;
        // Optimistic UI update
        for (const msgId of ids) {
          const msg = this.messages.find(m => m.uuid === msgId);
          if (!msg) continue;
          if (!msg.labels) msg.labels = [];
          if (!msg.labels.some(l => l.uuid === label.uuid)) {
            msg.labels.push({ uuid: label.uuid, name: label.name, color: label.color, icon: label.icon });
            if (!msg.is_read) label.unread_count = (label.unread_count || 0) + 1;
          }
        }
        await Promise.all(ids.map(msgId =>
          this._fetch(`/api/v1/mail/messages/${msgId}/labels`, {
            method: 'POST',
            body: { label_ids: [label.uuid] },
          })
        ));
      } catch (e) {
        console.warn('Label drop failed:', e);
      }
      this._draggingMsgIds = null;
    },
  };
};
