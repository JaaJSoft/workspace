function rulesApp(accountId, labels, folders) {
  return {
    accountId,
    labels: labels || [],
    folders: folders || [],
    rules: [],
    editing: null,
    formMode: 'simple',
    simpleCondition: { field: 'from', op: 'contains', value: '' },
    simpleAction: { type: 'mark_read' },
    advancedConditionsText: '',
    advancedActionsText: '',
    advancedError: '',
    conditionFields: [
      { value: 'from', label: 'From' },
      { value: 'to', label: 'To' },
      { value: 'cc', label: 'Cc' },
      { value: 'recipient', label: 'To or Cc' },
      { value: 'subject', label: 'Subject' },
      { value: 'body', label: 'Body' },
      { value: 'folder', label: 'Folder' },
      { value: 'has_attachments', label: 'Has attachments' },
      { value: 'is_starred', label: 'Is starred' },
      { value: 'date', label: 'Date' },
    ],
    textFields: ['from', 'to', 'cc', 'recipient', 'subject', 'body', 'folder'],
    boolFields: ['has_attachments', 'is_starred'],
    dateFields: ['date'],

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

    accountLabels() {
      return this.labels.filter(l => l.account_id === this.accountId);
    },

    accountFolders() {
      return this.folders.filter(f => f.account_id === this.accountId);
    },

    compatibleOps(field) {
      if (this.textFields.includes(field)) {
        return ['contains', 'equals', 'starts_with', 'ends_with', 'matches_regex', 'in_list'];
      }
      if (this.boolFields.includes(field)) return ['is_true', 'is_false'];
      if (this.dateFields.includes(field)) return ['greater_than', 'less_than'];
      return [];
    },

    needsValue(op) {
      return !['is_true', 'is_false'].includes(op);
    },

    openRuleForm(rule) {
      if (rule) {
        this.editing = JSON.parse(JSON.stringify(rule));
        const cond = this.editing.conditions;
        if (cond && cond.field) {
          this.simpleCondition = { ...cond };
          this.formMode = 'simple';
        } else {
          this.formMode = 'advanced';
        }
        const act = (this.editing.actions || [])[0];
        if (act) this.simpleAction = { ...act };
        this.advancedConditionsText = JSON.stringify(this.editing.conditions, null, 2);
        this.advancedActionsText = JSON.stringify(this.editing.actions, null, 2);
      } else {
        this.editing = {
          name: '',
          is_enabled: true,
          stop_processing: false,
          conditions: {},
          actions: [],
        };
        this.simpleCondition = { field: 'from', op: 'contains', value: '' };
        this.simpleAction = { type: 'mark_read' };
        this.formMode = 'simple';
        this.advancedConditionsText = '';
        this.advancedActionsText = '';
      }
      this.advancedError = '';
    },

    cancelEdit() {
      this.editing = null;
    },

    setMode(mode) {
      if (this.formMode === 'simple' && mode === 'advanced') {
        this.advancedConditionsText = JSON.stringify(this._buildSimpleConditions(), null, 2);
        this.advancedActionsText = JSON.stringify(this._buildSimpleActions(), null, 2);
      }
      this.formMode = mode;
    },

    _buildSimpleConditions() {
      const c = { field: this.simpleCondition.field, op: this.simpleCondition.op };
      if (this.needsValue(c.op)) c.value = this.simpleCondition.value;
      return c;
    },

    _buildSimpleActions() {
      const a = { type: this.simpleAction.type };
      if (a.type === 'add_label' || a.type === 'remove_label') a.label_id = this.simpleAction.label_id;
      if (a.type === 'move_to_folder') a.folder_id = this.simpleAction.folder_id;
      return [a];
    },

    syncAdvancedConditions() {
      try {
        JSON.parse(this.advancedConditionsText);
        this.advancedError = '';
      } catch (e) {
        this.advancedError = 'Invalid conditions JSON';
      }
    },

    syncAdvancedActions() {
      try {
        JSON.parse(this.advancedActionsText);
        this.advancedError = '';
      } catch (e) {
        this.advancedError = 'Invalid actions JSON';
      }
    },

    _payload() {
      let conditions, actions;
      if (this.formMode === 'simple') {
        conditions = this._buildSimpleConditions();
        actions = this._buildSimpleActions();
      } else {
        try {
          conditions = JSON.parse(this.advancedConditionsText);
          actions = JSON.parse(this.advancedActionsText);
        } catch (e) {
          this.advancedError = 'Invalid JSON';
          return null;
        }
      }
      return {
        name: this.editing.name,
        is_enabled: this.editing.is_enabled,
        stop_processing: this.editing.stop_processing,
        conditions,
        actions,
      };
    },

    async saveRule() {
      const body = this._payload();
      if (!body) return;
      let resp;
      if (this.editing.uuid) {
        resp = await fetch(`/api/v1/mail/rules/${this.editing.uuid}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify(body),
        });
      } else {
        resp = await fetch(`/api/v1/mail/rules`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ account_id: this.accountId, ...body }),
        });
      }
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        this.advancedError = data.detail || JSON.stringify(data);
        return;
      }
      this.editing = null;
      await this.loadRules();
    },

    async toggleRule(rule, enabled) {
      const resp = await fetch(`/api/v1/mail/rules/${rule.uuid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ is_enabled: enabled }),
      });
      if (resp.ok) rule.is_enabled = enabled;
    },

    async deleteRule(rule) {
      if (!confirm(`Delete rule "${rule.name}"?`)) return;
      const resp = await fetch(`/api/v1/mail/rules/${rule.uuid}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (resp.ok) this.rules = this.rules.filter(r => r.uuid !== rule.uuid);
    },
  };
}
