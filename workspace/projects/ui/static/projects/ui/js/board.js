// Kanban board and backlog interactions for the projects module.
// Pure list helpers are exported on window.projectBoardHelpers for unit tests.

function listOrder(listEl) {
  return Array.from(listEl.querySelectorAll('[data-task-uuid]')).map(
    (el) => el.dataset.taskUuid
  );
}

function taskParamUrl(href, uuid) {
  const url = new URL(href);
  if (uuid) {
    if (url.searchParams.get('task') === uuid) return null;
    url.searchParams.set('task', uuid);
  } else {
    if (!url.searchParams.has('task')) return null;
    url.searchParams.delete('task');
  }
  return url.pathname + url.search;
}

function fieldAction(field) {
  const map = {
    title: 'edit',
    description: 'edit',
    priority: 'edit',
    status: 'move',
    due_date: 'set_due',
    assignees: 'assign',
    labels: 'set_labels',
  };
  return map[field] || 'edit';
}

function emptyTaskFilters() {
  return { q: '', assignee: '', label: '', priority: '' };
}

function taskMatchesFilters(dataset, filters) {
  const query = (filters.q || '').trim().toLowerCase();
  if (query && !(dataset.search || '').includes(query)) return false;
  if (filters.priority && dataset.priority !== filters.priority) return false;
  if (
    filters.label &&
    !(dataset.labels || '').split(' ').includes(filters.label)
  ) {
    return false;
  }
  if (filters.assignee) {
    const ids = (dataset.assignees || '').split(' ').filter(Boolean);
    if (filters.assignee === 'none') {
      if (ids.length) return false;
    } else if (!ids.includes(filters.assignee)) {
      return false;
    }
  }
  return true;
}

function emptyTaskForm() {
  return {
    title: '',
    description: '',
    status: '',
    priority: 'medium',
    due_date: '',
    assignees: [],
    labels: [],
  };
}

