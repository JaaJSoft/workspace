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
      // Move the rule by `delta` positions in the global list (not the
      // filtered view). Backend renumbers atomically, so we reload after.
      const target = (rule.position || 0) + delta;
      if (target < 0) return;
      const resp = await fetch(`/api/v1/mail/rules/${rule.uuid}/reorder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ position: target }),
      });
      if (resp.ok) await this._loadRules();
    },

    rulesCanMoveUp(rule) {
      return (rule.position || 0) > 0;
    },

    rulesCanMoveDown(rule) {
      const maxPos = Math.max(...this.rulesList.map(r => r.position || 0));
      return (rule.position || 0) < maxPos;
    },

    async rulesDelete(rule) {
      if (!confirm(`Delete rule "${rule.name}"?`)) return;
      const resp = await fetch(`/api/v1/mail/rules/${rule.uuid}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (resp.ok) this.rulesList = this.rulesList.filter(r => r.uuid !== rule.uuid);
    },
  };
};
