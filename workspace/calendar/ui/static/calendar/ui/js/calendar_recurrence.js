// Recurrence handling for events: toggle, label rendering, and the
// "this/future/all" scope dialog used by save/delete on recurring events.
window.calendarRecurrenceMixin = function calendarRecurrenceMixin() {
  return {
    // True when the rule came from elsewhere (a phone, a feed) and the
    // picker cannot express it. The picker goes read-only rather than
    // silently rewriting the rule on the next save.
    isComplexRecurrence() {
      return !!this.form.recurrence_rule && !this.form.recurrence_simple;
    },

    toggleRecurrence() {
      if (this.isComplexRecurrence()) return;
      this.form.recurrence_frequency = this.form.recurrence_frequency ? null : 'weekly';
      if (!this.form.recurrence_frequency) {
        this.form.recurrence_interval = 1;
        this.form.recurrence_end = '';
      }
    },

    buildRecurrenceRule(tz) {
      if (this.isComplexRecurrence()) return this.form.recurrence_rule;
      if (!this.form.recurrence_frequency) return '';
      const parts = [`FREQ=${this.form.recurrence_frequency.toUpperCase()}`];
      const interval = this.form.recurrence_interval || 1;
      if (interval > 1) parts.push(`INTERVAL=${interval}`);
      if (this.form.recurrence_end) {
        // RFC 5545 3.3.10: UNTIL must match DTSTART's value type. An all-day
        // series has a DATE start, so a date-time UNTIL makes the rule invalid
        // for any client that checks - which CalDAV clients do.
        if (this.form.all_day) {
          parts.push(`UNTIL=${this.form.recurrence_end.replace(/-/g, '')}`);
        } else {
          const iso = window.wallClockToIso(this.form.recurrence_end + 'T23:59:59', tz);
          parts.push(`UNTIL=${new Date(iso).toISOString().replace(/[-:]|\.\d{3}/g, '')}`);
        }
      }
      return `RRULE:${parts.join(';')}`;
    },

    recurrenceLabel() {
      return this._panelRaw?.recurrence_summary || '';
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
