// Public booking pages: the slot picker anyone holding the link uses, and the
// page a guest manages their booking from. Neither has a signed-in user, so
// the zone is the visitor's browser one (they can switch it), never a setting.
(function () {
  // 'en-CA' formats as YYYY-MM-DD: a day key comparable as a string.
  const dayKeyFormatter = window.zonedFormatter('en-CA');

  function browserZone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch (e) {
      return 'UTC';
    }
  }

  function zoneList(current) {
    let zones = [];
    try {
      zones = Intl.supportedValuesOf ? Intl.supportedValuesOf('timeZone') : [];
    } catch (e) {
      zones = [];
    }
    if (!zones.includes(current)) zones = [current, ...zones];
    return zones;
  }

  function dayKey(iso, zone) {
    return dayKeyFormatter(zone).format(new Date(iso));
  }

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  // {year, month} (month 1-12) of the month holding *date* in *zone*.
  function monthOf(date, zone) {
    const [year, month] = dayKeyFormatter(zone).format(date).split('-').map(Number);
    return { year, month };
  }

  function shiftMonth(m, delta) {
    const index = m.year * 12 + (m.month - 1) + delta;
    return { year: Math.floor(index / 12), month: (index % 12) + 1 };
  }

  // The instants bounding a month of *zone*: [first day 00:00, next first 00:00).
  function monthRange(m, zone) {
    const next = shiftMonth(m, 1);
    return [
      window.wallClockToIso(`${m.year}-${pad(m.month)}-01T00:00`, zone),
      window.wallClockToIso(`${next.year}-${pad(next.month)}-01T00:00`, zone),
    ];
  }

  // The weeks of a month view, Monday first: each a list of seven
  // 'YYYY-MM-DD' keys, null for the days of the neighbouring months.
  function monthGrid(m) {
    const first = new Date(Date.UTC(m.year, m.month - 1, 1));
    const days = new Date(Date.UTC(m.year, m.month, 0)).getUTCDate();
    const lead = (first.getUTCDay() + 6) % 7;
    const cells = [];
    for (let i = 0; i < lead; i++) cells.push(null);
    for (let d = 1; d <= days; d++) cells.push(`${m.year}-${pad(m.month)}-${pad(d)}`);
    while (cells.length % 7) cells.push(null);
    const weeks = [];
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
    return weeks;
  }

  function groupByDay(slots, zone) {
    const days = {};
    for (const iso of slots) {
      const key = dayKey(iso, zone);
      (days[key] = days[key] || []).push(iso);
    }
    return days;
  }

  function formatTime(iso, zone) {
    return new Date(iso).toLocaleTimeString(window.getUserLocale(), {
      timeZone: zone, hour: '2-digit', minute: '2-digit',
    });
  }

  // A day key read as a calendar day: noon UTC keeps it on the same date in
  // every zone the formatter could apply.
  function formatDay(key, options) {
    return new Date(key + 'T12:00:00Z').toLocaleDateString(window.getUserLocale(), {
      timeZone: 'UTC', ...options,
    });
  }

  function formatWhen(iso, zone) {
    const date = new Date(iso);
    return date.toLocaleDateString(window.getUserLocale(), {
      timeZone: zone, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
    }) + ' · ' + formatTime(iso, zone);
  }

  function weekdayLabels() {
    // 2024-01-01 is a Monday.
    return [0, 1, 2, 3, 4, 5, 6].map((i) => formatDay(`2024-01-0${i + 1}`, { weekday: 'short' }));
  }

  window.bookingUtils = {
    browserZone, zoneList, dayKey, monthOf, shiftMonth, monthRange, monthGrid,
    groupByDay, formatTime, formatDay, formatWhen, weekdayLabels,
  };

  window.bookingPage = function bookingPage() {
    const page = JSON.parse(document.getElementById('booking-page-data').textContent);
    const zone = browserZone();
    const today = monthOf(new Date(), zone);
    return {
      page,
      zone,
      zones: zoneList(zone),
      weekdays: weekdayLabels(),
      month: today,
      firstMonth: today,
      lastMonth: monthOf(new Date(Date.now() + page.days_ahead * 86400000), zone),
      slotsByDay: {},
      loading: false,
      loadError: false,
      selectedDay: null,
      selectedSlot: null,
      step: 'pick',
      form: { name: '', email: '', note: '' },
      submitting: false,
      error: '',
      result: null,
      copied: false,
      _request: 0,

      init() {
        this.loadMonth();
      },

      monthLabel() {
        return formatDay(`${this.month.year}-${pad(this.month.month)}-01`, { month: 'long', year: 'numeric' });
      },

      weeks() {
        return monthGrid(this.month);
      },

      canGoBack() {
        return this.month.year * 12 + this.month.month > this.firstMonth.year * 12 + this.firstMonth.month;
      },

      canGoForward() {
        return this.month.year * 12 + this.month.month < this.lastMonth.year * 12 + this.lastMonth.month;
      },

      changeMonth(delta) {
        this.month = shiftMonth(this.month, delta);
        this.selectedDay = null;
        this.loadMonth();
      },

      changeZone() {
        this.selectedDay = null;
        this.loadMonth();
      },

      async loadMonth() {
        const request = ++this._request;
        const [from, to] = monthRange(this.month, this.zone);
        this.loading = true;
        this.loadError = false;
        try {
          const params = new URLSearchParams({ from, to });
          const resp = await fetch(`/api/v1/booking-pages/shared/${encodeURIComponent(this.page.token)}/slots?${params}`);
          if (request !== this._request) return;
          if (!resp.ok) throw new Error(String(resp.status));
          const data = await resp.json();
          if (request !== this._request) return;
          this.slotsByDay = groupByDay(data.slots, this.zone);
          if (!this.selectedDay || !this.slotsByDay[this.selectedDay]) {
            this.selectedDay = Object.keys(this.slotsByDay).sort()[0] || null;
          }
        } catch (e) {
          if (request === this._request) {
            this.slotsByDay = {};
            this.loadError = true;
          }
        }
        if (request === this._request) this.loading = false;
      },

      hasSlots(key) {
        return Boolean(key && this.slotsByDay[key]);
      },

      isToday(key) {
        return key === dayKey(new Date().toISOString(), this.zone);
      },

      dayNumber(key) {
        return Number(key.slice(8));
      },

      daySlots() {
        return this.slotsByDay[this.selectedDay] || [];
      },

      noSlotsThisMonth() {
        return !this.loading && !this.loadError && Object.keys(this.slotsByDay).length === 0;
      },

      selectedDayLabel() {
        return this.selectedDay ? formatDay(this.selectedDay, { weekday: 'long', day: 'numeric', month: 'long' }) : '';
      },

      time(iso) {
        return formatTime(iso, this.zone);
      },

      when(iso) {
        return formatWhen(iso, this.zone);
      },

      pickSlot(iso) {
        this.selectedSlot = iso;
        this.error = '';
        this.step = 'form';
      },

      backToSlots() {
        this.step = 'pick';
        this.error = '';
      },

      canSubmit() {
        return this.form.name.trim() && this.form.email.trim() && !this.submitting;
      },

      async submit() {
        if (!this.canSubmit()) return;
        this.submitting = true;
        this.error = '';
        try {
          const resp = await fetch(`/api/v1/booking-pages/shared/${encodeURIComponent(this.page.token)}/book`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              start: this.selectedSlot,
              name: this.form.name.trim(),
              email: this.form.email.trim(),
              note: this.form.note.trim(),
              timezone: this.zone,
            }),
          });
          const data = await resp.json().catch(() => ({}));
          if (resp.status === 201) {
            this.result = data;
            this.step = 'done';
          } else if (resp.status === 409) {
            this.error = data.detail || gettext('This time is no longer available. Pick another one.');
            this.step = 'pick';
            this.loadMonth();
          } else if (resp.status === 429) {
            this.error = gettext('Too many attempts. Please try again later.');
          } else if (data.email) {
            this.error = gettext('Enter a valid email address.');
          } else {
            this.error = data.detail || gettext('The booking failed. Please try again.');
          }
        } catch (e) {
          this.error = gettext('Network error. Please try again.');
        }
        this.submitting = false;
      },

      confirmationSentLabel() {
        return interpolate(gettext('A confirmation is on its way to %(email)s.'), { email: this.form.email.trim() }, true);
      },

      async copyManageLink() {
        try {
          await navigator.clipboard.writeText(this.result.manage_url);
          this.copied = true;
          setTimeout(() => { this.copied = false; }, 2000);
        } catch (e) {}
      },
    };
  };

  window.bookingManage = function bookingManage(token) {
    const booking = JSON.parse(document.getElementById('booking-data').textContent);
    const zone = browserZone();
    return {
      booking,
      zone,
      canCancel: JSON.parse(document.getElementById('booking-can-cancel').textContent),
      confirming: false,
      cancelling: false,
      error: '',

      when(iso) {
        return formatWhen(iso, this.zone);
      },

      endTime() {
        return formatTime(this.booking.end, this.zone);
      },

      async cancel() {
        this.cancelling = true;
        this.error = '';
        try {
          const resp = await fetch(`/api/v1/bookings/shared/${encodeURIComponent(token)}/cancel`, { method: 'POST' });
          const data = await resp.json().catch(() => ({}));
          if (resp.ok) {
            this.booking = data;
            this.canCancel = false;
            this.confirming = false;
          } else {
            this.error = data.detail || gettext('The cancellation failed. Please try again.');
          }
        } catch (e) {
          this.error = gettext('Network error. Please try again.');
        }
        this.cancelling = false;
      },
    };
  };
})();
