// Calendar entities (the user's calendar lists): owned/subscribed/external
// CRUD + external ICS feed handling. Visibility toggles still live in
// calendar.js itself since they cross-cut all calendar types.
window.calendarCalendarsMixin = function calendarCalendarsMixin() {
  return {
    // --- Calendar CRUD ---
    createCalendar() {
      const defaultColor = this.calendarColors[this.ownedCalendars.length % this.calendarColors.length];
      this.calendarModalMode = 'create';
      this.calendarForm = { uuid: null, name: '', color: defaultColor };
      this.showCalendarModal = true;
      this.$nextTick(() => {
        const input = document.getElementById('calendar-form-name');
        if (input) { input.focus(); input.select(); }
      });
    },

    editCalendar(cal) {
      this.calendarModalMode = 'edit';
      this.calendarForm = { uuid: cal.uuid, name: cal.name, color: cal.color };
      this.showCalendarModal = true;
      this.$nextTick(() => {
        const input = document.getElementById('calendar-form-name');
        if (input) { input.focus(); input.select(); }
      });
    },

    async saveCalendar() {
      const { uuid, name, color } = this.calendarForm;
      if (!name.trim() || this.savingCalendar) return;

      this.savingCalendar = true;
      try {
        if (this.calendarModalMode === 'create') {
          const resp = await fetch('/api/v1/calendar/calendars', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
            body: JSON.stringify({ name: name.trim(), color }),
          });
          if (resp.ok) {
            const cal = await resp.json();
            this.ownedCalendars.push(cal);
            this.visibleCalendars[cal.uuid] = true;
            this._saveVisibility();
            this.showCalendarModal = false;
          }
        } else {
          const resp = await fetch(`/api/v1/calendar/calendars/${uuid}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
            body: JSON.stringify({ name: name.trim(), color }),
          });
          if (resp.ok) {
            const updated = await resp.json();
            const idx = this.ownedCalendars.findIndex(c => c.uuid === uuid);
            if (idx >= 0) this.ownedCalendars[idx] = updated;
            this.showCalendarModal = false;
            if (this.calendar) this.calendar.refetchEvents();
            this.refetchAgenda();
          }
        }
      } finally {
        this.savingCalendar = false;
      }
    },

    async deleteCalendar(cal) {
      const ok = await AppDialog.confirm({
        title: 'Delete calendar',
        message: `Delete "${cal.name}" and all its events?`,
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;

      const resp = await fetch(`/api/v1/calendar/calendars/${cal.uuid}`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (resp.ok || resp.status === 204) {
        this.ownedCalendars = this.ownedCalendars.filter(c => c.uuid !== cal.uuid);
        delete this.visibleCalendars[cal.uuid];
        this._saveVisibility();
        if (this.calendar) this.calendar.refetchEvents();
        this.refetchAgenda();
      }
    },

    // --- External Calendars ---
    _loadExternalCalendars() {
      fetch('/api/v1/calendar/external-calendars', { credentials: 'same-origin' })
        .then(r => r.ok ? r.json() : [])
        .then(data => {
          this.externalCalendars = data;
          // Make newly loaded external calendars visible by default
          const saved = localStorage.getItem('calendarVisible');
          if (!saved) {
            data.forEach(c => { this.visibleCalendars[c.uuid] = true; });
            this.visibleCalendars = { ...this.visibleCalendars };
          } else {
            // Ensure new external calendars get visible if not yet tracked
            let changed = false;
            data.forEach(c => {
              if (!(c.uuid in this.visibleCalendars)) {
                this.visibleCalendars[c.uuid] = true;
                changed = true;
              }
            });
            if (changed) {
              this.visibleCalendars = { ...this.visibleCalendars };
              this._saveVisibility();
            }
          }
          if (this.calendar) this.calendar.refetchEvents();
          this.refetchAgenda();
        })
        .catch(() => {});
    },

    createExternalCalendar() {
      const defaultColor = this.calendarColors[(this.ownedCalendars.length + this.externalCalendars.length) % this.calendarColors.length];
      this.externalForm = { name: '', url: '', color: defaultColor };
      this.showExternalModal = true;
      this.$nextTick(() => {
        const input = document.getElementById('external-form-url');
        if (input) { input.focus(); }
      });
    },

    async saveExternalCalendar() {
      const { name, url, color } = this.externalForm;
      if (!url.trim() || this.savingExternal) return;

      this.savingExternal = true;
      try {
        const resp = await fetch('/api/v1/calendar/external-calendars', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ name: (name.trim() || 'External calendar'), url: url.trim(), color }),
        });
        if (resp.ok) {
          const ext = await resp.json();
          this.externalCalendars.push(ext);
          this.visibleCalendars[ext.uuid] = true;
          this._saveVisibility();
          this.showExternalModal = false;
          // The sync runs async on the backend; refetch events after a short delay
          setTimeout(() => {
            if (this.calendar) this.calendar.refetchEvents();
            this.refetchAgenda();
          }, 3000);
        }
      } finally {
        this.savingExternal = false;
      }
    },

    async deleteExternalCalendar(ext) {
      const ok = await AppDialog.confirm({
        title: 'Remove external calendar',
        message: `Remove "${ext.name}" and all its synced events?`,
        okLabel: 'Remove',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;

      const resp = await fetch(`/api/v1/calendar/external-calendars/${ext.external_source.uuid}`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (resp.ok || resp.status === 204) {
        this.externalCalendars = this.externalCalendars.filter(c => c.uuid !== ext.uuid);
        delete this.visibleCalendars[ext.uuid];
        this._saveVisibility();
        if (this.calendar) this.calendar.refetchEvents();
        this.refetchAgenda();
      }
    },

    async syncExternalCalendar(ext) {
      if (this.syncingExternal[ext.uuid]) return;
      this.syncingExternal = { ...this.syncingExternal, [ext.uuid]: true };
      try {
        await fetch(`/api/v1/calendar/external-calendars/${ext.external_source.uuid}/sync`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        // Refetch after a delay to allow backend sync
        setTimeout(() => {
          if (this.calendar) this.calendar.refetchEvents();
          this.refetchAgenda();
        }, 3000);
      } finally {
        // Keep spinner for a few seconds while backend syncs
        setTimeout(() => {
          this.syncingExternal = { ...this.syncingExternal, [ext.uuid]: false };
        }, 3000);
      }
    },
  };
};
