window.calendarApp = function calendarApp(calendarsData) {
  return {
    calendar: null,
    currentView: 'dayGridMonth',
    currentTitle: '',

    // Preferences
    _prefsDefaults: { defaultView: 'dayGridMonth', firstDay: 1, weekNumbers: false, dayMaxEvents: 3, timeFormat: '24h', defaultAllDay: false, showDeclined: false },
    prefs: { defaultView: 'dayGridMonth', firstDay: 1, weekNumbers: false, dayMaxEvents: 3, timeFormat: '24h', defaultAllDay: false, showDeclined: false },

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
      recurrence_frequency: null,
      recurrence_interval: 1,
      recurrence_end: '',
    },
    _panelRaw: null,
    eventOwner: null,
    eventMembers: [],
    myInviteStatus: null,
    selectedMembers: [],
    saving: false,
    deleting: false,
    loadingEvent: false,

    // Scope dialog state
    showScopeDialog: false,
    scopeAction: null,
    scopeResolve: null,

    // Context menu state
    ctxMenu: { open: false, x: 0, y: 0, event: null, isOwner: false, inviteStatus: null },

    // Poll state
    showPollListModal: false,
    showPollCreateModal: false,
    showPollDetailModal: false,
    pollFilter: 'mine',    // 'mine' | 'shared'
    pollShowClosed: false,
    polls: [],
    pollsLoading: false,
    pollForm: { title: '', description: '', slots: [{ start: '', end: '', showEnd: false }, { start: '', end: '', showEnd: false }] },
    pollFormSubmitting: false,
    pollFormError: null,
    currentPoll: null,
    currentPollLoading: false,
    pollMyVotes: {},
    pollSubmitting: false,
    pollFinalizeSlotId: null,

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
        } else if (key === 'showDeclined') {
          this.calendar.refetchEvents();
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
        this.ctxMenu.open = false;
        this.calendar.changeView(view);
        this.currentView = view;
        this.calendar.setOption('selectable', view !== 'listWeek');
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
        selectable: urlView !== 'listWeek',
        selectMirror: true,
        dayMaxEvents: this.prefs.dayMaxEvents,
        eventTimeFormat: this._timeFormatFC(),
        slotLabelFormat: this._timeFormatFC(),
        height: '100%',
        listDayFormat: { weekday: 'long', day: 'numeric', month: 'long' },
        listDaySideFormat: false,

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

        eventDidMount: (info) => {
          info.el.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            this.openContextMenu(e, info.event.extendedProps._raw);
          });
          // Add recurring indicator
          const raw = info.event.extendedProps._raw;
          if (raw?.is_recurring) {
            const titleEl = info.el.querySelector('.fc-event-title') || info.el.querySelector('.fc-list-event-title');
            if (titleEl) {
              const icon = document.createElement('span');
              icon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display:inline;vertical-align:middle;margin-left:3px;opacity:0.6"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg>';
              titleEl.appendChild(icon);
            }
          }
        },
      });

      this.calendar.render();
      this._syncTitle();

      const eventId = params.get('event');
      if (eventId) this.openEventById(eventId);

      const pollId = params.get('poll');
      if (pollId) this.openPollDetail(pollId);

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

      return data.filter(event => {
        // Hide declined events unless preference is enabled
        const isOwner = String(event.owner.id) === String(currentUserId);
        if (!isOwner) {
          const membership = event.members.find(m => String(m.user.id) === String(currentUserId));
          if (membership?.status === 'declined' && !this.prefs.showDeclined) return false;
        }
        return true;
      }).map(event => {
        const isOwner = String(event.owner.id) === String(currentUserId);
        const membership = event.members.find(m => String(m.user.id) === String(currentUserId));
        const isInvited = !isOwner && !!membership;
        const isPending = isInvited && membership.status === 'pending';
        const isDeclined = isInvited && membership.status === 'declined';

        // Find calendar color
        const cal = [...this.ownedCalendars, ...this.subscribedCalendars].find(c => c.uuid === event.calendar_id);
        const color = cal?.color || 'primary';

        const classNames = [`event-color-${color}`];
        if (isInvited) classNames.push('event-invited');
        if (isPending) classNames.push('event-pending');
        if (isDeclined) classNames.push('event-declined');

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
        recurrence_frequency: null,
        recurrence_interval: 1,
        recurrence_end: '',
      };
      this._panelRaw = null;
      this.selectedMembers = [];
      this.eventOwner = null;
      this.eventMembers = [];
      this.myInviteStatus = null;
      this.showModal = true;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    openViewPanel(event) {
      this.loadingEvent = false;
      this._panelRaw = event;
      const currentUserId = String(document.body.dataset.userId);
      const isOwner = String(event.owner.id) === currentUserId;

      const allDay = !!event.all_day;
      const fmt = allDay ? (s => this.toLocalDate(s)) : (s => this.toLocalDatetime(s));
      this.form = {
        uuid: event.uuid,
        calendar_id: String(event.calendar_id),
        title: event.title,
        description: event.description || '',
        start: fmt(event.start),
        end: event.end ? fmt(event.end) : '',
        all_day: allDay,
        location: event.location || '',
        recurrence_frequency: event.recurrence_frequency || null,
        recurrence_interval: event.recurrence_interval || 1,
        recurrence_end: event.recurrence_end ? this.toLocalDate(event.recurrence_end) : '',
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
      this.loadingEvent = true;
      this.showPanel = true;
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
        } else {
          this.showPanel = false;
        }
      } catch (e) {
        this.showPanel = false;
      }
      this.loadingEvent = false;
    },

    openEditModal() {
      this.modalMode = 'edit';
      // Normalize dates for the input type (panel stores datetime format)
      if (this.form.all_day) {
        this.form.start = this.toLocalDate(this.form.start);
        this.form.end = this.toLocalDate(this.form.end);
      }
      // Populate recurrence fields from raw data
      this.form.recurrence_frequency = this._panelRaw?.recurrence_frequency || null;
      this.form.recurrence_interval = this._panelRaw?.recurrence_interval || 1;
      this.form.recurrence_end = this._panelRaw?.recurrence_end ? this.toLocalDate(this._panelRaw.recurrence_end) : '';
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
        recurrence_frequency: this.form.recurrence_frequency || null,
        recurrence_interval: this.form.recurrence_interval || 1,
        recurrence_end: this.form.recurrence_end ? new Date(this.form.recurrence_end).toISOString() : null,
      };

      try {
        let url, method;
        if (this.modalMode === 'create') {
          url = '/api/v1/calendar/events';
          method = 'POST';
        } else {
          url = `/api/v1/calendar/events/${this.form.uuid}`;
          method = 'PUT';
        }

        // For recurring event edits, ask scope
        if (this.modalMode === 'edit' && this._panelRaw?.is_recurring) {
          const scope = await this.openScopeDialog('edit');
          if (!scope) { this.saving = false; return; }
          payload.scope = scope;
          if (scope !== 'all') {
            payload.original_start = this._panelRaw.original_start;
          }
          // Use master UUID for API call
          const targetUuid = this._panelRaw.master_event_id || this.form.uuid;
          url = `/api/v1/calendar/events/${targetUuid}`;
        }

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
            this._panelRaw = saved;
            this.form = {
              uuid: saved.uuid,
              calendar_id: saved.calendar_id,
              title: saved.title,
              description: saved.description || '',
              start: this.toLocalDatetime(saved.start),
              end: saved.end ? this.toLocalDatetime(saved.end) : '',
              all_day: saved.all_day,
              location: saved.location || '',
              recurrence_frequency: saved.recurrence_frequency || null,
              recurrence_interval: saved.recurrence_interval || 1,
              recurrence_end: saved.recurrence_end ? this.toLocalDate(saved.recurrence_end) : '',
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

      // For recurring events, ask scope instead of confirm dialog
      if (this._panelRaw?.is_recurring) {
        const scope = await this.openScopeDialog('delete');
        if (!scope) return;

        this.deleting = true;
        try {
          const targetUuid = this._panelRaw.master_event_id || this.form.uuid;
          let url = `/api/v1/calendar/events/${targetUuid}?scope=${scope}`;
          if (scope !== 'all') {
            url += `&original_start=${encodeURIComponent(this._panelRaw.original_start)}`;
          }
          const resp = await fetch(url, {
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
        return;
      }

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

    // --- Keyboard shortcuts ---
    handleKeydown(e) {
      // Skip when typing in inputs, textareas, selects
      const tag = e.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      // Skip when a modal is open
      if (this.showModal || this.showCalendarModal || this.showPollListModal || this.showPollCreateModal || this.showPollDetailModal) return;
      // Don't intercept browser shortcuts (Ctrl/Cmd+key)
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      const key = e.key;

      // Navigation
      if (key === 'ArrowLeft') { e.preventDefault(); this.calendarPrev(); return; }
      if (key === 'ArrowRight') { e.preventDefault(); this.calendarNext(); return; }
      if (key === 't' || key === 'T') { e.preventDefault(); this.calendarToday(); return; }

      // Views
      if (key === 'm' || key === 'M') { e.preventDefault(); this.changeView('dayGridMonth'); return; }
      if (key === 'w' || key === 'W') { e.preventDefault(); this.changeView('timeGridWeek'); return; }
      if (key === 'd' || key === 'D') { e.preventDefault(); this.changeView('timeGridDay'); return; }
      if (key === 'a' || key === 'A') { e.preventDefault(); this.changeView('listWeek'); return; }

      // New event
      if (key === 'n' || key === 'N') {
        e.preventDefault();
        const now = new Date();
        const pad = n => String(n).padStart(2, '0');
        const dateStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
        const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
        const start = `${dateStr}T${timeStr}`;
        this.openCreateModal(start, this._addHour(now.toISOString()), false);
        return;
      }

      // Close panel
      if (key === 'Escape' && this.showPanel) { e.preventDefault(); this.closePanel(); return; }

      // Help
      if (key === '?') {
        e.preventDefault();
        const dlg = document.getElementById('calendar-help-dialog');
        if (dlg) { dlg.showModal(); lucide?.createIcons(); }
      }
    },

    // --- Context menu ---
    openContextMenu(nativeEvent, rawEvent) {
      const currentUserId = String(document.body.dataset.userId);
      const isOwner = String(rawEvent.owner.id) === currentUserId;
      const membership = (rawEvent.members || []).find(m => String(m.user.id) === currentUserId);
      const inviteStatus = (!isOwner && membership) ? membership.status : null;

      // Store event data in form for actions that need it
      this.ctxMenu.event = rawEvent;
      this.ctxMenu.isOwner = isOwner;
      this.ctxMenu.inviteStatus = inviteStatus;
      this.ctxMenu.open = true;

      // Position with viewport overflow detection
      this.$nextTick(() => {
        const menuEl = this.$el.querySelector('[x-show="ctxMenu.open"]');
        if (!menuEl) return;
        const menuRect = menuEl.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;

        let x = nativeEvent.clientX;
        let y = nativeEvent.clientY;

        if (x + menuRect.width > vw) x = vw - menuRect.width - 10;
        if (y + menuRect.height > vh) y = vh - menuRect.height - 10;

        this.ctxMenu.x = x;
        this.ctxMenu.y = y;

        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    ctxMenuAction(action) {
      const rawEvent = this.ctxMenu.event;
      this.ctxMenu.open = false;
      if (!rawEvent) return;

      switch (action) {
        case 'view':
          this.openViewPanel(rawEvent);
          break;
        case 'copy_link': {
          const url = new URL(window.location.origin + window.location.pathname);
          url.searchParams.set('event', rawEvent.uuid);
          navigator.clipboard.writeText(url.toString()).then(() => {
            if (window.AppAlert) window.AppAlert.success('Link copied to clipboard', { duration: 2000 });
          }).catch(() => {
            if (window.AppAlert) window.AppAlert.error('Failed to copy link');
          });
          break;
        }
        case 'edit':
          this.openViewPanel(rawEvent);
          this.$nextTick(() => this.openEditModal());
          break;
        case 'delete':
          this.openViewPanel(rawEvent);
          this.$nextTick(() => this.deleteEvent());
          break;
        case 'accept':
          // Load event into form state so respondToInvitation works
          this.openViewPanel(rawEvent);
          this.$nextTick(() => this.respondToInvitation('accepted'));
          break;
        case 'decline':
          this.openViewPanel(rawEvent);
          this.$nextTick(() => this.respondToInvitation('declined'));
          break;
      }
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

    // --- Recurrence ---
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
        this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      });
    },

    resolveScopeDialog(scope) {
      this.showScopeDialog = false;
      if (this.scopeResolve) {
        this.scopeResolve(scope);
        this.scopeResolve = null;
      }
    },

    closeModal() { this.showModal = false; },

    // --- Polls ---
    openPollList() {
      this.showPollListModal = true;
      this.loadPolls();
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    async loadPolls() {
      this.pollsLoading = true;
      try {
        const status = this.pollShowClosed ? 'all' : 'open';
        const resp = await fetch(`/api/v1/calendar/polls?filter=${this.pollFilter}&status=${status}`, { credentials: 'same-origin' });
        if (resp.ok) this.polls = await resp.json();
      } catch (e) {}
      this.pollsLoading = false;
    },

    setPollFilter(filter) {
      this.pollFilter = filter;
      this.loadPolls();
    },

    togglePollShowClosed() {
      this.pollShowClosed = !this.pollShowClosed;
      this.loadPolls();
    },

    openPollCreate() {
      this.showPollListModal = false;
      this.showPollCreateModal = true;
      this.pollForm = { title: '', description: '', slots: [{ start: '', end: '', showEnd: false }, { start: '', end: '', showEnd: false }] };
      this.pollFormError = null;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    addPollSlot() {
      this.pollForm.slots.push({ start: '', end: '', showEnd: false });
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    removePollSlot(i) {
      if (this.pollForm.slots.length > 2) {
        this.pollForm.slots.splice(i, 1);
        this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      }
    },

    async submitPoll() {
      if (!this.pollForm.title.trim()) return;
      const validSlots = this.pollForm.slots.filter(s => s.start);
      if (validSlots.length < 2) {
        this.pollFormError = 'At least 2 time slots with a start time are required.';
        return;
      }
      this.pollFormSubmitting = true;
      this.pollFormError = null;
      try {
        const resp = await fetch('/api/v1/calendar/polls', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
          body: JSON.stringify({
            title: this.pollForm.title.trim(),
            description: this.pollForm.description,
            slots: validSlots.map(s => ({
              start: new Date(s.start).toISOString(),
              end: s.end ? new Date(s.end).toISOString() : null,
            })),
          }),
        });
        if (resp.ok) {
          const created = await resp.json();
          this.showPollCreateModal = false;
          this.openPollDetail(created.uuid);
        } else {
          const data = await resp.json().catch(() => null);
          this.pollFormError = data?.detail || data?.slots?.[0] || 'Failed to create poll.';
        }
      } catch (e) {
        this.pollFormError = 'Network error. Please try again.';
      }
      this.pollFormSubmitting = false;
    },

    async openPollDetail(uuid) {
      this.showPollListModal = false;
      this.showPollDetailModal = true;
      this.currentPoll = null;
      this.currentPollLoading = true;
      this.pollMyVotes = {};
      this.pollFinalizeSlotId = null;
      this._setPollUrl(uuid);
      await this.loadPoll(uuid);
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    closePollDetail() {
      this.showPollDetailModal = false;
      this._setPollUrl(null);
    },

    _setPollUrl(pollId) {
      const params = new URLSearchParams(window.location.search);
      if (pollId) {
        params.set('poll', pollId);
      } else {
        params.delete('poll');
      }
      const qs = params.toString();
      const url = window.location.pathname + (qs ? '?' + qs : '');
      history.replaceState(null, '', url);
    },

    async loadPoll(uuid) {
      this.currentPollLoading = true;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${uuid}`, { credentials: 'same-origin' });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          // Pre-populate my votes
          const userId = String(document.body.dataset.userId);
          const myVotes = {};
          for (const vote of (this.currentPoll.votes || [])) {
            if (vote.user && String(vote.user.id) === userId) {
              myVotes[vote.slot_id] = vote.choice;
            }
          }
          this.pollMyVotes = myVotes;
          this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
        }
      } catch (e) {}
      this.currentPollLoading = false;
    },

    pollCycleVote(slotId) {
      const cycle = ['yes', 'maybe', 'no'];
      const current = this.pollMyVotes[slotId];
      const idx = cycle.indexOf(current);
      const next = cycle[(idx + 1) % cycle.length];
      this.pollMyVotes = { ...this.pollMyVotes, [slotId]: next };
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    async submitPollVotes() {
      if (!this.currentPoll) return;
      const votes = Object.entries(this.pollMyVotes).map(([slot_id, choice]) => ({ slot_id, choice }));
      if (votes.length === 0) return;
      this.pollSubmitting = true;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${this.currentPoll.uuid}/vote`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
          body: JSON.stringify({ votes }),
        });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          if (window.AppAlert) window.AppAlert.success('Votes saved!', { duration: 2000 });
          this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
        }
      } catch (e) {}
      this.pollSubmitting = false;
    },

    async finalizePoll() {
      if (!this.currentPoll || !this.pollFinalizeSlotId) return;
      this.pollSubmitting = true;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${this.currentPoll.uuid}/finalize`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
          body: JSON.stringify({ slot_id: this.pollFinalizeSlotId }),
        });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          if (this.calendar) this.calendar.refetchEvents();
          if (window.AppAlert) window.AppAlert.success('Poll finalized! Event created.', { duration: 3000 });
        }
      } catch (e) {}
      this.pollSubmitting = false;
    },

    async deletePoll(uuid) {
      const ok = await AppDialog.confirm({
        title: 'Delete poll',
        message: 'Delete this poll and all its votes?',
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${uuid}`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': this.csrfToken() },
        });
        if (resp.ok || resp.status === 204) {
          if (this.showPollDetailModal) {
            this.closePollDetail();
            this.openPollList();
          } else {
            this.loadPolls();
          }
        }
      } catch (e) {}
    },

    copyPollShareLink() {
      if (!this.currentPoll?.share_url) return;
      navigator.clipboard.writeText(this.currentPoll.share_url).then(() => {
        if (window.AppAlert) window.AppAlert.success('Share link copied!', { duration: 2000 });
      }).catch(() => {
        if (window.AppAlert) window.AppAlert.error('Failed to copy link');
      });
    },

    pollParticipants() {
      if (!this.currentPoll?.votes) return [];
      const map = new Map();
      for (const vote of this.currentPoll.votes) {
        const key = vote.user ? `u-${vote.user.id}` : `g-${vote.guest_name}`;
        if (!map.has(key)) {
          map.set(key, {
            key,
            name: vote.user ? vote.user.username : vote.guest_name,
            isUser: !!vote.user,
            userId: vote.user?.id,
            votes: {},
          });
        }
        map.get(key).votes[vote.slot_id] = vote.choice;
      }
      return Array.from(map.values());
    },

    pollVoteClass(choice) {
      if (choice === 'yes') return 'text-success';
      if (choice === 'maybe') return 'text-warning';
      if (choice === 'no') return 'text-error opacity-40';
      return 'text-base-content/20';
    },

    pollVoteIcon(choice) {
      if (choice === 'yes') return 'check-circle';
      if (choice === 'maybe') return 'help-circle';
      if (choice === 'no') return 'x-circle';
      return 'circle';
    },

    formatPollSlotDate(slot) {
      if (!slot?.start) return '';
      const d = new Date(slot.start);
      return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    },

    formatPollSlotTime(slot) {
      if (!slot?.start) return '';
      const d = new Date(slot.start);
      const startTime = this.prefs.timeFormat === '12h'
        ? d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
        : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
      if (!slot.end) return startTime;
      const e = new Date(slot.end);
      const endTime = this.prefs.timeFormat === '12h'
        ? e.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
        : e.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
      return `${startTime} – ${endTime}`;
    },

    async addPollInvitee(event) {
      const user = event.detail.user;
      if (!this.currentPoll) return;
      // Skip if already invited
      if ((this.currentPoll.invitees || []).find(i => i.user.id === user.id)) return;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${this.currentPoll.uuid}/invite`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
          body: JSON.stringify({ user_ids: [user.id] }),
        });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
        }
      } catch (e) {}
    },

    async removePollInvitee(userId) {
      if (!this.currentPoll) return;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${this.currentPoll.uuid}/invite`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken() },
          body: JSON.stringify({ user_ids: [userId] }),
        });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
        }
      } catch (e) {}
    },

    isPollCreator() {
      if (!this.currentPoll) return false;
      return String(this.currentPoll.created_by?.id) === String(document.body.dataset.userId);
    },
  };
};
