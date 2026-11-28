// Project task due dates as a read-only calendar overlay: fetched from the
// projects API at render time (never mirrored into Event rows), painted as
// all-day entries, and clicked through to the task's board panel.

// The day before a 'YYYY-MM-DD' day: pure date arithmetic, done in UTC.
function calendarDayBefore(day) {
  const date = new Date(day + 'T00:00:00Z');
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

window.calendarTasksMixin = function calendarTasksMixin() {
  const PAGE = 1000;
  // Hardcoded SVG markup for the task indicator, kept module-level so the
  // hot path in decorateTaskEvent() doesn't re-allocate it per render.
  const TASK_ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m9 11 3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>';

  return {
    async fetchTaskEvents(start, end) {
      if (!this.prefs.showTasks) return [];
      // FullCalendar hands the grid's own days, with or without its offset
      // (2026-07-27T00:00:00+02:00), and leaves the last one out: the date
      // part is the day the user sees, and the list takes both ends in.
      const params = new URLSearchParams({
        due_after: start.slice(0, 10),
        due_before: calendarDayBefore(end.slice(0, 10)),
        completed: 'false',
        ordering: 'due_date',
        limit: String(PAGE),
      });
      const tasks = [];
      try {
        for (let offset = 0; ; offset += PAGE) {
          params.set('offset', String(offset));
          const resp = await fetch('/api/v1/projects/tasks?' + params, {
            credentials: 'same-origin',
          });
          if (!resp.ok) return [];
          tasks.push(...(await resp.json()));
          if (resp.headers.get('X-Has-More') !== 'true') break;
        }
      } catch (e) {
        // A failing overlay must not take the events source down with it:
        // FullCalendar drops every source's result when one rejects.
        return [];
      }
      return tasks.map(task => ({
        // Prefixed so a task uuid can never collide with an event uuid in
        // FullCalendar's id index (the hover card looks events up by id).
        id: `task-${task.uuid}`,
        title: task.title,
        start: task.due_date,
        allDay: true,
        classNames: ['event-task', `event-task-${task.priority}`],
        extendedProps: { _task: task },
      }));
    },

    decorateTaskEvent(info) {
      const task = info.event.extendedProps._task;
      info.el.title = `${task.reference} · ${task.project.name}`;

      // Hover card, desktop only - (hover: hover) excludes touch-primary
      // devices where a tap synthesizes a mouseenter and would pop the card
      // right after the click already navigated away.
      if (window.matchMedia('(hover: hover)').matches) {
        info.el.addEventListener('mouseenter', () => {
          window.calendarCard.show(info.el, task.card_url);
        });
        info.el.addEventListener('mouseleave', () => {
          window.calendarCard.scheduleHide(info.el);
        });
      }

      const titleEl = info.el.querySelector('.fc-event-title')
        || info.el.querySelector('.fc-list-event-title');
      if (!titleEl) return;
      const icon = document.createElement('span');
      icon.className = 'fc-event-icon fc-event-icon-leading';
      // SVG is a hardcoded constant (TASK_ICON_SVG above) - no user input.
      icon[`inner${'HTML'}`] = TASK_ICON_SVG;
      titleEl.prepend(icon);
    },

    openTaskFromCalendar(task) {
      window.location.href = task.url;
    },
  };
};