function projectBoard(config) {
  return {
    currentView: config.view || 'board',
    collapsed: localStorage.getItem('projectsSidebarCollapsed') === 'true',
    dragging: null,
    saving: false,
    statuses: [],
    members: [],
    labels: [],
    form: emptyTaskForm(),
    formError: '',
    panelTaskUuid: config.initialTask || null,
    _panelGeneration: 0,
    filters: emptyTaskFilters(),
    selected: [],

    init() {
      this.statuses = JSON.parse(
        document.getElementById('statuses-data').textContent
      );
      this.members = JSON.parse(
        document.getElementById('members-data').textContent
      );
      this.labels = JSON.parse(
        document.getElementById('labels-data').textContent
      );
    },

    isMobile() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },

    sidebarCollapsed() {
      return this.isMobile() ? false : this.collapsed;
    },

    toggleCollapse() {
      if (this.isMobile()) return;
      this.collapsed = !this.collapsed;
      localStorage.setItem('projectsSidebarCollapsed', this.collapsed);
    },

    _closeDrawerOnMobile() {
      if (this.isMobile()) {
        const toggle = document.getElementById('projects-drawer');
        if (toggle) toggle.checked = false;
      }
    },

    headers() {
      return {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCSRFToken(),
      };
    },

    onDragStart(event, uuid) {
      if (!config.writable) return;
      this.dragging = uuid;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', uuid);
    },

    onDragEnd() {
      this.dragging = null;
    },

    onDragOver(event) {
      if (!this.dragging) return;
      event.dataTransfer.dropEffect = 'move';
    },

    async onDrop(event, statusUuid) {
      const uuid = this.dragging;
      this.dragging = null;
      if (!uuid || !config.writable) return;
      const list = event.currentTarget.querySelector(
        '[data-column-list], [data-backlog-list]'
      );
      const card = document.querySelector('[data-task-uuid="' + uuid + '"]');
      if (!list || !card) return;
      // Optimistic DOM move (pinned.js precedent); the reorder endpoint is
      // idempotent, and any failure refreshes back to server truth.
      const targetCard = event.target.closest('[data-task-uuid]');
      if (targetCard && targetCard.dataset.taskUuid !== uuid) {
        list.insertBefore(card, targetCard);
      } else if (!targetCard) {
        list.appendChild(card);
      }
      await this.saveOrder(statusUuid, listOrder(list));
    },

    async saveOrder(statusUuid, order) {
      this.saving = true;
      try {
        const resp = await fetch(config.apiBase + '/tasks/reorder', {
          method: 'POST',
          headers: this.headers(),
          body: JSON.stringify({ status: statusUuid, order: order }),
        });
        if (!resp.ok) throw new Error('Reorder failed');
      } catch (e) {
        // On failure, refresh in finally restores server truth
      } finally {
        this.refresh();
        this.saving = false;
      }
    },

    refresh() {
      let url = config.projectBase;
      if (this.currentView === 'backlog') url += '/backlog';
      else if (this.currentView === 'settings') url += '/settings';
      else if (this.currentView !== 'overview') url += '/board';
      this.$ajax(url, { target: 'project-content' });
      // Board-level changes (drag moves, send-to-board, field edits) also
      // change the open task's panel content: reload it alongside so its
      // status, activity and metadata stay in sync with the cards.
      if (this.panelTaskUuid) this._loadPanel(this.panelTaskUuid);
    },

    taskVisible(dataset) {
      return taskMatchesFilters(dataset, this.filters);
    },

    filtersActive() {
      return Boolean(
        this.filters.q.trim() ||
          this.filters.assignee ||
          this.filters.label ||
          this.filters.priority
      );
    },

    clearFilters() {
      this.filters = emptyTaskFilters();
    },

    _visibleTaskEls(scope) {
      return Array.from(
        document.querySelectorAll(scope + ' [data-task-uuid]')
      ).filter((el) => taskMatchesFilters(el.dataset, this.filters));
    },

    columnCount(statusUuid, total) {
      if (!this.filtersActive()) return total;
      return this._visibleTaskEls('[data-status-uuid="' + statusUuid + '"]')
        .length;
    },

    backlogVisibleCount() {
      return this._visibleTaskEls('#backlog').length;
    },

    visibleBacklogUuids() {
      return this._visibleTaskEls('#backlog').map((el) => el.dataset.taskUuid);
    },

    isSelected(uuid) {
      return this.selected.includes(uuid);
    },

    toggleSelect(uuid) {
      this.selected = this.isSelected(uuid)
        ? this.selected.filter((u) => u !== uuid)
        : this.selected.concat(uuid);
    },

    clearSelection() {
      this.selected = [];
    },

    allVisibleSelected() {
      const visible = this.visibleBacklogUuids();
      return visible.length > 0 && visible.every((u) => this.isSelected(u));
    },

    toggleSelectAll() {
      // Only touch the rows matching the current filters: selections made
      // under another filter must survive a select-all/deselect-all here.
      const visible = this.visibleBacklogUuids();
      if (!visible.length) return;
      if (this.allVisibleSelected()) {
        this.selected = this.selected.filter((u) => !visible.includes(u));
      } else {
        this.selected = this.selected.concat(
          visible.filter((u) => !this.isSelected(u))
        );
      }
    },

    boardStatuses() {
      return this.statuses.filter((s) => s.category !== 'backlog');
    },

    async moveTasks(uuids, statusUuid) {
      if (!config.writable || !uuids.length || !statusUuid) return;
      try {
        const resp = await fetch(config.apiBase + '/tasks/move', {
          method: 'POST',
          headers: this.headers(),
          body: JSON.stringify({ status: statusUuid, tasks: uuids }),
        });
        if (!resp.ok) throw new Error('Move failed');
        this.selected = this.selected.filter((u) => !uuids.includes(u));
      } catch (e) {
        if (window.AppAlert) AppAlert.error('Could not move the tasks.');
      } finally {
        this.refresh();
      }
    },

    sendToBoard(uuid) {
      const firstActive = this.statuses.find((s) => s.category === 'active');
      if (!firstActive) return Promise.resolve();
      return this.moveTasks([uuid], firstActive.uuid);
    },

    sendSelected(statusUuid) {
      let target = statusUuid;
      if (!target) {
        const firstActive = this.statuses.find((s) => s.category === 'active');
        target = firstActive && firstActive.uuid;
      }
      return this.moveTasks(this.selected.slice(), target);
    },

    newTask(statusUuid) {
      if (!config.writable) return;
      this.form = emptyTaskForm();
      this.form.status = statusUuid;
      this.formError = '';
      this.$refs.taskDialog.showModal();
    },

    async openTask(uuid) {
      this.panelTaskUuid = uuid;
      const next = taskParamUrl(window.location.href, uuid);
      if (next) history.pushState(null, '', next);
      await this._loadPanel(uuid);
    },

    async _loadPanel(uuid) {
      // On network failure $ajax rejects outright. On a 4xx it resolves and fires
      // ajax:error, then throws a RenderError because the error page has no
      // #task-panel target, which is what lands us in this catch. Pinned to
      // alpine-ajax 0.12.6; a custom 404 template containing that id would
      // silently break this path.
      const generation = ++this._panelGeneration;
      try {
        await this.$ajax(config.projectBase + '/tasks/' + uuid + '/panel', {
          target: 'task-panel',
        });
      } catch (e) {
        // A load the user has already navigated away from must not alert
        // nor close the panel it lost the race to.
        if (generation !== this._panelGeneration) return;
        if (window.AppAlert) AppAlert.error('Could not load the task.');
        this.closePanel();
      }
    },

    closePanel() {
      this.panelTaskUuid = null;
      const next = taskParamUrl(window.location.href, null);
      if (next) history.replaceState(null, '', next);
    },

    onPopState() {
      const path = window.location.pathname;
      this.currentView = path.endsWith('/backlog')
        ? 'backlog'
        : path.endsWith('/board')
          ? 'board'
          : path.endsWith('/settings')
            ? 'settings'
            : 'overview';
      const task = new URL(window.location.href).searchParams.get('task');
      if (task && task !== this.panelTaskUuid) {
        this.panelTaskUuid = task;
        this._loadPanel(task);
      } else if (!task) {
        this.panelTaskUuid = null;
      }
    },

    ensureTaskParam() {
      // View-switch navigations push a URL without ?task=; restore it so
      // refresh and shared links keep pointing at the open panel.
      if (!this.panelTaskUuid) return;
      const next = taskParamUrl(window.location.href, this.panelTaskUuid);
      if (next) history.replaceState(null, '', next);
    },

    async patchTask(uuid, patch) {
      try {
        const resp = await fetch(config.apiBase + '/tasks/' + uuid, {
          method: 'PATCH',
          headers: this.headers(),
          body: JSON.stringify(patch),
        });
        if (!resp.ok) throw new Error('Save failed');
      } catch (e) {
        if (window.AppAlert) AppAlert.error('Could not save the task.');
      } finally {
        // Success or failure, re-render server truth: refresh swaps the
        // board cards and reloads whatever panel is open now (nothing if
        // the user closed it meanwhile). If the task vanished, _loadPanel
        // closes the panel (task-deleted-while-open case).
        this.refresh();
      }
    },

    async deletePanelTask(uuid, title) {
      const ok = await AppDialog.confirm({
        title: 'Delete task',
        message: 'Are you sure you want to delete "' + title + '"?',
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        const resp = await fetch(config.apiBase + '/tasks/' + uuid, {
          method: 'DELETE',
          headers: this.headers(),
        });
        if (!resp.ok) throw new Error('Delete failed');
        this.closePanel();
      } catch (e) {
        if (window.AppAlert) AppAlert.error('Could not delete the task.');
      } finally {
        this.refresh();
      }
    },

    async saveTask() {
      if (this.saving) return;
      this.saving = true;
      try {
        const resp = await fetch(config.apiBase + '/tasks', {
          method: 'POST',
          headers: this.headers(),
          body: JSON.stringify({
            title: this.form.title,
            description: this.form.description,
            status: this.form.status,
            priority: this.form.priority,
            due_date: this.form.due_date || null,
            assignees: this.form.assignees,
            labels: this.form.labels,
          }),
        });
        if (!resp.ok) {
          this.formError = 'Could not save the task.';
          return;
        }
        this.$refs.taskDialog.close();
        this.refresh();
      } catch (e) {
        this.formError = 'Could not save the task.';
      } finally {
        this.saving = false;
      }
    },
  };
}

