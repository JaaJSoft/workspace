// Mail rules: list, create, edit, delete, toggle, dry-run trigger.
// State lives on the root mailApp object (rulesAccount, rulesList, rulesEditing,
// rulesForm). Labels and folders are reused from this.labels / this.folders
// (populated by the labels and folders mixins).
window.mailRulesMixin = function mailRulesMixin() {
  const TEXT_FIELDS = ['from', 'to', 'cc', 'recipient', 'subject', 'body', 'folder'];
  const BOOL_FIELDS = ['has_attachments', 'is_starred'];
  const DATE_FIELDS = ['date'];

  return {
    rulesConditionFields: [
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

    async showRules(account) {
      this.rulesAccount = account;
      this.rulesEditing = null;
      this.rulesList = [];

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

    rulesCompatibleOps(field) {
      if (TEXT_FIELDS.includes(field)) {
        return ['contains', 'equals', 'starts_with', 'ends_with', 'matches_regex', 'in_list'];
      }
      if (BOOL_FIELDS.includes(field)) return ['is_true', 'is_false'];
      if (DATE_FIELDS.includes(field)) return ['greater_than', 'less_than'];
      return [];
    },

    rulesNeedsValue(op) {
      return !['is_true', 'is_false'].includes(op);
    },

    rulesOpenForm(rule) {
      if (rule) {
        this.rulesEditing = JSON.parse(JSON.stringify(rule));
        const cond = this.rulesEditing.conditions;
        if (cond && cond.field) {
          this.rulesForm.mode = 'simple';
          this.rulesForm.simpleCondition = { ...cond };
        } else {
          this.rulesForm.mode = 'advanced';
        }
        const act = (this.rulesEditing.actions || [])[0];
        if (act) this.rulesForm.simpleAction = { ...act };
        this.rulesForm.advancedConditionsText = JSON.stringify(this.rulesEditing.conditions, null, 2);
        this.rulesForm.advancedActionsText = JSON.stringify(this.rulesEditing.actions, null, 2);
      } else {
        this.rulesEditing = {
          name: '',
          is_enabled: true,
          stop_processing: false,
          conditions: {},
          actions: [],
        };
        this.rulesForm.mode = 'simple';
        this.rulesForm.simpleCondition = { field: 'from', op: 'contains', value: '' };
        this.rulesForm.simpleAction = { type: 'mark_read' };
        this.rulesForm.advancedConditionsText = '';
        this.rulesForm.advancedActionsText = '';
      }
      this.rulesForm.error = '';
    },

    rulesCancelEdit() {
      this.rulesEditing = null;
      this.rulesForm.error = '';
    },

    rulesSetMode(mode) {
      if (this.rulesForm.mode === 'simple' && mode === 'advanced') {
        this.rulesForm.advancedConditionsText = JSON.stringify(this._rulesBuildSimpleConditions(), null, 2);
        this.rulesForm.advancedActionsText = JSON.stringify(this._rulesBuildSimpleActions(), null, 2);
      }
      this.rulesForm.mode = mode;
    },

    _rulesBuildSimpleConditions() {
      const c = { field: this.rulesForm.simpleCondition.field, op: this.rulesForm.simpleCondition.op };
      if (this.rulesNeedsValue(c.op)) c.value = this.rulesForm.simpleCondition.value;
      return c;
    },

    _rulesBuildSimpleActions() {
      const a = { type: this.rulesForm.simpleAction.type };
      if (a.type === 'add_label' || a.type === 'remove_label') a.label_id = this.rulesForm.simpleAction.label_id;
      if (a.type === 'move_to_folder') a.folder_id = this.rulesForm.simpleAction.folder_id;
      return [a];
    },

    rulesSyncAdvancedConditions() {
      try {
        JSON.parse(this.rulesForm.advancedConditionsText);
        this.rulesForm.error = '';
      } catch (e) {
        this.rulesForm.error = 'Invalid conditions JSON';
      }
    },

    rulesSyncAdvancedActions() {
      try {
        JSON.parse(this.rulesForm.advancedActionsText);
        this.rulesForm.error = '';
      } catch (e) {
        this.rulesForm.error = 'Invalid actions JSON';
      }
    },

    _rulesPayload() {
      let conditions, actions;
      if (this.rulesForm.mode === 'simple') {
        conditions = this._rulesBuildSimpleConditions();
        actions = this._rulesBuildSimpleActions();
      } else {
        try {
          conditions = JSON.parse(this.rulesForm.advancedConditionsText);
          actions = JSON.parse(this.rulesForm.advancedActionsText);
        } catch (e) {
          this.rulesForm.error = 'Invalid JSON';
          return null;
        }
      }
      return {
        name: this.rulesEditing.name,
        is_enabled: this.rulesEditing.is_enabled,
        stop_processing: this.rulesEditing.stop_processing,
        conditions,
        actions,
      };
    },

    async rulesSave() {
      const body = this._rulesPayload();
      if (!body) return;
      let resp;
      if (this.rulesEditing.uuid) {
        resp = await fetch(`/api/v1/mail/rules/${this.rulesEditing.uuid}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify(body),
        });
      } else {
        resp = await fetch('/api/v1/mail/rules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ account_id: this.rulesAccount.uuid, ...body }),
        });
      }
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        this.rulesForm.error = data.detail || JSON.stringify(data);
        return;
      }
      this.rulesEditing = null;
      await this._loadRules();
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
