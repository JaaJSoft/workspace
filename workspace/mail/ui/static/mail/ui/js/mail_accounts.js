// Mail accounts: add / edit / remove, OAuth, sync, test, autodiscover,
// account context menu actions.
window.mailAccountsMixin = function mailAccountsMixin() {
  return {
    // ----- Accounts -----
    showAddAccount() {
      this.newAccount = _defaultNewAccount();
      this.accountError = '';
      document.getElementById('mail-add-account-dialog').showModal();
    },

    async autodiscoverSettings() {
      const email = (this.newAccount.email || '').trim();
      if (!email) return;

      this.autoDiscovering = true;
      this.accountError = '';

      try {
        const res = await this._fetch('/api/v1/mail/autodiscover', {
          method: 'POST',
          body: { email },
        });

        if (res.ok) {
          const data = await res.json();
          this.newAccount.imap_host = data.imap_host;
          this.newAccount.imap_port = data.imap_port;
          this.newAccount.imap_use_ssl = data.imap_use_ssl;
          this.newAccount.smtp_host = data.smtp_host;
          this.newAccount.smtp_port = data.smtp_port;
          this.newAccount.smtp_use_tls = data.smtp_use_tls;
          if (!this.newAccount.username) {
            this.newAccount.username = email;
          }
        } else {
          this.accountError = 'Could not auto-detect settings for this email. Please fill in manually.';
        }
      } catch (e) {
        this.accountError = 'Auto-detection failed. Please fill in settings manually.';
      }

      this.autoDiscovering = false;

    },

    closeAddAccount() {
      document.getElementById('mail-add-account-dialog').close();
    },

    startOAuth(provider) {
      const w = 600, h = 700;
      const left = (screen.width - w) / 2;
      const top = (screen.height - h) / 2;
      window.open(
        `/api/v1/mail/oauth2/authorize?provider=${provider}`,
        'oauth2',
        `width=${w},height=${h},left=${left},top=${top}`,
      );
    },

    async addAccount() {
      this.addingAccount = true;
      this.accountError = '';

      try {
        const res = await this._fetch('/api/v1/mail/accounts', {
          method: 'POST',
          body: this.newAccount,
        });

        if (res.ok) {
          const account = await res.json();
          this.accounts.push(account);
          this.expandedAccounts[account.uuid] = true;
          await this.loadFolders(account.uuid);
          await this.fetchLabels(account.uuid);
          this.closeAddAccount();

          // Trigger initial sync
          this.syncAccount(account.uuid);
        } else {
          const data = await res.json().catch(() => ({}));
          this.accountError = data.detail || JSON.stringify(data) || 'Failed to add account';
        }
      } catch (e) {
        this.accountError = 'Network error. Please check your connection and try again.';
      } finally {
        this.addingAccount = false;
      }
    },

    async syncAccount(uuid) {
      this.syncingAccounts[uuid] = true;
      try {
        await this._fetch(`/api/v1/mail/accounts/${uuid}/sync`, { method: 'POST' });
        await this.loadFolders(uuid);
        if (this.selectedFolder?.account_id === uuid) {
          await this.loadMessages();
        }
      } finally {
        this.syncingAccounts[uuid] = false;
      }

    },

    async testAccount(uuid) {
      const res = await this._fetch(`/api/v1/mail/accounts/${uuid}/test`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        const imapStatus = data.imap.success ? 'OK' : `Failed: ${data.imap.error}`;
        const smtpStatus = data.smtp.success ? 'OK' : `Failed: ${data.smtp.error}`;
        await AppDialog.message({
          title: 'Connection Test',
          message: `IMAP: ${imapStatus}\nSMTP: ${smtpStatus}`,
          icon: data.imap.success && data.smtp.success ? 'check-circle' : 'alert-triangle',
          iconClass: data.imap.success && data.smtp.success ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning',
        });
      }
    },

    async removeAccount(uuid) {
      const ok = await AppDialog.confirm({
        title: 'Remove Account',
        message: 'This will delete the account and all synced messages. Continue?',
        okLabel: 'Remove',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;

      let res;
      try {
        res = await this._fetch(`/api/v1/mail/accounts/${uuid}`, { method: 'DELETE' });
      } catch (e) {
        await AppDialog.message({
          title: 'Remove Account',
          message: 'Network error. Please check your connection and try again.',
          icon: 'alert-triangle',
          iconClass: 'bg-error/10 text-error',
        });
        return;
      }

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        await AppDialog.message({
          title: 'Remove Account',
          message: data.detail || 'Failed to remove account',
          icon: 'alert-triangle',
          iconClass: 'bg-error/10 text-error',
        });
        return;
      }

      this.accounts = this.accounts.filter(a => a.uuid !== uuid);
      delete this.folders[uuid];
      if (this.selectedFolder?.account_id === uuid) {
        this.selectedFolder = null;
        this.messages = [];
        this.selectedMessage = null;
        this.messageDetail = null;
        this._updateUrl(null);
      }
    },

    showEditAccount(account) {
      this.editAccount = {
        uuid: account.uuid,
        email: account.email,
        display_name: account.display_name || '',
        imap_host: account.imap_host,
        imap_port: account.imap_port,
        imap_use_ssl: account.imap_use_ssl,
        smtp_host: account.smtp_host,
        smtp_port: account.smtp_port,
        smtp_use_tls: account.smtp_use_tls,
        username: account.username,
        password: '',
      };
      this.editAccountError = '';
      document.getElementById('mail-edit-account-dialog').showModal();
    },

    closeEditAccount() {
      document.getElementById('mail-edit-account-dialog').close();
      this.editAccount = null;
    },

    async saveAccount() {
      this.savingAccount = true;
      this.editAccountError = '';

      const payload = { ...this.editAccount };
      const uuid = payload.uuid;
      delete payload.uuid;
      delete payload.email;
      if (!payload.password) delete payload.password;

      try {
        const res = await this._fetch(`/api/v1/mail/accounts/${uuid}`, {
          method: 'PATCH',
          body: payload,
        });

        if (res.ok) {
          const updated = await res.json();
          const idx = this.accounts.findIndex(a => a.uuid === uuid);
          if (idx !== -1) this.accounts[idx] = updated;
          this.closeEditAccount();
        } else {
          const data = await res.json().catch(() => ({}));
          this.editAccountError = data.detail || JSON.stringify(data) || 'Failed to save account';
        }
      } catch (e) {
        this.editAccountError = 'Network error. Please check your connection and try again.';
      } finally {
        this.savingAccount = false;
      }
    },

    showSignature(account) {
      this.signatureEdit = {
        uuid: account.uuid,
        email: account.email,
        text: account.signature || '',
        saving: false,
      };
      this.signatureEditError = '';
      document.getElementById('mail-signature-dialog').showModal();
    },

    async saveSignature() {
      if (this.signatureEdit.saving) return;
      this.signatureEdit.saving = true;
      this.signatureEditError = '';
      try {
        const res = await this._fetch(`/api/v1/mail/accounts/${this.signatureEdit.uuid}`, {
          method: 'PATCH',
          body: { signature: this.signatureEdit.text },
        });
        if (res.ok) {
          const acc = this.accounts.find(a => a.uuid === this.signatureEdit.uuid);
          if (acc) acc.signature = this.signatureEdit.text;
          document.getElementById('mail-signature-dialog').close();
        } else {
          const data = await res.json().catch(() => ({}));
          this.signatureEditError = data.detail || 'Failed to save signature';
        }
      } catch (e) {
        this.signatureEditError = 'Failed to save signature';
      } finally {
        this.signatureEdit.saving = false;
      }
    },

    // ----- Account context menu -----
    openAccountContextMenu(event, account) {
      event.preventDefault();
      event.stopPropagation();
      const menu = document.getElementById('account-context-menu');
      if (!menu) return;

      this.accountCtx.account = account;
      this.accountCtx.open = true;

      this.$nextTick(() => {
        const rect = menu.getBoundingClientRect();
        let x = event.clientX;
        let y = event.clientY;
        if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 10;
        if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 10;
        this.accountCtx.x = x;
        this.accountCtx.y = y;

      });
    },

    accountCtxAction(action) {
      const account = this.accountCtx.account;
      this.accountCtx.open = false;
      if (!account) return;

      switch (action) {
        case 'sync':
          this.syncAccount(account.uuid);
          break;
        case 'edit':
          this.showEditAccount(account);
          break;
        case 'signature':
          this.showSignature(account);
          break;
        case 'test':
          this.testAccount(account.uuid);
          break;
        case 'hidden_folders':
          this._showHiddenFolders(account);
          break;
        case 'filters_rules':
          this.showRules(account);
          break;
        case 'remove':
          this.removeAccount(account.uuid);
          break;
      }
    },
  };
};
