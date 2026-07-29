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

// Keep in sync with COLUMN_COLORS in settings.js (minus its "no color" entry).
var LABEL_COLORS = [
  '#ef4444',
  '#f97316',
  '#eab308',
  '#22c55e',
  '#3b82f6',
  '#a855f7',
];

function pickLabelColor(labels) {
  const counts = new Map(LABEL_COLORS.map((color) => [color, 0]));
  labels.forEach((label) => {
    if (counts.has(label.color)) {
      counts.set(label.color, counts.get(label.color) + 1);
    }
  });
  let best = LABEL_COLORS[0];
  counts.forEach((count, color) => {
    if (count < counts.get(best)) best = color;
  });
  return best;
}

// Combobox over the project's labels. allLabels/selectedUuids are getters so
// the parent's reactive state is read at call time. An empty createUrl hides
// the create row (non-admins); the server enforces admin regardless.
function labelSelector(eventName, allLabels, selectedUuids, createUrl) {
  return {
    query: '',
    results: [],
    showDropdown: false,
    highlight: -1,
    creating: false,
    createError: false,
    eventName: eventName,
    allLabels: allLabels,
    selectedUuids: selectedUuids,
    createUrl: createUrl || '',

    trimmedQuery() {
      return (this.query || '').trim();
    },

    available() {
      const selected = this.selectedUuids();
      return this.allLabels().filter((l) => !selected.includes(l.uuid));
    },

    exactMatch() {
      const needle = this.trimmedQuery().toLowerCase();
      return (
        this.allLabels().find((l) => l.name.toLowerCase() === needle) || null
      );
    },

    showCreate() {
      return (
        Boolean(this.createUrl) &&
        this.trimmedQuery() !== '' &&
        !this.exactMatch()
      );
    },

    searchLocal() {
      const needle = this.trimmedQuery().toLowerCase();
      this.results = this.available().filter((l) =>
        l.name.toLowerCase().includes(needle)
      );
      this.highlight = -1;
      this.createError = false;
      this.showDropdown = true;
    },

    handleKeydown(e) {
      const count = this.results.length + (this.showCreate() ? 1 : 0);
      const open = this.showDropdown && count > 0;
      if (e.key === 'ArrowDown' && open) {
        e.preventDefault();
        this.highlight = (this.highlight + 1) % count;
      } else if (e.key === 'ArrowUp' && open) {
        e.preventDefault();
        this.highlight = this.highlight <= 0 ? count - 1 : this.highlight - 1;
      } else if (e.key === 'Enter' && this.trimmedQuery()) {
        // Always swallow Enter while a label is being typed: the input sits
        // inside the task form and a fall-through would submit it.
        e.preventDefault();
        if (this.highlight >= 0 && this.highlight < this.results.length) {
          this.select(this.results[this.highlight]);
        } else if (this.highlight === this.results.length && this.showCreate()) {
          this.createLabel();
        } else {
          const exact = this.exactMatch();
          if (exact && this.available().some((l) => l.uuid === exact.uuid)) {
            this.select(exact);
          } else if (this.showCreate()) {
            this.createLabel();
          }
        }
      }
    },

    select(label) {
      window.dispatchEvent(
        new CustomEvent(this.eventName, { detail: { label: label } })
      );
      this.query = '';
      this.results = [];
      this.showDropdown = false;
      this.highlight = -1;
      this.createError = false;
    },

    async createLabel() {
      const name = this.trimmedQuery();
      if (!name || !this.createUrl || this.creating) return;
      this.creating = true;
      this.createError = false;
      try {
        const resp = await fetch(this.createUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          body: JSON.stringify({
            name: name,
            color: pickLabelColor(this.allLabels()),
          }),
        });
        if (!resp.ok) throw new Error('Create failed');
        const label = await resp.json();
        window.dispatchEvent(
          new CustomEvent('project-label-created', { detail: { label: label } })
        );
        this.select(label);
      } catch (e) {
        this.createError = true;
      } finally {
        this.creating = false;
      }
    },
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

    formAssigneeName(id) {
      const user = this.members.find((m) => m.id === id);
      return user ? user.username : 'Unknown user';
    },

    formUnassignedUsers() {
      return this.members.filter((m) => !this.form.assignees.includes(m.id));
    },

    addFormAssignee(user) {
      if (!this.form.assignees.includes(user.id)) this.form.assignees.push(user.id);
    },

    removeFormAssignee(id) {
      this.form.assignees = this.form.assignees.filter((v) => v !== id);
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
    users: [],
    assigneeNames: {},
    linkCopied: false,

    init() {
      this.data = JSON.parse(
        document.getElementById('task-panel-data').textContent
      );
      this.actions = JSON.parse(
        document.getElementById('task-panel-actions').textContent
      );
      // members-data lives on the page shell, not in the swapped panel, so
      // it survives alpine-ajax panel reloads.
      const membersEl = document.getElementById('members-data');
      this.users = membersEl ? JSON.parse(membersEl.textContent) : [];
      // Departed users can still be assigned: seed names from the task's own
      // assignee list first, then overlay the assignable users.
      (this.data.assignee_users || []).forEach((u) => {
        this.assigneeNames[u.id] = u.username;
      });
      this.users.forEach((u) => {
        this.assigneeNames[u.id] = u.username;
      });
    },

    assigneeName(id) {
      return this.assigneeNames[id] || 'Unknown user';
    },

    unassignedUsers() {
      return this.users.filter((u) => !this.data.assignees.includes(u.id));
    },

    addAssignee(user) {
      this.toggleMulti('assignees', user.id, true);
    },

    removeAssignee(id) {
      this.toggleMulti('assignees', id, false);
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
      let value = this.draft;
      // The title editor is a textarea (it has to wrap like the read
      // view), so pasted newlines are possible; the title itself is
      // single-line.
      if (field === 'title') value = value.replace(/\s*[\r\n]+\s*/g, ' ');
      if (value === this.data[field]) return;
      if (field === 'title' && !value.trim()) return;
      this.commitField(field, value);
    },

    autoGrowTitle(el) {
      el.style.height = 'auto';
      // The textarea is box-content sized, so height is the text block only.
      // scrollHeight is integer-rounded while the line height is fractional
      // (22.5px); snapping to whole lines keeps the box the exact height of
      // the read-mode h2 and avoids a 1px reflow of everything below.
      const style = getComputedStyle(el);
      const lineHeight = parseFloat(style.lineHeight);
      const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      const lines = Math.max(1, Math.round((el.scrollHeight - padding) / lineHeight));
      el.style.height = lines * lineHeight + 'px';
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

    copyLink(reference) {
      // taskParamUrl returns null when the URL already points at this task.
      const path =
        taskParamUrl(window.location.href, reference) ||
        window.location.pathname + window.location.search;
      const url = window.location.origin + path;
      navigator.clipboard.writeText(url).then(() => {
        this.linkCopied = true;
        setTimeout(() => {
          this.linkCopied = false;
        }, 1500);
      });
    },
  };
}

window.projectBoard = projectBoard;
window.taskPanel = taskPanel;
window.labelSelector = labelSelector;
window.projectBoardHelpers = {
  listOrder: listOrder,
  taskParamUrl: taskParamUrl,
  fieldAction: fieldAction,
  taskMatchesFilters: taskMatchesFilters,
  pickLabelColor: pickLabelColor,
};
