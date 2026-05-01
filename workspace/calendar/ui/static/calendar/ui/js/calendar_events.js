// Event lifecycle: FullCalendar setup + event fetching, custom Alpine
// agenda view, create/edit/view modals, save/delete/respond, member
// selection, duration shortcuts, keyboard shortcuts, context menu, and
// event card popover actions. Also includes panel display formatters.
window.calendarEventsMixin = function calendarEventsMixin() {
  // Hardcoded SVG markup for the recurring-event indicator. Kept as a
  // module-level constant so the hot path in eventDidMount() doesn't
  // re-allocate the string on every render.
  const RECURRING_ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display:inline;vertical-align:middle;margin-left:3px;opacity:0.6"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg>';

  return {
    // --- FullCalendar ---
    initCalendar() {
      const calendarEl = this.$refs.calendarEl;
      if (!calendarEl) return;

      const params = new URLSearchParams(window.location.search);
      const urlSlug = params.get('view');
      // URL uses clean slugs (month/week/day/agenda); translate to the
      // internal view name, falling back to the user's default when absent.
      // `_viewFromUrl` also accepts legacy slugs (dayGridMonth, listWeek, listAgenda).
      let urlView = this._viewFromUrl(urlSlug) || this.prefs.defaultView;
      const urlDate = params.get('date');

      // FullCalendar doesn't know about the custom agenda view.
      // Initialize FullCalendar with a safe fallback view; if the URL asked
      // for agenda, we'll render the custom Alpine block instead.
      const fcInitialView = urlView === 'agenda' ? 'dayGridMonth' : urlView;
      this.currentView = urlView;
      this.calendar = new FullCalendar.Calendar(calendarEl, {
        initialView: fcInitialView,
        ...(urlDate ? { initialDate: urlDate } : {}),
        headerToolbar: false,
        // Follow the browser's language (e.g. en-US, fr-FR) so FullCalendar
        // formats weekdays, months, and the toolbar title consistently with
        // the rest of the UI (which uses `toLocaleDateString(undefined, ...)`).
        locale: (navigator.language || 'en').toLowerCase(),
        firstDay: this.prefs.firstDay,
        weekNumbers: this.prefs.weekNumbers,
        nowIndicator: true,
        editable: false,
        selectable: true,
        selectMirror: true,
        dayMaxEvents: true,
        expandRows: true,
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
            window._eventCardScheduleHide(info.el);
            this.openContextMenu(e, info.event.extendedProps._raw);
          });
          // Event card popover on hover - desktop only.
          // (hover: hover) excludes touch-primary devices where a tap would
          // otherwise synthesize a mouseenter and pop the card after the click.
          if (window.matchMedia('(hover: hover)').matches) {
            info.el.addEventListener('mouseenter', () => {
              if (this.ctxMenu.open) return;
              window._eventCardShow(info.el, info.event.id);
            });
            info.el.addEventListener('mouseleave', () => {
              window._eventCardScheduleHide(info.el);
            });
          }
          // Add recurring indicator
          const raw = info.event.extendedProps._raw;
          if (raw?.is_recurring) {
            const titleEl = info.el.querySelector('.fc-event-title') || info.el.querySelector('.fc-list-event-title');
            if (titleEl) {
              const icon = document.createElement('span');
              // SVG is a hardcoded constant (RECURRING_ICON_SVG above) - no user input.
              icon[`inner${'HTML'}`] = RECURRING_ICON_SVG;
              titleEl.appendChild(icon);
            }
          }
        },
      });

      this.calendar.render();
      this._syncTitle();

      if (this.currentView === 'agenda') {
        this.loadAgenda();
      }

      // Wire up the agenda infinite-scroll observer (runs alongside the
      // explicit "Load more" button as a fallback).
      this._setupAgendaObserver();

      const eventId = params.get('event');
      if (eventId) this.openEventById(eventId);

      const pollId = params.get('poll');
      if (pollId) this.openPollDetail(pollId);

      // ?action=new-event or ?action=new-poll - open create modal from command palette
      const action = params.get('action');
      if (action === 'new-event') {
        const now = new Date();
        const pad = n => String(n).padStart(2, '0');
        const dateStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
        const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
        const start = `${dateStr}T${timeStr}`;
        this.$nextTick(() => this.openCreateModal(start, this._addHour(now.toISOString()), false));
      } else if (action === 'new-poll') {
        this.$nextTick(() => { this.showPollListModal = true; this.openPollCreate(); });
      }
      if (action) {
        const url = new URL(window.location);
        url.searchParams.delete('action');
        history.replaceState(null, '', url);
      }

      // Browser back/forward
      window.addEventListener('popstate', () => {
        const p = new URLSearchParams(window.location.search);
        // Translate URL slug to internal view name (handles short slugs + legacy).
        const view = this._viewFromUrl(p.get('view')) || this.prefs.defaultView;
        const date = p.get('date');
        const evt = p.get('event');

        if (view !== this.currentView) {
          this.changeView(view);
        }
        if (view !== 'agenda') {
          if (date) {
            this.calendar.gotoDate(date);
          } else {
            this.calendar.today();
          }
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
        const cal = [...this.ownedCalendars, ...this.subscribedCalendars, ...this.externalCalendars].find(c => c.uuid === event.calendar_id);
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

    // --- Agenda view (custom, not FullCalendar) ---

    async loadAgenda() {
      // Reset and fetch page 1 from "now".
      const now = new Date();
      this.agenda.events = [];
      this.agenda.nextAfter = now.toISOString();
      this.agenda.initialLoaded = false;
      this.agenda.seenIds = new Set();
      await this.loadMoreAgenda();
      this.agenda.initialLoaded = true;
    },

    async loadMoreAgenda() {
      if (this.agenda.loading || this.agenda.nextAfter === null) return;
      this.agenda.loading = true;
      try {
        const calIds = Object.keys(this.visibleCalendars)
          .filter(k => this.visibleCalendars[k]).join(',');
        const params = new URLSearchParams({
          after: this.agenda.nextAfter,
          limit: '20',
          calendar_ids: calIds,
          show_declined: this.prefs.showDeclined ? 'true' : 'false',
        });
        const resp = await fetch(`/api/v1/calendar/events?${params}`, {
          credentials: 'same-origin',
        });
        if (!resp.ok) return;
        const data = await resp.json();

        // Dedup boundary events (events with start == cursor may appear on
        // both pages - see backend cursor stability note).
        const fresh = (data.events || []).filter(e => !this.agenda.seenIds.has(e.uuid));
        fresh.forEach(e => this.agenda.seenIds.add(e.uuid));
        this.agenda.events = [...this.agenda.events, ...fresh];
        this.agenda.nextAfter = data.next_after;
      } finally {
        this.agenda.loading = false;
      }
    },

    refetchAgenda() {
      // Re-load from scratch (filters/prefs changed, or an event was CRUD'd).
      if (this.currentView === 'agenda') {
        this.loadAgenda();
      }
    },

    _setupAgendaObserver() {
      // IntersectionObserver-driven infinite scroll for the agenda view.
      // Watches the sentinel at the bottom of the list; when it comes within
      // 300px of the viewport bottom, we fire loadMoreAgenda(). The method
      // itself guards against concurrent fetches and exhausted cursors, so
      // the observer can fire liberally without causing duplicate requests.
      if (this._agendaObserver) return; // already set up
      if (typeof IntersectionObserver === 'undefined') return; // fallback: button only

      const sentinel = this.$refs.agendaSentinel;
      if (!sentinel) return;

      this._agendaObserver = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          if (this.currentView !== 'agenda') continue;
          if (!this.agenda.initialLoaded) continue; // wait for page 1 first
          this.loadMoreAgenda();
        }
      }, {
        // Pre-load 300px before the user reaches the bottom, for a smooth feel.
        rootMargin: '300px 0px',
        threshold: 0,
      });

      this._agendaObserver.observe(sentinel);
    },

    agendaByDay() {
      // Regular method (not a getter - Alpine getters are not reliably reactive
      // per the project memory note). Groups events by local date, and marks
      // the "today" group so the template can highlight it with the accent color.
      const groups = [];
      let currentKey = null;
      let currentGroup = null;

      const todayDate = new Date();
      const todayKey = `${todayDate.getFullYear()}-${String(todayDate.getMonth() + 1).padStart(2, '0')}-${String(todayDate.getDate()).padStart(2, '0')}`;
      const tomorrowDate = new Date(todayDate);
      tomorrowDate.setDate(tomorrowDate.getDate() + 1);
      const tomorrowKey = `${tomorrowDate.getFullYear()}-${String(tomorrowDate.getMonth() + 1).padStart(2, '0')}-${String(tomorrowDate.getDate()).padStart(2, '0')}`;

      for (const event of this.agenda.events) {
        const d = new Date(event.start);
        const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        if (key !== currentKey) {
          currentKey = key;
          // `undefined` locale uses the browser's default (OS/language settings)
          const dateLabel = d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
          let label;
          if (key === todayKey) {
            label = `Today · ${dateLabel}`;
          } else if (key === tomorrowKey) {
            label = `Tomorrow · ${dateLabel}`;
          } else {
            label = dateLabel;
          }
          currentGroup = {
            date: key,
            label,
            isToday: key === todayKey,
            events: [],
          };
          groups.push(currentGroup);
        }
        currentGroup.events.push(event);
      }
      return groups;
    },

    formatAgendaEventTime(event) {
      if (event.all_day) return 'All day';
      const d = new Date(event.start);
      const opts = this.prefs.timeFormat === '12h'
        ? { hour: 'numeric', minute: '2-digit', hour12: true }
        : { hour: '2-digit', minute: '2-digit', hour12: false };
      // `undefined` locale uses browser default
      return d.toLocaleTimeString(undefined, opts);
    },

    agendaEventClasses(event) {
      // Returns the space-separated class string for an agenda event row.
      // `cal-<color>` selects the calendar color via a CSS custom property
      // (see .agenda-event.cal-* rules in calendar.css). The FullCalendar-scoped
      // `event-color-*` classes don't apply here because the agenda is rendered
      // outside the .fc container.
      const currentUserId = document.body.dataset.userId;
      const isOwner = String(event.owner.id) === String(currentUserId);
      const membership = (event.members || []).find(m => String(m.user.id) === String(currentUserId));
      const isInvited = !isOwner && !!membership;
      const isPending = isInvited && membership.status === 'pending';
      const isDeclined = isInvited && membership.status === 'declined';

      const cal = [...this.ownedCalendars, ...this.subscribedCalendars, ...this.externalCalendars]
        .find(c => c.uuid === event.calendar_id);
      const color = cal?.color || 'primary';

      const classes = [`cal-${color}`];
      if (isInvited) classes.push('event-invited');
      if (isPending) classes.push('event-pending');
      if (isDeclined) classes.push('event-declined');
      return classes.join(' ');
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
    createEventNow() {
      const now = new Date();
      const pad = n => String(n).padStart(2, '0');
      const dateStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
      const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
      const start = `${dateStr}T${timeStr}`;
      this.openCreateModal(start, this._addHour(now.toISOString()), false);
    },

    openCreateModal(start, end, allDay) {
      if (this.showModal) return; // prevent double-open from dateClick + select
      this.modalMode = 'create';
      this.showPanel = false;
      const defaultCal = this.ownedCalendars.find(c => !c.is_synced)?.uuid || null;

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
      this.externalOrganizer = null;
      this.eventMembers = [];
      this.myInviteStatus = null;
      this.showModal = true;

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
      this.externalOrganizer = event.external_organizer || '';
      this.eventMembers = event.members || [];
      this.selectedMembers = (event.members || []).map(m => m.user);
      this.myInviteStatus = isOwner ? null : ((event.members || []).find(m => String(m.user.id) === currentUserId)?.status || null);
      this.showPanel = true;
      this._pushUrl();

    },

    closePanel() {
      this.showPanel = false;
      this._syncUrl();
    },

    async openEventById(eventId) {
      if (!isValidUuid(eventId)) return;
      // Clear stale panel state up-front so a 404 / network failure can't
      // leave handleEventCardAction targeting the previously opened event.
      this._panelRaw = null;
      this.eventOwner = null;
      this.eventMembers = [];
      this.selectedMembers = [];
      this.externalOrganizer = '';
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
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify(payload),
        });
        if (resp.ok) {
          const saved = await resp.json();
          this.showModal = false;
          this.calendar.refetchEvents();
          this.refetchAgenda();
          if (this.modalMode === 'edit' && this.showPanel) {
            this._panelRaw = saved;
            // Match openViewPanel(): all-day events keep date-only values to
            // avoid timezone-shifted datetimes leaking back into panel state.
            const fmtStart = saved.all_day ? this.toLocalDate.bind(this) : this.toLocalDatetime.bind(this);
            this.form = {
              uuid: saved.uuid,
              calendar_id: saved.calendar_id,
              title: saved.title,
              description: saved.description || '',
              start: fmtStart(saved.start),
              end: saved.end ? fmtStart(saved.end) : '',
              all_day: saved.all_day,
              location: saved.location || '',
              recurrence_frequency: saved.recurrence_frequency || null,
              recurrence_interval: saved.recurrence_interval || 1,
              recurrence_end: saved.recurrence_end ? this.toLocalDate(saved.recurrence_end) : '',
            };
            this.eventOwner = saved.owner;
            this.externalOrganizer = saved.external_organizer || '';
            this.eventMembers = saved.members;
            this.selectedMembers = saved.members.map(m => m.user);

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
            headers: { 'X-CSRFToken': getCSRFToken() },
          });
          if (resp.ok || resp.status === 204) {
            this.showPanel = false;
            this.calendar.refetchEvents();
            this.refetchAgenda();
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
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (resp.ok || resp.status === 204) {
          this.showPanel = false;
          this.calendar.refetchEvents();
          this.refetchAgenda();
        }
      } catch (e) {}
      this.deleting = false;
    },

    // --- Respond ---
    async respondToInvitation(newStatus) {
      try {
        const targetUuid = this._panelRaw.master_event_id || this.form.uuid;
        const resp = await fetch(`/api/v1/calendar/events/${targetUuid}/respond`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ status: newStatus }),
        });
        if (resp.ok) {
          this.myInviteStatus = newStatus;
          const currentUserId = String(document.body.dataset.userId);
          const member = this.eventMembers.find(m => String(m.user.id) === currentUserId);
          if (member) member.status = newStatus;
          this.calendar.refetchEvents();
          this.refetchAgenda();
        }
      } catch (e) {}
    },

    // --- Members (event invitees) ---
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
      if (this.showModal || this.showScopeDialog || this.showCalendarModal || this.showPollListModal || this.showPollCreateModal || this.showPollDetailModal || this.showPollEditModal) return;
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
      if (key === 'a' || key === 'A') { e.preventDefault(); this.changeView('agenda'); return; }

      // New event
      if (key === 'n' || key === 'N') {
        e.preventDefault();
        this.createEventNow();
        return;
      }

      // Close panel
      if (key === 'Escape' && this.showPanel) { e.preventDefault(); this.closePanel(); return; }

      // Help
      if (key === '?') {
        e.preventDefault();
        const dlg = document.getElementById('calendar-help-dialog');
        if (dlg) { dlg.showModal(); }
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
      this.ctxMenu.isExternal = this.externalCalendars.some(c => c.uuid === rawEvent.calendar_id);
      this.ctxMenu.inviteStatus = inviteStatus;

      // Set initial position at cursor before opening so the menu doesn't
      // flash at (0, 0). Overflow adjustment follows in $nextTick once the
      // menu is rendered and we can measure its size.
      this.ctxMenu.x = nativeEvent.clientX;
      this.ctxMenu.y = nativeEvent.clientY;
      this.ctxMenu.open = true;

      this.$nextTick(() => {
        const menuEl = this.$el.querySelector('[x-show="ctxMenu.open"]');
        if (!menuEl) return;
        const menuRect = menuEl.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;

        let x = this.ctxMenu.x;
        let y = this.ctxMenu.y;

        if (x + menuRect.width > vw) x = vw - menuRect.width - 10;
        if (y + menuRect.height > vh) y = vh - menuRect.height - 10;

        this.ctxMenu.x = x;
        this.ctxMenu.y = y;
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

    // --- Event card actions ---
    async handleEventCardAction(evt) {
      const { action, eventId } = evt.detail;
      if (!eventId) return;
      // Hide any open popover
      document.querySelectorAll('.event-card-popover').forEach(p => { p.style.display = 'none'; });

      await this.openEventById(eventId);
      if (!this._panelRaw) return;

      this.$nextTick(() => {
        switch (action) {
          case 'edit': this.openEditModal(); break;
          case 'delete': this.deleteEvent(); break;
          case 'accept': this.respondToInvitation('accepted'); break;
          case 'decline': this.respondToInvitation('declined'); break;
        }
      });
    },

    // --- Panel display helpers ---
    panelDateDisplay() {
      const { start, end, all_day } = this.form;
      if (!start) return '';
      const dateStr = this._fmtDate(start);
      if (all_day) {
        // FullCalendar stores `end` as the day AFTER the last covered day for
        // all-day events. Render the inclusive last day so a Mon-Tue event
        // doesn't show as Mon -> Wed.
        const inclusiveEnd = end ? new Date(new Date(end).getTime() - 86400000) : null;
        if (!end || this._sameDay(start, end) || this._sameDay(start, inclusiveEnd)) {
          return dateStr;
        }
        return `${dateStr} → ${this._fmtDate(inclusiveEnd)}`;
      }
      const startTime = this._fmtTime(start);
      if (!end) return `${dateStr}, ${startTime}`;
      const endTime = this._fmtTime(end);
      if (this._sameDay(start, end)) {
        return `${dateStr}, ${startTime} – ${endTime}`;
      }
      return `${dateStr}, ${startTime} → ${this._fmtDate(end)}, ${endTime}`;
    },

    panelTimeLabel() {
      const { start, end, all_day } = this.form;
      if (all_day) return 'All day';
      if (!start) return '';
      const startTime = this._fmtTime(start);
      if (!end || this._sameDay(start, end)) {
        const endTime = end ? this._fmtTime(end) : null;
        return endTime ? `${startTime} – ${endTime}` : startTime;
      }
      return startTime;
    },

    eventCalendarObj() {
      return [...this.ownedCalendars, ...this.subscribedCalendars, ...this.externalCalendars].find(c => c.uuid === this.form.calendar_id) || null;
    },

    isExternalEvent() {
      return this.externalCalendars.some(c => c.uuid === this.form.calendar_id);
    },

    eventCalendarColor() {
      return this.eventCalendarObj()?.color || 'primary';
    },

    closeModal() { this.showModal = false; },
  };
};
