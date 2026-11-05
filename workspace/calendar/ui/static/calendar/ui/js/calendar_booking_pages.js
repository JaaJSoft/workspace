// Booking pages, as their owner manages them from the calendar: the list of
// pages with their public links, the page form (weekly hours, rules, the
// calendars checked for conflicts) and the upcoming appointments.
// Backend: calendar/views/booking_pages.py.

// Weekly hours are seven lists, Monday first, of ['HH:MM', 'HH:MM'] ranges.
window.bookingHours = {
  defaultWeek() {
    return [0, 1, 2, 3, 4, 5, 6].map((day) => (day < 5 ? [['09:00', '17:00']] : []));
  },

  copyWeek(week) {
    return (week || []).map((ranges) => ranges.map((range) => [range[0], range[1]]));
  },

  // The range added after the day's last one: the hour following it, or 9
  // to 17 on an empty day. null when the day has no hour left after it.
  nextRange(ranges) {
    if (!ranges.length) return ['09:00', '17:00'];
    const hour = Number(ranges[ranges.length - 1][1].slice(0, 2)) + 1;
    if (hour > 22) return null;
    const pad = (n) => String(n).padStart(2, '0');
    return [`${pad(hour)}:00`, `${pad(hour + 1)}:00`];
  },

  // Monday first, in the interface language. 2024-01-01 is a Monday.
  weekdayLabels(locale) {
    return [1, 2, 3, 4, 5, 6, 7].map((d) => new Date(Date.UTC(2024, 0, d, 12)).toLocaleDateString(locale, {
      timeZone: 'UTC', weekday: 'long',
    }));
  },
};

