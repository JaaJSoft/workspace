window.calendarApp = function calendarApp(calendarsData) {
  return {
    calendar: null,
    currentView: 'dayGridMonth',
    currentTitle: '',

    // Preferences
    _prefsDefaults: { defaultView: 'dayGridMonth', firstDay: 1, weekNumbers: false, dayMaxEvents: 3, timeFormat: '24h', defaultAllDay: false },
    prefs: { defaultView: 'dayGridMonth', firstDay: 1, weekNumbers: false, dayMaxEvents: 3, timeFormat: '24h', defaultAllDay: false },

    // Sidebar
    collapsed: localStorage.getItem('calendarSidebarCollapsed') === 'true',
    ownedCalendars: calendarsData?.owned || [],
    subscribedCalendars: calendarsData?.subscribed || [],
    visibleCalendars: {},  // { uuid: true } for reactive tracking

    // Calendar form state
    calendarColors: ['primary', 'secondary', 'accent', 'info', 'success', 'warning', 'error'],
    showCalendarModal: false,
    calendarModalMode: 'create',
    calendarForm: { uuid: null, name: '', color: 'primary' },

    // Panel & modal state
    showPanel: false,
    showModal: false,
    modalMode: 'create',
    form: {
      uuid: null,
      calendar_id: null,
      title: '',
      description: '',
      start: '',
      end: '',
      all_day: false,
      location: '',
    },
    eventOwner: null,
    eventMembers: [],
    myInviteStatus: null,
    selectedMembers: [],
    saving: false,
    deleting: false,

    csrfToken() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
        || '';
    },

    init() {
      // Initialize visible calendars (all visible by default)
      const saved = localStorage.getItem('calendarVisible');
      if (saved) {
        try {
          const arr = JSON.parse(saved);
          const obj = {};
          arr.forEach(id => obj[id] = true);
          this.visibleCalendars = obj;
        } catch (e) {
          this._showAllCalendars();
        }
      } else {
        this._showAllCalendars();
      }

      // Load preferences from API
      this._loadPrefs();

      if (this.isMobile()) this.collapsed = true;
      window.matchMedia('(max-width: 1023px)').addEventListener('change', (e) => {
        if (e.matches) this.collapsed = true;
      });

      this.$watch('collapsed', () => {
        setTimeout(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); }, 350);
      });

      this.$watch('showPanel', () => {
        setTimeout(() => { if (this.calendar) this.calendar.updateSize(); }, 250);
      });

      this.$nextTick(() => {
        this.initCalendar();
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    _prefsUrl: '/api/v1/settings/calendar/preferences',

    _loadPrefs() {
      fetch(this._prefsUrl, { credentials: 'same-origin' })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data?.value && typeof data.value === 'object') {
            this.prefs = { ...this._prefsDefaults, ...data.value };
            this._applyAllPrefs();
          }
        })
        .catch(() => {});
    },

    _savePrefs() {
      fetch(this._prefsUrl, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
        body: JSON.stringify({ value: this.prefs }),
      }).catch(() => {});
    },

    _timeFormatFC() {
      return this.prefs.timeFormat === '12h'
        ? { hour: 'numeric', minute: '2-digit', meridiem: 'short' }
        : { hour: '2-digit', minute: '2-digit', hour12: false };
    },

    _applyAllPrefs() {
      if (!this.calendar) return;
      this.calendar.setOption('firstDay', this.prefs.firstDay);
      this.calendar.setOption('weekNumbers', this.prefs.weekNumbers);
      this.calendar.setOption('dayMaxEvents', this.prefs.dayMaxEvents);
      this.calendar.setOption('eventTimeFormat', this._timeFormatFC());
      this.calendar.setOption('slotLabelFormat', this._timeFormatFC());
      // Only apply default view if URL didn't specify one
      const urlView = new URLSearchParams(window.location.search).get('view');
      if (!urlView && this.currentView !== this.prefs.defaultView) {
        this.calendar.changeView(this.prefs.defaultView);
        this.currentView = this.prefs.defaultView;
        this._syncTitle();
      }
    },

    updatePref(key, value) {
      this.prefs = { ...this.prefs, [key]: value };
      this._savePrefs();
      if (this.calendar) {
        if (key === 'defaultView') {
          this.calendar.changeView(value);
          this.currentView = value;
          this._syncTitle();
        } else if (key === 'firstDay') {
          this.calendar.setOption('firstDay', value);
        } else if (key === 'weekNumbers') {
          this.calendar.setOption('weekNumbers', value);
        } else if (key === 'dayMaxEvents') {
          this.calendar.setOption('dayMaxEvents', value);
        } else if (key === 'timeFormat') {
          this.calendar.setOption('eventTimeFormat', this._timeFormatFC());
          this.calendar.setOption('slotLabelFormat', this._timeFormatFC());
        }
      }
    },

    _showAllCalendars() {
      const obj = {};
      this.ownedCalendars.forEach(c => obj[c.uuid] = true);
      this.subscribedCalendars.forEach(c => obj[c.uuid] = true);
      this.visibleCalendars = obj;
    },

    _saveVisibility() {
      const ids = Object.keys(this.visibleCalendars).filter(k => this.visibleCalendars[k]);
      localStorage.setItem('calendarVisible', JSON.stringify(ids));
    },

    isCalendarVisible(uuid) {
      return !!this.visibleCalendars[uuid];
    },

    // --- Sidebar ---
    isMobile() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },

    toggleCollapse() {
      if (this.isMobile()) return;
      this.collapsed = !this.collapsed;
      localStorage.setItem('calendarSidebarCollapsed', this.collapsed);
    },

    toggleCalendarVisibility(uuid) {
      this.visibleCalendars = { ...this.visibleCalendars, [uuid]: !this.visibleCalendars[uuid] };
      this._saveVisibility();
      if (this.calendar) this.calendar.refetchEvents();
    },

    // --- View controls ---
    calendarPrev() {
      if (this.calendar) { this.calendar.prev(); this._syncTitle(); this._syncUrl(); }
    },
    calendarNext() {
      if (this.calendar) { this.calendar.next(); this._syncTitle(); this._syncUrl(); }
    },
    calendarToday() {
      if (this.calendar) { this.calendar.today(); this._syncTitle(); this._syncUrl(); }
    },
    changeView(view) {
      if (this.calendar) {
        this.calendar.changeView(view);
        this.currentView = view;
        this._syncTitle();
        this._syncUrl();
      }
    },
    _syncTitle() {
      if (this.calendar) this.currentTitle = this.calendar.view.title;
    },

    // --- URL state ---
    _syncUrl() {
      if (!this.calendar) return;
      const params = new URLSearchParams();
      const view = this.calendar.view;
      if (view.type !== this.prefs.defaultView) params.set('view', view.type);
      // Store the current date as YYYY-MM-DD
      const d = this.calendar.getDate();
      const dateStr = d.toISOString().split('T')[0];
      const today = new Date().toISOString().split('T')[0];
      if (dateStr !== today) params.set('date', dateStr);
      if (this.showPanel && this.form.uuid) params.set('event', this.form.uuid);
      const qs = params.toString();
      const url = window.location.pathname + (qs ? '?' + qs : '');
      history.replaceState(null, '', url);
    },

    _pushUrl() {
      if (!this.calendar) return;
      const params = new URLSearchParams();
      const view = this.calendar.view;
      if (view.type !== this.prefs.defaultView) params.set('view', view.type);
      const d = this.calendar.getDate();
      const dateStr = d.toISOString().split('T')[0];
      const today = new Date().toISOString().split('T')[0];
      if (dateStr !== today) params.set('date', dateStr);
      if (this.showPanel && this.form.uuid) params.set('event', this.form.uuid);
      const qs = params.toString();
      const url = window.location.pathname + (qs ? '?' + qs : '');
      history.pushState(null, '', url);
    },

    // --- Calendar CRUD ---
    createCalendar() {
      const defaultColor = this.calendarColors[this.ownedCalendars.length % this.calendarColors.length];
      this.calendarModalMode = 'create';
      this.calendarForm = { uuid: null, name: '', color: defaultColor };
      this.showCalendarModal = true;
      this.$nextTick(() => {
        const input = document.getElementById('calendar-form-name');
        if (input) { input.focus(); input.select(); }
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    editCalendar(cal) {
      this.calendarModalMode = 'edit';
      this.calendarForm = { uuid: cal.uuid, name: cal.name, color: cal.color };
      this.showCalendarModal = true;
      this.$nextTick(() => {
        const input = document.getElementById('calendar-form-name');
        if (input) { input.focus(); input.select(); }
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    async saveCalendar() {
      const { uuid, name, color } = this.calendarForm;
      if (!name.trim()) return;

      if (this.calendarModalMode === 'create') {
        const resp = await fetch('/api/v1/calendar/calendars', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
          body: JSON.stringify({ name: name.trim(), color }),
        });
        if (resp.ok) {
          const cal = await resp.json();
          this.ownedCalendars.push(cal);
          this.visibleCalendars[cal.uuid] = true;
          this._saveVisibility();
          this.showCalendarModal = false;
          this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
        }
      } else {
        const resp = await fetch(`/api/v1/calendar/calendars/${uuid}`, {
          method: 'PUT',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
          body: JSON.stringify({ name: name.trim(), color }),
        });
        if (resp.ok) {
          const updated = await resp.json();
          const idx = this.ownedCalendars.findIndex(c => c.uuid === uuid);
          if (idx >= 0) this.ownedCalendars[idx] = updated;
          this.showCalendarModal = false;
          if (this.calendar) this.calendar.refetchEvents();
        }
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
        headers: { 'X-CSRFToken': this.csrfToken() },
      });
      if (resp.ok || resp.status === 204) {
        this.ownedCalendars = this.ownedCalendars.filter(c => c.uuid !== cal.uuid);
        delete this.visibleCalendars[cal.uuid];
        this._saveVisibility();
        if (this.calendar) this.calendar.refetchEvents();
      }
    },

    // --- FullCalendar ---
    initCalendar() {
      const calendarEl = this.$refs.calendarEl;
      if (!calendarEl) return;

      const params = new URLSearchParams(window.location.search);
      const urlView = params.get('view') || this.prefs.defaultView;
      const urlDate = params.get('date');

      this.currentView = urlView;
      this.calendar = new FullCalendar.Calendar(calendarEl, {
        initialView: urlView,
        ...(urlDate ? { initialDate: urlDate } : {}),
        headerToolbar: false,
        locale: 'fr',
        firstDay: this.prefs.firstDay,
        weekNumbers: this.prefs.weekNumbers,
        nowIndicator: true,
        editable: false,
        selectable: true,
        selectMirror: true,
        dayMaxEvents: this.prefs.dayMaxEvents,
        eventTimeFormat: this._timeFormatFC(),
        slotLabelFormat: this._timeFormatFC(),
        height: '100%',

        events: (info) => {
          return this.fetchEvents(info.startStr, info.endStr);
        },

        dateClick: (info) => {
          if (info.allDay) {
            this.openCreateModal(info.dateStr, '', true);
          } else {
            this.openCreateModal(info.dateStr, this._addHour(info.dateStr), false);
          }
        },

        select: (info) => {
          if (info.allDay) {
            // FullCalendar uses exclusive end: single-day click gives next day as end
            const days = (new Date(info.endStr) - new Date(info.startStr)) / 86400000;
            this.openCreateModal(info.startStr, days <= 1 ? '' : info.endStr, true);
          } else {
            // Ensure at least 1h gap (default slot selection can be 30min)
            const gap = new Date(info.endStr) - new Date(info.startStr);
            const end = gap < 3600000 ? this._addHour(info.startStr) : info.endStr;
            this.openCreateModal(info.startStr, end, false);
          }
          this.calendar.unselect();
        },

        eventClick: (info) => {
          this.openViewPanel(info.event.extendedProps._raw);
        },
      });

      this.calendar.render();
      this._syncTitle();

      const eventId = params.get('event');
      if (eventId) this.openEventById(eventId);

      // Browser back/forward
      window.addEventListener('popstate', () => {
        const p = new URLSearchParams(window.location.search);
        const view = p.get('view') || this.prefs.defaultView;
        const date = p.get('date');
        const evt = p.get('event');

        if (view !== this.calendar.view.type) {
          this.calendar.changeView(view);
          this.currentView = view;
        }
        if (date) {
          this.calendar.gotoDate(date);
        } else {
          this.calendar.today();
        }
        this._syncTitle();

        if (evt) {
          this.openEventById(evt);
        } else if (this.showPanel) {
          this.showPanel = false;
        }
      });
    },

    async fetchEvents(start, end) {
      const calIds = Object.keys(this.visibleCalendars).filter(k => this.visibleCalendars[k]).join(',');
      const url = `/api/v1/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}` +
        `&calendar_ids=${encodeURIComponent(calIds)}`;

      const resp = await fetch(url, { credentials: 'same-origin' });
      if (!resp.ok) return [];
      const data = await resp.json();
      const currentUserId = document.body.dataset.userId;

      return data.map(event => {
        const isOwner = String(event.owner.id) === String(currentUserId);
        const membership = event.members.find(m => String(m.user.id) === String(currentUserId));
        const isInvited = !isOwner && !!membership;
        const isPending = isInvited && membership.status === 'pending';

        // Find calendar color
        const cal = [...this.ownedCalendars, ...this.subscribedCalendars].find(c => c.uuid === event.calendar_id);
        const color = cal?.color || 'primary';

        const classNames = [`event-color-${color}`];
        if (isInvited) classNames.push('event-invited');
        if (isPending) classNames.push('event-pending');

        return {
          id: event.uuid,
          title: event.title,
          start: event.start,
          end: event.end,
          allDay: event.all_day,
          classNames,
          extendedProps: { _raw: event },
        };
      });
    },

    // --- All-day toggle ---
    toggleAllDay() {
      const newAllDay = !this.form.all_day;
      const start = this.form.start;
      const end = this.form.end;

      // Clear values, flip the flag (changes input type), then set converted values
      this.form.start = '';
      this.form.end = '';
      this.form.all_day = newAllDay;

      this.$nextTick(() => {
        if (newAllDay) {
          this.form.start = this.toLocalDate(start);
          this.form.end = this.toLocalDate(end);
        } else {
          this.form.start = (start && !start.includes('T')) ? start + 'T09:00' : start;
          this.form.end = (end && !end.includes('T')) ? end + 'T10:00' : end;
        }
      });
    },

    // --- Create modal ---
    openCreateModal(start, end, allDay) {
      if (this.showModal) return; // prevent double-open from dateClick + select
      this.modalMode = 'create';
      this.showPanel = false;
      const defaultCal = this.ownedCalendars[0]?.uuid || null;

      // Preference controls all-day default, ignore FullCalendar's allDay flag
      const useAllDay = this.prefs.defaultAllDay;

      // If month view gives date-only string but we need datetime, default to 09:00
      let startStr = start;
      let endStr = end;
      if (!useAllDay && startStr.length === 10) {
        startStr = startStr + 'T09:00:00';
        endStr = endStr || (startStr.split('T')[0] + 'T10:00:00');
      }

      const startVal = useAllDay ? this.toLocalDate(startStr) : this.toLocalDatetime(startStr);
      const endVal = endStr ? (useAllDay ? this.toLocalDate(endStr) : this.toLocalDatetime(endStr)) : '';

      this.form = {
        uuid: null,
        calendar_id: defaultCal,
        title: '',
        description: '',
        start: startVal,
        end: endVal,
        all_day: useAllDay,
        location: '',
      };
      this.selectedMembers = [];
      this.eventOwner = null;
      this.eventMembers = [];
      this.myInviteStatus = null;
      this.showModal = true;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    openViewPanel(event) {
      const currentUserId = String(document.body.dataset.userId);
      const isOwner = String(event.owner.id) === currentUserId;

      this.form = {
        uuid: event.uuid,
        calendar_id: String(event.calendar_id),
        title: event.title,
        description: event.description || '',
        start: this.toLocalDatetime(event.start),
        end: event.end ? this.toLocalDatetime(event.end) : '',
        all_day: !!event.all_day,
        location: event.location || '',
      };
      this.eventOwner = event.owner;
      this.eventMembers = event.members || [];
      this.selectedMembers = (event.members || []).map(m => m.user);
      this.myInviteStatus = isOwner ? null : ((event.members || []).find(m => String(m.user.id) === currentUserId)?.status || null);
      this.showPanel = true;
      this._pushUrl();
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    closePanel() {
      this.showPanel = false;
      this._syncUrl();
    },

    async openEventById(eventId) {
      try {
        const resp = await fetch(`/api/v1/calendar/events/${eventId}`, { credentials: 'same-origin' });
        if (resp.ok) {
          const event = await resp.json();
          // Navigate calendar to the event's date
          if (this.calendar && event.start) {
            this.calendar.gotoDate(event.start);
            this._syncTitle();
          }
          this.openViewPanel(event);
        }
      } catch (e) {}
    },

    openEditModal() {
      this.modalMode = 'edit';
      // Normalize dates for the input type (panel stores datetime format)
      if (this.form.all_day) {
        this.form.start = this.toLocalDate(this.form.start);
        this.form.end = this.toLocalDate(this.form.end);
      }
      this.showModal = true;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    isOwner() {
      if (!this.eventOwner) return true;
      return String(this.eventOwner.id) === String(document.body.dataset.userId);
    },

    // --- Save ---
    async saveEvent() {
      if (!this.form.title.trim()) return;
      if (this.form.end && this.form.start && new Date(this.form.end) < new Date(this.form.start)) {
        await AppDialog.error({ message: 'End date cannot be before start date.' });
        return;
      }
      this.saving = true;

      const payload = {
        calendar_id: this.form.calendar_id,
        title: this.form.title.trim(),
        description: this.form.description,
        start: new Date(this.form.start).toISOString(),
        end: this.form.end ? new Date(this.form.end).toISOString() : null,
        all_day: this.form.all_day,
        location: this.form.location,
        member_ids: this.selectedMembers.map(u => u.id),
      };

      try {
        const [url, method] = this.modalMode === 'create'
          ? ['/api/v1/calendar/events', 'POST']
          : [`/api/v1/calendar/events/${this.form.uuid}`, 'PUT'];

        const resp = await fetch(url, {
          method,
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
          body: JSON.stringify(payload),
        });
        if (resp.ok) {
          const saved = await resp.json();
          this.showModal = false;
          this.calendar.refetchEvents();
          if (this.modalMode === 'edit' && this.showPanel) {
            this.form = {
              uuid: saved.uuid,
              calendar_id: saved.calendar_id,
              title: saved.title,
              description: saved.description || '',
              start: this.toLocalDatetime(saved.start),
              end: saved.end ? this.toLocalDatetime(saved.end) : '',
              all_day: saved.all_day,
              location: saved.location || '',
            };
            this.eventOwner = saved.owner;
            this.eventMembers = saved.members;
            this.selectedMembers = saved.members.map(m => m.user);
            this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
          }
        }
      } catch (e) {}
      this.saving = false;
    },

    // --- Delete event ---
    async deleteEvent() {
      if (!this.form.uuid) return;
      const ok = await AppDialog.confirm({
        title: 'Delete event',
        message: `Delete "${this.form.title}"?`,
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;

      this.deleting = true;
      try {
        const resp = await fetch(`/api/v1/calendar/events/${this.form.uuid}`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': this.csrfToken() },
        });
        if (resp.ok || resp.status === 204) {
          this.showPanel = false;
          this.calendar.refetchEvents();
        }
      } catch (e) {}
      this.deleting = false;
    },

    // --- Respond ---
    async respondToInvitation(newStatus) {
      try {
        const resp = await fetch(`/api/v1/calendar/events/${this.form.uuid}/respond`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
          body: JSON.stringify({ status: newStatus }),
        });
        if (resp.ok) {
          this.myInviteStatus = newStatus;
          const currentUserId = String(document.body.dataset.userId);
          const member = this.eventMembers.find(m => String(m.user.id) === currentUserId);
          if (member) member.status = newStatus;
          this.calendar.refetchEvents();
        }
      } catch (e) {}
    },

    // --- Members ---
    addMember(event) {
      const user = event.detail.user;
      if (!this.selectedMembers.find(m => m.id === user.id)) {
        this.selectedMembers.push(user);
      }
    },
    removeMember(userId) {
      this.selectedMembers = this.selectedMembers.filter(m => m.id !== userId);
    },

    // --- Duration shortcuts ---
    activeDuration() {
      if (!this.form.start || !this.form.end) return null;
      const startMs = new Date(this.form.start).getTime();
      const endMs = new Date(this.form.end).getTime();
      return Math.round((endMs - startMs) / 60000);
    },

    applyDuration(minutes) {
      if (!this.form.start) return;
      const d = new Date(this.form.start);
      d.setMinutes(d.getMinutes() + minutes);
      this.form.end = this.form.all_day ? this.toLocalDate(d.toISOString()) : this.toLocalDatetime(d.toISOString());
    },

    // --- Helpers ---
    toLocalDatetime(isoStr) {
      if (!isoStr) return '';
      const d = new Date(isoStr);
      if (isoStr.length === 10) return isoStr;
      const pad = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    },

    toLocalDate(isoStr) {
      if (!isoStr) return '';
      return isoStr.split('T')[0];
    },

    _addHour(isoStr) {
      const d = new Date(isoStr);
      d.setHours(d.getHours() + 1);
      return d.toISOString();
    },

    // --- Display helpers ---
    _fmtDate(isoStr) {
      if (!isoStr) return '';
      const d = new Date(isoStr);
      const today = new Date();
      const tomorrow = new Date(today);
      tomorrow.setDate(tomorrow.getDate() + 1);
      const isToday = d.toDateString() === today.toDateString();
      const isTomorrow = d.toDateString() === tomorrow.toDateString();
      const opts = { weekday: 'long', day: 'numeric', month: 'long' };
      if (d.getFullYear() !== today.getFullYear()) opts.year = 'numeric';
      const datePart = d.toLocaleDateString('en-US', opts);
      if (isToday) return `Today \u2014 ${datePart}`;
      if (isTomorrow) return `Tomorrow \u2014 ${datePart}`;
      return datePart;
    },

    _fmtTime(isoStr) {
      if (!isoStr) return '';
      const d = new Date(isoStr);
      return this.prefs.timeFormat === '12h'
        ? d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
        : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    },

    _sameDay(a, b) {
      if (!a || !b) return false;
      return new Date(a).toDateString() === new Date(b).toDateString();
    },

    panelDateDisplay() {
      const { start, end, all_day } = this.form;
      if (!start) return '';
      const dateStr = this._fmtDate(start);
      if (all_day) {
        if (!end || this._sameDay(start, end) || this._sameDay(start, new Date(new Date(end).getTime() - 86400000))) {
          return dateStr;
        }
        return `${dateStr} \u2192 ${this._fmtDate(end)}`;
      }
      const startTime = this._fmtTime(start);
      if (!end) return `${dateStr}, ${startTime}`;
      const endTime = this._fmtTime(end);
      if (this._sameDay(start, end)) {
        return `${dateStr}, ${startTime} \u2013 ${endTime}`;
      }
      return `${dateStr}, ${startTime} \u2192 ${this._fmtDate(end)}, ${endTime}`;
    },

    panelTimeLabel() {
      const { start, end, all_day } = this.form;
      if (all_day) return 'All day';
      if (!start) return '';
      const startTime = this._fmtTime(start);
      if (!end || this._sameDay(start, end)) {
        const endTime = end ? this._fmtTime(end) : null;
        return endTime ? `${startTime} \u2013 ${endTime}` : startTime;
      }
      return startTime;
    },

    eventCalendarObj() {
      return [...this.ownedCalendars, ...this.subscribedCalendars].find(c => c.uuid === this.form.calendar_id) || null;
    },

    eventCalendarColor() {
      return this.eventCalendarObj()?.color || 'primary';
    },

    closeModal() { this.showModal = false; },
  };
};
