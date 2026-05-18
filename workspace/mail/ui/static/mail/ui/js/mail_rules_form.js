// Mail rules form: simple/advanced mode, payload building, save.
// List-side methods (load, toggle, delete) live in mail_rules.js.
window.mailRulesFormMixin = function mailRulesFormMixin() {
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

    // When the user switches the field, the previously-selected op may not
    // be valid for the new field (e.g. 'contains' on 'is_starred'). Reset
    // to the first compatible op and clear the value if it's no longer
    // needed, otherwise the form sits in an inconsistent state until the
    // user manually picks an op.
    rulesOnFieldChange() {
      const ops = this.rulesCompatibleOps(this.rulesForm.simpleCondition.field);
      if (!ops.includes(this.rulesForm.simpleCondition.op)) {
        this.rulesForm.simpleCondition.op = ops[0] || '';
      }
      if (!this.rulesNeedsValue(this.rulesForm.simpleCondition.op)) {
        this.rulesForm.simpleCondition.value = '';
      }
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
  };
};
