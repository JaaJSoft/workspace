// Mail compose: open / close / send / draft, reply / replyAll / forward,
// recipient tag input + autocomplete, attachment files, draft persistence
// (server + localStorage).
window.mailComposeMixin = function mailComposeMixin() {
  return {
    // ----- Compose -----
    async showCompose(defaults = {}) {
      this.compose = { ..._defaultCompose(), ...defaults };
      // Normalize to/cc/bcc to arrays
      this.compose.to = _parseEmails(this.compose.to);
      this.compose.cc = _parseEmails(this.compose.cc);
      this.compose.bcc = _parseEmails(this.compose.bcc);
      if (this.accounts.length > 0 && !this.compose.account_id) {
        this.compose.account_id = this.accounts[0].uuid;
      }
      this.showCcBcc = !!(this.compose.cc.length || this.compose.bcc.length);

      // If no defaults (fresh compose), check localStorage for a saved draft
      if ((!defaults.to || (Array.isArray(defaults.to) && !defaults.to.length)) && !defaults.subject && !defaults.body) {
        const saved = this._getLocalStorageDraft();
        if (saved && ((saved.to && saved.to.length) || saved.subject || saved.body)) {
          const restore = await AppDialog.confirm({
            title: 'Restore draft',
            message: 'You have an unsaved draft. Would you like to restore it?',
            okLabel: 'Restore',
            cancelLabel: 'Discard',
            icon: 'file-edit',
            iconClass: 'bg-info/10 text-info',
          });
          if (restore) {
            this.compose.to = _parseEmails(saved.to);
            this.compose.cc = _parseEmails(saved.cc);
            this.compose.bcc = _parseEmails(saved.bcc);
            this.compose.subject = saved.subject || '';
            this.compose.body = saved.body || '';
            this.compose.draft_id = saved.draft_id || null;
            this.compose.is_reply = saved.is_reply || false;
            if (saved.account_id) this.compose.account_id = saved.account_id;
            if (saved.cc?.length || saved.bcc?.length) this.showCcBcc = true;
          } else {
            this._clearLocalStorageDraft();
          }
        }
      }

      document.getElementById('mail-compose-dialog').showModal();
    },

    async closeCompose() {
      if (this.compose._saveTimer) clearTimeout(this.compose._saveTimer);
      if (this._hasComposeContent()) {
        await this._saveDraft();
      } else {
        this._clearLocalStorageDraft();
      }
      document.getElementById('mail-compose-dialog').close();
      this.compose = _defaultCompose();
    },

    replyTo(msg) {
      const from = msg.from_address?.email || '';
      const subject = msg.subject?.startsWith('Re:') ? msg.subject : `Re: ${msg.subject || ''}`;
      this.showCompose({
        to: from,
        subject,
        body: `\n\n---\nOn ${this.formatFullDate(msg.date)}, ${msg.from_address?.name || from} wrote:\n> ${(msg.body_text || msg.snippet || '').replace(/\n/g, '\n> ')}`,
        account_id: msg.account_id,
        is_reply: true,
        reply_message_id: msg.uuid,
      });
    },

    replyAll(msg) {
      const from = msg.from_address?.email || '';
      const subject = msg.subject?.startsWith('Re:') ? msg.subject : `Re: ${msg.subject || ''}`;
      // Collect all "to" addresses except our own account
      const account = this.accounts.find(a => a.uuid === msg.account_id);
      const myEmail = account?.email?.toLowerCase() || '';
      const toAddrs = [from, ...(msg.to_addresses || []).map(a => a.email)]
        .filter(e => e && e.toLowerCase() !== myEmail);
      const ccAddrs = (msg.cc_addresses || []).map(a => a.email)
        .filter(e => e && e.toLowerCase() !== myEmail);
      this.showCompose({
        to: [...new Set(toAddrs)],
        cc: [...new Set(ccAddrs)],
        subject,
        body: `\n\n---\nOn ${this.formatFullDate(msg.date)}, ${msg.from_address?.name || from} wrote:\n> ${(msg.body_text || msg.snippet || '').replace(/\n/g, '\n> ')}`,
        account_id: msg.account_id,
        is_reply: true,
        reply_message_id: msg.uuid,
      });
    },

    forwardMessage(msg) {
      const subject = msg.subject?.startsWith('Fwd:') ? msg.subject : `Fwd: ${msg.subject || ''}`;
      this.showCompose({
        subject,
        body: `\n\n---\nForwarded message from ${msg.from_address?.name || msg.from_address?.email || 'Unknown'}:\n\n${msg.body_text || msg.snippet || ''}`,
        account_id: msg.account_id,
        is_reply: true,
      });
    },

    // ----- Tag input helpers -----
    _tagInput: { to: '', cc: '', bcc: '' },

    addTag(field, value) {
      const v = (value || '').trim();
      if (!v) return;
      if (!this.compose[field].includes(v)) {
        this.compose[field].push(v);
      }
      this._tagInput[field] = '';
      this._acClose();
    },

    removeTag(field, index) {
      this.compose[field].splice(index, 1);
    },

    handleTagKeydown(event, field) {
      const val = this._tagInput[field];

      // Autocomplete navigation
      if (this._acIsOpen(field)) {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          this._autocomplete.highlight = Math.min(this._autocomplete.highlight + 1, this._autocomplete.results.length - 1);
          return;
        }
        if (event.key === 'ArrowUp') {
          event.preventDefault();
          this._autocomplete.highlight = Math.max(this._autocomplete.highlight - 1, -1);
          return;
        }
        if (event.key === 'Enter' && this._autocomplete.highlight >= 0) {
          event.preventDefault();
          this._acSelect(this._autocomplete.results[this._autocomplete.highlight], field);
          return;
        }
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          this._acClose();
          return;
        }
      }

      if ((event.key === 'Enter' || event.key === ',' || event.key === ';' || event.key === 'Tab') && val.trim()) {
        event.preventDefault();
        this.addTag(field, val);
      } else if (event.key === 'Backspace' && !val && this.compose[field].length) {
        this.compose[field].pop();
      }
    },

    handleTagPaste(event, field) {
      event.preventDefault();
      const text = (event.clipboardData || window.clipboardData).getData('text');
      const emails = _parseEmails(text);
      for (const e of emails) this.addTag(field, e);
    },

    handleComposeFiles(event) {
      this.compose.attachments = [...this.compose.attachments, ...event.target.files];
    },

    async attachWorkspaceFiles() {
      const files = await AppDialog.filePicker({
        title: 'Attach from Workspace',
        message: 'Select files to attach to the email.',
        okLabel: 'Attach',
        okClass: 'btn-warning',
        icon: 'hard-drive',
        iconClass: 'bg-warning/10 text-warning',
        multiple: true,
      });
      if (!files || files.length === 0) return;
      const existing = new Set((this.compose.workspace_files || []).map(f => f.uuid));
      for (const f of files) {
        if (!existing.has(f.uuid)) {
          this.compose.workspace_files.push(f);
        }
      }
    },

    // ----- Autocomplete -----
    _acSearch(field) {
      if (this._autocomplete._timer) clearTimeout(this._autocomplete._timer);
      const q = (this._tagInput[field] || '').trim();
      if (q.length < 2) {
        this._acClose();
        return;
      }
      this._autocomplete.field = field;
      // Bump the request token so an in-flight fetch from a previous keystroke
      // can detect it has been superseded and skip its state writes. Without
      // this, a slow response for "ab" can clobber the results displayed for
      // "abcd" if it resolves after the newer request.
      const token = ++this._autocomplete._requestId;
      const isCurrent = () => token === this._autocomplete._requestId;
      this._autocomplete._timer = setTimeout(async () => {
        if (!isCurrent()) return;
        this._autocomplete.loading = true;
        try {
          let url = `/api/v1/mail/contacts/autocomplete?q=${encodeURIComponent(q)}`;
          if (this.compose.account_id) url += `&account_id=${this.compose.account_id}`;
          const res = await this._fetch(url);
          if (!isCurrent()) return;
          if (res.ok) {
            const data = await res.json();
            if (!isCurrent()) return;
            // Filter out emails already added in any field
            const existing = new Set([
              ...this.compose.to, ...this.compose.cc, ...this.compose.bcc,
            ].map(e => e.toLowerCase()));
            this._autocomplete.results = data.filter(c => !existing.has(c.email.toLowerCase()));
            this._autocomplete.highlight = -1;
            this._autocomplete.show = this._autocomplete.results.length > 0;
          }
        } catch (e) {
          if (!isCurrent()) return;
          this._autocomplete.show = false;
        }
        if (isCurrent()) this._autocomplete.loading = false;
      }, 300);
    },

    _acClose() {
      if (this._autocomplete._timer) clearTimeout(this._autocomplete._timer);
      // Mutate fields instead of reassigning the object so _requestId keeps
      // monotonically increasing - any in-flight fetch from before the close
      // will see a higher current id and bail out.
      this._autocomplete.results = [];
      this._autocomplete.highlight = -1;
      this._autocomplete.show = false;
      this._autocomplete.loading = false;
      this._autocomplete.field = null;
      this._autocomplete._timer = null;
      this._autocomplete._requestId = (this._autocomplete._requestId || 0) + 1;
    },

    _acSelect(contact, field) {
      const f = field || this._autocomplete.field;
      if (f) this.addTag(f, contact.email);
    },

    _acIsOpen(field) {
      return this._autocomplete.show && this._autocomplete.field === field;
    },

    async sendEmail() {
      // Commit any pending input
      if (this._tagInput.to) this.addTag('to', this._tagInput.to);
      if (this._tagInput.cc) this.addTag('cc', this._tagInput.cc);
      if (this._tagInput.bcc) this.addTag('bcc', this._tagInput.bcc);

      if (!this.compose.to.length) {
        this.compose.error = 'Please add at least one recipient';
        return;
      }

      this.compose.sending = true;
      this.compose.error = '';

      const formData = new FormData();
      formData.append('account_id', this.compose.account_id);
      formData.append('subject', this.compose.subject);
      formData.append('body_text', this.compose.body);
      const htmlBody = this.compose.body.replace(/\n/g, '<br>');
      formData.append('body_html', htmlBody);
      for (const addr of this.compose.to) formData.append('to', addr);
      for (const addr of this.compose.cc) formData.append('cc', addr);
      for (const addr of this.compose.bcc) formData.append('bcc', addr);
      for (const file of this.compose.attachments) formData.append('attachments', file);
      for (const wf of (this.compose.workspace_files || [])) formData.append('workspace_file_ids', wf.uuid);

      try {
        const res = await this._fetch('/api/v1/mail/messages/send', {
          method: 'POST',
          body: formData,
        });

        if (res.ok) {
          if (this.compose._saveTimer) clearTimeout(this.compose._saveTimer);
          const draftId = this.compose.draft_id;
          this._clearLocalStorageDraft();
          document.getElementById('mail-compose-dialog').close();
          this.compose = _defaultCompose();
          // Delete the draft after sending
          if (draftId) this._deleteDraft(draftId);
        } else {
          const data = await res.json().catch(() => ({}));
          this.compose.error = data.error || 'Failed to send email';
        }
      } catch (e) {
        // Network failure (offline, DNS, CORS, abort) - fetch rejects without
        // ever touching res.ok. Without this catch, sending=true would stick
        // and lock the dialog spinner indefinitely.
        this.compose.error = 'Failed to send email';
      } finally {
        this.compose.sending = false;
      }
    },

    // ----- Drafts -----
    _hasComposeContent() {
      return !!(this.compose.to.length || this.compose.subject || this.compose.body);
    },

    _scheduleDraftSave() {
      if (this.compose._saveTimer) clearTimeout(this.compose._saveTimer);
      if (!this._hasComposeContent()) return;
      this.compose._saveTimer = setTimeout(() => this._saveDraft(), 30000);
    },

    async _saveDraft() {
      if (this.compose.saving || this.compose.sending) return;
      if (!this._hasComposeContent()) return;

      this.compose.saving = true;

      const htmlBody = this.compose.body.replace(/\n/g, '<br>');

      const payload = {
        account_id: this.compose.account_id,
        to: this.compose.to,
        cc: this.compose.cc,
        bcc: this.compose.bcc,
        subject: this.compose.subject,
        body_text: this.compose.body,
        body_html: htmlBody,
      };
      if (this.compose.draft_id) payload.draft_id = this.compose.draft_id;

      try {
        const res = await this._fetch('/api/v1/mail/drafts', {
          method: 'POST',
          body: payload,
        });

        if (res.ok) {
          const data = await res.json();
          this.compose.draft_id = data.uuid;
          this.compose.last_saved = Date.now();
          // Refresh drafts folder counts
          this._refreshDraftsFolderCounts();
        }
      } catch (e) {
        // Silent fail — save to localStorage as fallback
      }

      // Always save to localStorage as fallback
      this._saveComposeToLocalStorage();
      this.compose.saving = false;
    },

    _saveComposeToLocalStorage() {
      try {
        const data = {
          account_id: this.compose.account_id,
          to: this.compose.to,
          cc: this.compose.cc,
          bcc: this.compose.bcc,
          subject: this.compose.subject,
          body: this.compose.body,
          draft_id: this.compose.draft_id,
          is_reply: this.compose.is_reply,
          saved_at: Date.now(),
        };
        localStorage.setItem('mail_compose_draft', JSON.stringify(data));
      } catch (e) {}
    },

    _clearLocalStorageDraft() {
      try { localStorage.removeItem('mail_compose_draft'); } catch (e) {}
    },

    _getLocalStorageDraft() {
      try {
        const raw = localStorage.getItem('mail_compose_draft');
        if (!raw) return null;
        const data = JSON.parse(raw);
        // Expire after 24h
        if (Date.now() - data.saved_at > 86400000) {
          this._clearLocalStorageDraft();
          return null;
        }
        return data;
      } catch (e) { return null; }
    },

    async _refreshDraftsFolderCounts() {
      // Refresh the Drafts folder of the account the draft was saved to, not
      // the first account that happens to have a Drafts folder. Otherwise a
      // user composing on a non-default account would see the badge update on
      // the wrong account and a stale badge on the right one.
      const composeUuid = this.compose?.account_id;
      if (!composeUuid) return;
      const flds = this.folders[composeUuid] || [];
      const draftsFolder = flds.find(f => f.folder_type === 'drafts');
      if (!draftsFolder) return;
      await this.loadFolders(composeUuid);
      if (this.selectedFolder?.uuid === draftsFolder.uuid) {
        await this.loadMessages();
      }
    },

    async _deleteDraft(draftId) {
      try {
        await this._fetch(`/api/v1/mail/drafts/${draftId}`, { method: 'DELETE' });
        this._refreshDraftsFolderCounts();
      } catch (e) {}
    },
  };
};
