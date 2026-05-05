window.calendarApp = function calendarApp() {
  // Read server-rendered calendars from <script id="calendars-data" type="application/json">.
  let calendarsData = { owned: [], subscribed: [] };
  const calsEl = document.getElementById('calendars-data');
  if (calsEl) {
    try { calendarsData = JSON.parse(calsEl.textContent); } catch (e) {}
  }

  return {
    // ── State ────────────────────────────────────────────────
    calendar: null,
    currentView: 'dayGridMonth',
    currentTitle: '',

    // Preferences
    _prefsDefaults: { defaultView: 'dayGridMonth', firstDay: 1, weekNumbers: false, timeFormat: '24h', defaultAllDay: false, showDeclined: false, notifyPollVotes: true },
    prefs: { defaultView: 'dayGridMonth', firstDay: 1, weekNumbers: false, timeFormat: '24h', defaultAllDay: false, showDeclined: false, notifyPollVotes: true },

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

    // External calendars
    externalCalendars: [],
    showExternalModal: false,
    externalForm: { name: '', url: '', color: 'primary' },
    savingExternal: false,
    savingCalendar: false,
    syncingExternal: {},  // { [calendar_uuid]: true }

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
    externalOrganizer: null,
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
    ctxMenu: { open: false, x: 0, y: 0, event: null, isOwner: false, isExternal: false, inviteStatus: null },

    // Poll state
    showPollListModal: false,
    showPollCreateModal: false,
    showPollDetailModal: false,
    showPollEditModal: false,
    pollFilter: 'mine',    // 'mine' | 'shared'
    pollShowClosed: false,
    pollSearch: '',
    polls: [],
    pollsLoading: false,
    pollForm: { title: '', description: '', slots: [{ start: '', end: '', showEnd: false }, { start: '', end: '', showEnd: false }] },
    pollFormSubmitting: false,
    pollFormError: null,
    currentPoll: null,
    currentPollLoading: false,
    pollMyVotes: {},
    _loadPollRequestId: 0,
    pollSubmitting: false,
    pollFinalizeSlotId: null,

    // Agenda view state (custom list view, not FullCalendar)
    agenda: {
      events: [],
      nextAfter: null,
      loading: false,
      initialLoaded: false,
      seenIds: new Set(),
    },

    // Desktop hover capability (excludes touch-primary devices)
    _hasHover: window.matchMedia('(hover: hover)').matches,

    // ── Compose calendarApp from domain mixins ──────────────
    // Each mixin returns an object literal with its own methods, and we
    // spread them so they all share `this` at runtime. Order matters when
    // two mixins define the same key - later spreads override earlier ones.
    ...calendarCalendarsMixin(),
    ...calendarEventsMixin(),
    ...calendarRecurrenceMixin(),
    ...calendarPollsMixin(),

    // ── Init: orchestrates first paint and global listeners ─
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

      // Hydrate preferences from server-rendered JSON (embedded via Django's
      // |json_script filter). Avoids an extra GET /api/v1/settings/calendar/preferences
      // which would resolve after FC is already mounted with default firstDay/weekNumbers/view,
      // forcing a second render - and a second /api/v1/calendar/events fetch.
      const prefsEl = document.getElementById('calendar-prefs-data');
      if (prefsEl) {
        try {
          const prefsData = JSON.parse(prefsEl.textContent);
          if (prefsData && typeof prefsData === 'object') {
            this.prefs = { ...this._prefsDefaults, ...prefsData };
            // Migrate legacy view names: listWeek (original FC list view) and
            // listAgenda (intermediate rename) -> agenda (current name).
            if (this.prefs.defaultView === 'listWeek' || this.prefs.defaultView === 'listAgenda') {
              this.prefs.defaultView = 'agenda';
              this._savePrefs();
            }
          }
        } catch (e) {}
      }

      // Load external calendars
      this._loadExternalCalendars();

      window.matchMedia('(max-width: 1023px)').addEventListener('change', () => {
        if (this.calendar) this.$nextTick(() => this.calendar.updateSize());
      });

      this.$watch('showPanel', () => {
        setTimeout(() => { if (this.calendar) this.calendar.updateSize(); }, 250);
      });

      this.$nextTick(() => {
        this.initCalendar();
      });
    },

    // ── Preferences ─────────────────────────────────────────
    _prefsUrl: '/api/v1/settings/calendar/preferences',

    _savePrefs() {
      fetch(this._prefsUrl, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ value: this.prefs }),
      }).catch(() => {});
    },

    _timeFormatFC() {
      return this.prefs.timeFormat === '12h'
        ? { hour: 'numeric', minute: '2-digit', meridiem: 'short' }
        : { hour: '2-digit', minute: '2-digit', hour12: false };
    },

    updatePref(key, value) {
      this.prefs = { ...this.prefs, [key]: value };
      this._savePrefs();
      if (this.calendar) {
        if (key === 'defaultView') {
          // Route through changeView so that 'agenda' (custom Alpine view) is
          // handled without calling FullCalendar.changeView('agenda') which
          // would crash since FC doesn't know about the custom view.
          this.changeView(value);
        } else if (key === 'firstDay') {
          this.calendar.setOption('firstDay', value);
        } else if (key === 'weekNumbers') {
          this.calendar.setOption('weekNumbers', value);
        } else if (key === 'timeFormat') {
          this.calendar.setOption('eventTimeFormat', this._timeFormatFC());
          this.calendar.setOption('slotLabelFormat', this._timeFormatFC());
        } else if (key === 'showDeclined') {
          this.calendar.refetchEvents();
          this.refetchAgenda();
        }
      }
    },

    // ── Calendar visibility ─────────────────────────────────
    _showAllCalendars() {
      const obj = {};
      this.ownedCalendars.forEach(c => obj[c.uuid] = true);
      this.subscribedCalendars.forEach(c => obj[c.uuid] = true);
      this.externalCalendars.forEach(c => obj[c.uuid] = true);
      this.visibleCalendars = obj;
    },

    _saveVisibility() {
      const ids = Object.keys(this.visibleCalendars).filter(k => this.visibleCalendars[k]);
      localStorage.setItem('calendarVisible', JSON.stringify(ids));
    },

    isCalendarVisible(uuid) {
      return !!this.visibleCalendars[uuid];
    },

    // ── Sidebar ─────────────────────────────────────────────
    isMobile() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },

    sidebarCollapsed() {
      return this.isMobile() ? false : this.collapsed;
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
      this.refetchAgenda();
    },

    // ── View controls ───────────────────────────────────────
    calendarPrev() {
      if (this.currentView === 'agenda') return; // no-op in agenda
      if (this.calendar) { this.calendar.prev(); this._syncTitle(); this._syncUrl(); }
    },
    calendarNext() {
      if (this.currentView === 'agenda') return; // no-op in agenda
      if (this.calendar) { this.calendar.next(); this._syncTitle(); this._syncUrl(); }
    },
    calendarToday() {
      if (this.currentView === 'agenda') {
        this.loadAgenda(); // reset to "from now"
        this._syncTitle();
        this._syncUrl();
        return;
      }
      if (this.calendar) { this.calendar.today(); this._syncTitle(); this._syncUrl(); }
    },
    changeView(view) {
      if (!this.calendar) return;
      this.ctxMenu.open = false;

      if (view === 'agenda') {
        // Agenda is a custom Alpine view, not a FullCalendar view.
        this.currentView = 'agenda';
        if (this.agenda.events.length === 0) {
          this.loadAgenda();
        }
        this._syncTitle();
        this._syncUrl();
        return;
      }

      this.calendar.changeView(view);
      this.currentView = view;
      this.calendar.setOption('selectable', true);
      this._syncTitle();
      this._syncUrl();
    },
    _syncTitle() {
      if (this.currentView === 'agenda') {
        this.currentTitle = 'Agenda';
      } else if (this.calendar) {
        this.currentTitle = this.calendar.view.title;
      }
    },

    // ── URL state ───────────────────────────────────────────
    // Map internal view names (FullCalendar identifiers + our custom 'agenda')
    // to clean, user-facing URL slugs. Keeping the internal state as FC names
    // avoids touching every calendar.changeView(...) / view.type callsite.
    _viewToUrl(viewType) {
      return {
        dayGridMonth: 'month',
        timeGridWeek: 'week',
        timeGridDay:  'day',
        agenda:       'agenda',
      }[viewType] || viewType;
    },

    _viewFromUrl(slug) {
      // Also accepts the legacy FC names for bookmark compat.
      return {
        month:         'dayGridMonth',
        week:          'timeGridWeek',
        day:           'timeGridDay',
        agenda:        'agenda',
        // legacy
        dayGridMonth:  'dayGridMonth',
        timeGridWeek:  'timeGridWeek',
        timeGridDay:   'timeGridDay',
        listWeek:      'agenda',
        listAgenda:    'agenda',
      }[slug] || null;
    },

    _buildUrlParams() {
      // Always emit the current view in the URL, regardless of user preference.
      // (We used to hide the param when it matched prefs.defaultView, but that
      // made the URL lie about the current state and was confusing to debug.)
      const params = new URLSearchParams();
      const viewType = this.currentView;
      params.set('view', this._viewToUrl(viewType));
      // Store the current date as YYYY-MM-DD (skipped for agenda since it's always "from now")
      if (viewType !== 'agenda' && this.calendar) {
        const d = this.calendar.getDate();
        const dateStr = d.toISOString().split('T')[0];
        const today = new Date().toISOString().split('T')[0];
        if (dateStr !== today) params.set('date', dateStr);
      }
      if (this.showPanel && this.form.uuid) params.set('event', this.form.uuid);
      return params;
    },

    _syncUrl() {
      if (!this.calendar) return;
      const qs = this._buildUrlParams().toString();
      const url = window.location.pathname + (qs ? '?' + qs : '');
      history.replaceState(null, '', url);
    },

    _pushUrl() {
      if (!this.calendar) return;
      const qs = this._buildUrlParams().toString();
      const url = window.location.pathname + (qs ? '?' + qs : '');
      history.pushState(null, '', url);
    },

    // ── Date/time helpers ───────────────────────────────────
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

    // ── Display helpers (panel / agenda formatting) ─────────
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
      if (isToday) return `Today — ${datePart}`;
      if (isTomorrow) return `Tomorrow — ${datePart}`;
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
  };
};