window.calendarBookingPagesMixin = function calendarBookingPagesMixin() {
  return {
    showBookingListModal: false,
    showBookingFormModal: false,
    bookingTab: 'pages',
    bookingPages: [],
    bookingPagesLoading: false,
    upcomingBookings: [],
    upcomingBookingsLoading: false,
    bookingForm: null,
    bookingFormError: null,
    bookingFormSaving: false,
    bookingMailAccounts: [],
    bookingZones: [],
    bookingWeekdays: window.bookingHours.weekdayLabels(window.getUserLocale ? window.getUserLocale() : undefined),

    openBookingPages() {
      this.showBookingListModal = true;
      this.loadBookingPages();
      if (this.bookingTab === 'bookings') this.loadUpcomingBookings();
    },

    setBookingTab(tab) {
      this.bookingTab = tab;
      if (tab === 'bookings') this.loadUpcomingBookings();
    },

    async loadBookingPages() {
      const request = this._bookingPagesRequest = (this._bookingPagesRequest || 0) + 1;
      this.bookingPagesLoading = true;
      try {
        const resp = await fetch('/api/v1/booking-pages', { credentials: 'same-origin' });
        if (resp.ok && request === this._bookingPagesRequest) {
          this.bookingPages = await resp.json();
        }
      } catch (e) {}
      if (request === this._bookingPagesRequest) this.bookingPagesLoading = false;
    },

    async loadUpcomingBookings() {
      const request = this._bookingsRequest = (this._bookingsRequest || 0) + 1;
      this.upcomingBookingsLoading = true;
      try {
        const resp = await fetch('/api/v1/bookings', { credentials: 'same-origin' });
        if (resp.ok && request === this._bookingsRequest) {
          this.upcomingBookings = await resp.json();
        }
      } catch (e) {}
      if (request === this._bookingsRequest) this.upcomingBookingsLoading = false;
    },

    // The mail module may be absent or hidden for this user: no account to
    // offer is the same as none configured.
    async _loadBookingMailAccounts() {
      try {
        const resp = await fetch('/api/v1/mail/accounts', { credentials: 'same-origin' });
        this.bookingMailAccounts = resp.ok ? await resp.json() : [];
      } catch (e) {
        this.bookingMailAccounts = [];
      }
    },

    _bookingZoneList(current) {
      let zones = [];
      try {
        zones = Intl.supportedValuesOf ? Intl.supportedValuesOf('timeZone') : [];
      } catch (e) {}
      if (current && !zones.includes(current)) zones = [current, ...zones];
      return zones;
    },

    newBookingPage() {
      const zone = (window.getUserTimeZone && window.getUserTimeZone()) || 'UTC';
      this._openBookingForm({
        uuid: null,
        title: '',
        description: '',
        location: '',
        calendar: this.ownedCalendars[0]?.uuid || '',
        busy_calendars: [...this.ownedCalendars, ...this.externalCalendars].map((c) => c.uuid),
        duration_minutes: 30,
        buffer_minutes: 0,
        min_notice_hours: 4,
        days_ahead: 30,
        weekly_hours: window.bookingHours.defaultWeek(),
        timezone: zone,
        mail_account: '',
        is_active: true,
      });
    },

    editBookingPage(page) {
      this._openBookingForm({
        ...page,
        busy_calendars: [...page.busy_calendars],
        weekly_hours: window.bookingHours.copyWeek(page.weekly_hours),
        mail_account: page.mail_account || '',
      });
    },

    _openBookingForm(form) {
      this.bookingForm = form;
      this.bookingFormError = null;
      this.bookingZones = this._bookingZoneList(form.timezone);
      this.showBookingFormModal = true;
      this._loadBookingMailAccounts();
    },

    bookingBusyCalendarChoices() {
      return [...this.ownedCalendars, ...this.externalCalendars];
    },

    toggleBookingBusyCalendar(uuid) {
      const list = this.bookingForm.busy_calendars;
      const index = list.indexOf(uuid);
      if (index >= 0) list.splice(index, 1);
      else list.push(uuid);
    },

    toggleBookingDay(day) {
      const week = this.bookingForm.weekly_hours;
      week[day] = week[day].length ? [] : [['09:00', '17:00']];
    },

    canAddBookingRange(day) {
      return window.bookingHours.nextRange(this.bookingForm.weekly_hours[day]) !== null;
    },

    addBookingRange(day) {
      const ranges = this.bookingForm.weekly_hours[day];
      const next = window.bookingHours.nextRange(ranges);
      if (next) ranges.push(next);
    },

    removeBookingRange(day, index) {
      this.bookingForm.weekly_hours[day].splice(index, 1);
    },

    async submitBookingPage() {
      const form = this.bookingForm;
      if (!form || !form.title.trim() || this.bookingFormSaving) return;
      this.bookingFormSaving = true;
      this.bookingFormError = null;
      const body = {
        title: form.title.trim(),
        description: form.description,
        location: form.location.trim(),
        calendar: form.calendar,
        busy_calendars: form.busy_calendars,
        duration_minutes: Number(form.duration_minutes),
        buffer_minutes: Number(form.buffer_minutes),
        min_notice_hours: Number(form.min_notice_hours),
        days_ahead: Number(form.days_ahead),
        weekly_hours: form.weekly_hours,
        timezone: form.timezone,
        mail_account: form.mail_account || null,
        is_active: form.is_active,
      };
      try {
        const resp = await fetch(form.uuid ? `/api/v1/booking-pages/${form.uuid}` : '/api/v1/booking-pages', {
          method: form.uuid ? 'PATCH' : 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify(body),
        });
        if (resp.ok) {
          this.showBookingFormModal = false;
          this.loadBookingPages();
        } else {
          const data = await resp.json().catch(() => null);
          this.bookingFormError = this._bookingErrorText(data) || gettext('The booking page could not be saved.');
        }
      } catch (e) {
        this.bookingFormError = gettext('Network error. Please try again.');
      }
      this.bookingFormSaving = false;
    },

    _bookingErrorText(data) {
      if (!data || typeof data !== 'object') return '';
      if (data.detail) return data.detail;
      const first = Object.values(data)[0];
      return Array.isArray(first) ? String(first[0]) : '';
    },

    async toggleBookingPageActive(page) {
      try {
        const resp = await fetch(`/api/v1/booking-pages/${page.uuid}`, {
          method: 'PATCH',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ is_active: !page.is_active }),
        });
        if (resp.ok) {
          const updated = await resp.json();
          this.bookingPages = this.bookingPages.map((p) => (p.uuid === updated.uuid ? updated : p));
        }
      } catch (e) {}
    },

    async deleteBookingPage(page) {
      const ok = await AppDialog.confirm({
        title: gettext('Delete booking page'),
        message: gettext('Its link stops working. Appointments already booked stay on your calendar.'),
        okLabel: gettext('Delete'),
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        const resp = await fetch(`/api/v1/booking-pages/${page.uuid}`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (resp.ok) this.loadBookingPages();
      } catch (e) {}
    },

    copyBookingLink(page) {
      navigator.clipboard.writeText(page.share_url).then(() => {
        if (window.AppAlert) window.AppAlert.success(gettext('Link copied'), { duration: 2000 });
      }).catch(() => {
        if (window.AppAlert) window.AppAlert.error(gettext('Failed to copy link'));
      });
    },

    async cancelUpcomingBooking(booking) {
      const ok = await AppDialog.confirm({
        title: gettext('Cancel appointment'),
        message: interpolate(gettext('Cancel the appointment with %(name)s? It is removed from your calendar.'), { name: booking.guest_name }, true),
        okLabel: gettext('Cancel appointment'),
        okClass: 'btn-error',
        icon: 'calendar-x',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        const resp = await fetch(`/api/v1/bookings/${booking.uuid}/cancel`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (resp.ok || resp.status === 409) {
          this.loadUpcomingBookings();
          this.loadBookingPages();
          if (this.calendar) this.calendar.refetchEvents();
        }
      } catch (e) {}
    },

    openBookedEvent(booking) {
      if (!booking.event_id) return;
      this.showBookingListModal = false;
      this.openEventById(booking.event_id);
    },

    bookingDurationChoices() {
      const current = Number(this.bookingForm?.duration_minutes) || 30;
      return [...new Set([15, 20, 30, 45, 60, 90, 120, current])].sort((a, b) => a - b);
    },

    bookingDurationLabel(minutes) {
      return interpolate(ngettext('%(count)s minute', '%(count)s minutes', minutes), { count: minutes }, true);
    },

    bookingUpcomingLabel(count) {
      return interpolate(ngettext('%(count)s upcoming', '%(count)s upcoming', count), { count }, true);
    },

    bookingWhen(booking) {
      const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
      const locale = window.getUserLocale ? window.getUserLocale() : undefined;
      const start = new Date(booking.start);
      return start.toLocaleDateString(locale, { timeZone: tz, weekday: 'short', day: 'numeric', month: 'short' })
        + ' · ' + start.toLocaleTimeString(locale, { timeZone: tz, hour: '2-digit', minute: '2-digit' });
    },
  };
};
