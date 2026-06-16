// Mail rules: list view, open dialog, load/toggle/delete.
// Form-side methods (mode switching, payload building, save) live in
// mail_rules_form.js. State is shared on the root mailApp object
// (rulesAccount, rulesList, rulesEditing, rulesForm).
window.mailRulesMixin = function mailRulesMixin() {
  return {
    async showRules(account) {
      this.rulesAccount = account;
      this.rulesEditing = null;
      this.rulesList = [];
      this.rulesSearch = '';

      const tasks = [];
      if (!this.folders[account.uuid]) tasks.push(this.loadFolders(account.uuid));
      if (!this.labels[account.uuid]) tasks.push(this.fetchLabels(account.uuid));
      if (tasks.length) await Promise.all(tasks);

      await this._loadRules();
      document.getElementById('mail-rules-dialog').showModal();
    },

    // Open the rules dialog and pre-fill the form with a "from = sender"
    // condition derived from the given message. The account is resolved
    // from msg.account_id so the new rule is created on the right account.
    async openRuleFromMessage(msg) {
      if (!msg) return;
      const email = (msg.from_address && msg.from_address.email) || '';
      if (!email) return;
      const account = this.accounts.find(a => a.uuid === msg.account_id);
      if (!account) return;

      await this.showRules(account);
      this.rulesOpenForm();
      // Force simple mode: a single leaf condition + single action is
      // exactly what the simple form supports, so this stays editable
      // without dropping the user into raw JSON.
      this.rulesForm.mode = 'simple';
      this.rulesForm.simpleCondition = { field: 'from', op: 'equals', value: email };
      const senderName = (msg.from_address && msg.from_address.name) || email;
      this.rulesEditing.name = `From ${senderName}`;
    },

    async _loadRules() {
      if (!this.rulesAccount) return;
      const resp = await fetch(`/api/v1/mail/rules?account=${this.rulesAccount.uuid}`);
      if (resp.ok) this.rulesList = await resp.json();
    },

    rulesToggleCompact() {
      window.updateMailPref('rulesCompact', !this.mailPrefs.rulesCompact);
    },

    filteredRulesList() {
      const q = (this.rulesSearch || '').trim().toLowerCase();
      if (!q) return this.rulesList;
      return this.rulesList.filter(r => {
        if ((r.name || '').toLowerCase().includes(q)) return true;
        // Also match on action types so users can find "all delete rules"
        // by typing 'delete', etc.
        const types = (r.actions || []).map(a => (a.type || '').toLowerCase());
        return types.some(t => t.includes(q));
      });
    },

    rulesAccountLabels() {
      return (this.labels[this.rulesAccount?.uuid] || []);
    },

    rulesAccountFolders() {
      return (this.folders[this.rulesAccount?.uuid] || []);
    },

    rulesDescribe(rule) {
      const actions = (rule.actions || []).map(a => a.type).join(', ');
      const condCount = this._rulesCountLeaves(rule.conditions);
      return `${condCount} condition${condCount === 1 ? '' : 's'} -> ${actions || 'no actions'}`;
    },

    _rulesCountLeaves(node) {
      if (!node || typeof node !== 'object') return 0;
      if (node.type === 'all' || node.type === 'any') {
        return (node.conditions || []).reduce((n, c) => n + this._rulesCountLeaves(c), 0);
      }
      return 1;
    },

    async rulesToggle(rule, enabled) {
      const previous = rule.is_enabled;
      rule.is_enabled = enabled;  // optimistic
      const resp = await fetch(`/api/v1/mail/rules/${rule.uuid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ is_enabled: enabled }),
      });
      if (!resp.ok) {
        rule.is_enabled = previous;  // revert
      }
    },

    async rulesMove(rule, delta) {
      // Use array index, not rule.position: rules created before the
      // position-on-create fix all sit at position 0, and the server falls
      // back to created_at for display. The reorder endpoint renumbers
      // atomically from any starting state, so sending the target index
      // always converges.
      const idx = this.rulesList.findIndex(r => r.uuid === rule.uuid);
      const target = idx + delta;
      if (idx === -1 || target < 0 || target >= this.rulesList.length) return;
      const resp = await fetch(`/api/v1/mail/rules/${rule.uuid}/reorder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ position: target }),
      });
      if (resp.ok) await this._loadRules();
    },

    rulesCanMoveUp(rule) {
      return this.rulesList.findIndex(r => r.uuid === rule.uuid) > 0;
    },

    rulesCanMoveDown(rule) {
      const idx = this.rulesList.findIndex(r => r.uuid === rule.uuid);
      return idx !== -1 && idx < this.rulesList.length - 1;
    },

    rulesOpenApply(rule) {
      this.rulesApplying = rule;
      this.rulesApplyResult = null;
      this.rulesApplyBusy = false;
      // Default to the first folder of the account, if any.
      const folders = this.rulesAccountFolders();
      this.rulesApplyFolderId = folders.length ? folders[0].uuid : '';
    },

    rulesCancelApply() {
      this.rulesApplying = null;
      this.rulesApplyResult = null;
      this.rulesApplyFolderId = '';
    },

    async _rulesApplyRequest(dryRun) {
      if (!this.rulesApplying || !this.rulesApplyFolderId) return null;
      // Track *which* action is running so each button renders its own
      // spinner: 'preview' for the dry run, 'run' for the real apply.
      // Truthy in both cases, so the shared :disabled bindings still work;
      // reset to false (falsy) when done.
      this.rulesApplyBusy = dryRun ? 'preview' : 'run';
      try {
        const resp = await fetch(`/api/v1/mail/rules/${this.rulesApplying.uuid}/apply`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ folder_id: this.rulesApplyFolderId, dry_run: dryRun }),
        });
        if (!resp.ok) return null;
        return await resp.json();
      } finally {
        this.rulesApplyBusy = false;
      }
    },

    async rulesPreviewApply() {
      const result = await this._rulesApplyRequest(true);
      if (result) this.rulesApplyResult = { ...result, applied_run: false };
    },

    async rulesRunApply() {
      const ok = await AppDialog.confirm({
        title: 'Apply rule',
        message: `Apply rule "${this.rulesApplying.name}" to the selected folder now? Actions like move and delete cannot be undone.`,
        okLabel: 'Apply',
        okClass: 'btn-warning',
      });
      if (!ok) return;
      // Drop any stale dry-run preview the user may have requested earlier so
      // the panel doesn't show old "X of Y match" numbers while the real
      // apply runs (and in case the apply itself returns null).
      this.rulesApplyResult = null;
      const result = await this._rulesApplyRequest(false);
      if (!result) return;
      this.rulesApplyResult = { ...result, applied_run: true };
      // A real apply can move/delete/relabel messages, so the sidebar folder
      // counts and the open message list are now stale. Refresh both when
      // something actually changed.
      if (result.applied > 0) {
        const accountId = this.rulesAccount && this.rulesAccount.uuid;
        if (accountId) await this.loadFolders(accountId);
        await this.loadMessages();
      }
    },

    async rulesDelete(rule) {
      const ok = await AppDialog.confirm({
        title: 'Delete rule',
        message: `Delete rule "${rule.name}"? This cannot be undone.`,
        okLabel: 'Delete',
        okClass: 'btn-error',
      });
      if (!ok) return;
      const resp = await fetch(`/api/v1/mail/rules/${rule.uuid}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (resp.ok) this.rulesList = this.rulesList.filter(r => r.uuid !== rule.uuid);
    },
  };
};
