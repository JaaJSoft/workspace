function rulesApp(accountId, labels, folders) {
  return {
    accountId,
    labels: labels || [],
    folders: folders || [],
    rules: [],
    editing: null,

    async init() {
      await this.loadRules();
    },

    async loadRules() {
      const resp = await fetch(`/api/v1/mail/rules?account=${this.accountId}`);
      if (!resp.ok) return;
      this.rules = await resp.json();
    },

    describeRule(rule) {
      const actions = (rule.actions || []).map(a => a.type).join(', ');
      const condCount = this._countLeaves(rule.conditions);
      return `${condCount} condition${condCount === 1 ? '' : 's'} -> ${actions || 'no actions'}`;
    },

    _countLeaves(node) {
      if (!node || typeof node !== 'object') return 0;
      if (node.type === 'all' || node.type === 'any') {
        return (node.conditions || []).reduce((n, c) => n + this._countLeaves(c), 0);
      }
      return 1;
    },

    openRuleForm(rule) {
      // Stub - Task 21 will replace this with a proper form dialog.
      this.editing = rule
        ? JSON.parse(JSON.stringify(rule))
        : {
            name: '',
            is_enabled: true,
            stop_processing: false,
            conditions: { type: 'all', conditions: [{ field: 'from', op: 'contains', value: '' }] },
            actions: [{ type: 'mark_read' }],
          };
    },

    async toggleRule(rule, enabled) {
      const resp = await fetch(`/api/v1/mail/rules/${rule.uuid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ is_enabled: enabled }),
      });
      if (resp.ok) {
        rule.is_enabled = enabled;
      }
    },

    async deleteRule(rule) {
      if (!confirm(`Delete rule "${rule.name}"?`)) return;
      const resp = await fetch(`/api/v1/mail/rules/${rule.uuid}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (resp.ok) {
        this.rules = this.rules.filter(r => r.uuid !== rule.uuid);
      }
    },
  };
}