function taskPanel() {
  return {
    data: {
      uuid: null,
      title: '',
      description: '',
      status: '',
      priority: 'medium',
      due_date: '',
      assignees: [],
      labels: [],
    },
    editing: null,
    draft: '',
    actions: [],

    init() {
      this.data = JSON.parse(
        document.getElementById('task-panel-data').textContent
      );
      this.actions = JSON.parse(
        document.getElementById('task-panel-actions').textContent
      );
    },

    can(actionId) {
      return this.actions.includes(actionId);
    },

    startEdit(field, value) {
      if (!this.can(fieldAction(field))) return;
      this.editing = field;
      this.draft = value;
    },

    cancelEdit() {
      this.editing = null;
    },

    commitDraft(field) {
      // Escape sets editing to null before blur fires; the guard makes
      // the trailing blur commit a no-op instead of an accidental save.
      if (this.editing !== field) return;
      this.editing = null;
      const value = this.draft;
      if (value === this.data[field]) return;
      if (field === 'title' && !value.trim()) return;
      this.commitField(field, value);
    },

    commitField(field, value) {
      if (!this.can(fieldAction(field))) return;
      this.patchTask(this.data.uuid, { [field]: value });
    },

    toggleMulti(field, id, checked) {
      const next = this.data[field].filter((v) => v !== id);
      if (checked) next.push(id);
      this.commitField(field, next);
    },

    removeTask() {
      if (!this.can('delete')) return;
      this.deletePanelTask(this.data.uuid, this.data.title);
    },
  };
}

window.projectBoard = projectBoard;
window.taskPanel = taskPanel;
window.projectBoardHelpers = {
  listOrder: listOrder,
  taskParamUrl: taskParamUrl,
  fieldAction: fieldAction,
  taskMatchesFilters: taskMatchesFilters,
};
