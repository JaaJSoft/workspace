// Recurrence handling for events: toggle, label rendering, and the
// "this/future/all" scope dialog used by save/delete on recurring events.
window.calendarRecurrenceMixin = function calendarRecurrenceMixin() {
  return {
    toggleRecurrence() {
      this.form.recurrence_frequency = this.form.recurrence_frequency ? null : 'weekly';
      if (!this.form.recurrence_frequency) {
        this.form.recurrence_interval = 1;
        this.form.recurrence_end = '';
      }
    },

    recurrenceLabel() {
      const raw = this._panelRaw;
      if (!raw) return '';
      const freq = raw.recurrence_frequency;
      const interval = raw.recurrence_interval || 1;
      if (!freq) return '';
      const units = { daily: ['day', 'days'], weekly: ['week', 'weeks'], monthly: ['month', 'months'], yearly: ['year', 'years'] };
      const [singular, plural] = units[freq] || ['', ''];
      return interval === 1 ? `Every ${singular}` : `Every ${interval} ${plural}`;
    },

    isRecurringEvent() {
      return this._panelRaw?.is_recurring || this._panelRaw?.master_event_id;
    },

    openScopeDialog(action) {
      return new Promise((resolve) => {
        this.scopeAction = action;
        this.scopeResolve = resolve;
        this.showScopeDialog = true;

      });
    },

    resolveScopeDialog(scope) {
      this.showScopeDialog = false;
      if (this.scopeResolve) {
        this.scopeResolve(scope);
        this.scopeResolve = null;
      }
    },
  };
};
