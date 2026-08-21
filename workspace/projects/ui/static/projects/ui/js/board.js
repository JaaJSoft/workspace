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
    estimate: 'edit',
    status: 'move',
    due_date: 'set_due',
    assignees: 'assign',
    labels: 'set_labels',
  };
  return map[field] || 'edit';
}

function emptyTaskFilters() {
  // assignee and label are multi-value (repeated query params, OR'd
  // server-side); assignee also accepts the literal 'none' for unassigned.
  return { q: '', assignee: [], label: [], priority: '', status: '' };
}

// Filtering is server-side; the filter state lives in the URL so a filtered
// view is shareable. These two helpers translate between the filters object
// and the query string, leaving non-filter params (the ?task= deep link)
// untouched.
function taskFiltersFromUrl(href) {
  const filters = emptyTaskFilters();
  const params = new URL(href).searchParams;
  Object.keys(filters).forEach((key) => {
    if (Array.isArray(filters[key])) {
      filters[key] = params.getAll(key).filter(Boolean);
    } else {
      const value = params.get(key);
      if (value) filters[key] = value;
    }
  });
  return filters;
}

function taskFilterUrl(href, filters) {
  const url = new URL(href);
  Object.entries(emptyTaskFilters()).forEach(([key, empty]) => {
    if (Array.isArray(empty)) {
      url.searchParams.delete(key);
      (filters[key] || []).forEach((value) =>
        url.searchParams.append(key, value)
      );
    } else {
      const value = (filters[key] || '').trim();
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
  });
  return url.pathname + url.search;
}

function emptyTaskForm() {
  return {
    title: '',
    description: '',
    status: '',
    priority: 'medium',
    due_date: '',
    estimate: '',
    assignees: [],
    labels: [],
  };
}

// The shared <tag-chip> palette, minus its "no color" entry: a new label
// always gets a color.
var LABEL_COLORS = window.TAG_CHIP_COLORS.map((c) => c.value).filter(Boolean);

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

// Menu-button behavior shared by the priority/status selector partials:
// opening focuses the checked item, arrows rove focus over the
// [role="menuitemradio"] rows, Escape hands focus back to the trigger.
function selectDropdown() {
  return {
    open: false,
    toggle() {
      this.open = !this.open;
      if (this.open) this.$nextTick(() => this.focusCurrent());
    },
    closeAndFocus() {
      this.open = false;
      this.$refs.trigger.focus();
    },
    options() {
      return Array.from(this.$root.querySelectorAll('[role="menuitemradio"]'));
    },
    focusCurrent() {
      const opts = this.options();
      const current = opts.find((o) => o.getAttribute('aria-checked') === 'true');
      (current || opts[0])?.focus();
    },
    move(step) {
      if (!this.open) {
        this.toggle();
        return;
      }
      const opts = this.options();
      if (!opts.length) return;
      const idx = opts.indexOf(document.activeElement);
      const next =
        idx === -1
          ? step > 0
            ? 0
            : opts.length - 1
          : (idx + step + opts.length) % opts.length;
      opts[next].focus();
    },
  };
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
        // inside the task form and a fall-through would submit it. Selecting
        // or creating only makes sense while the dropdown is actually open.
        e.preventDefault();
        if (!this.showDropdown) return;
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
      this.filters = taskFiltersFromUrl(window.location.href);

      // Catch up on board changes made elsewhere while the stream was down
      // (resumed tab, or a bfcache restore after a mobile back).
      window.addEventListener('sse:reconnect', () => this.refresh());
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

    handleKeydown(e) {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      if (e.target.isContentEditable) return;
      if (document.querySelector('dialog[open]')) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === '?') {
        e.preventDefault();
        const dlg = document.getElementById('projects-help-dialog');
        if (dlg) dlg.showModal();
      }
    },

    headers() {
      return {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCSRFToken(),
      };
    },

    onDragStart(event, uuid) {
      // Reordering a filtered subset would push every unlisted task of the
      // column after the visible ones server-side, so dragging is disabled
      // while filters narrow the list.
      if (!config.writable || this.filtersActive()) {
        event.preventDefault();
        return;
      }
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

    refreshContent() {
      let url = config.projectBase;
      if (this.currentView === 'backlog') url += '/backlog';
      else if (this.currentView === 'tasks') url += '/tasks';
      else if (this.currentView === 'settings') url += '/settings';
      else if (this.currentView === 'analytics') url += '/analytics';
      else if (this.currentView !== 'overview') url += '/board';
      // The active filters ride along so a refresh keeps the filtered view.
      this.$ajax(taskFilterUrl(window.location.origin + url, this.filters), {
        target: 'project-content',
      });
    },

    refresh() {
      this.refreshContent();
      // Board-level changes (drag moves, send-to-board, field edits) also
      // change the open task's panel content: reload it alongside so its
      // status, activity and metadata stay in sync with the cards.
      if (this.panelTaskUuid) this._loadPanel(this.panelTaskUuid);
    },

    applyFilters() {
      const next = taskFilterUrl(window.location.href, this.filters);
      history.replaceState(null, '', next);
      this.$ajax(next, { target: 'project-content' });
    },

    syncFiltersFromUrl() {
      // After any content swap the URL is the source of truth: a drawer
      // navigation carries no filter params, so this is what resets the
      // filter bar when the user switches views.
      this.filters = taskFiltersFromUrl(window.location.href);
    },

    filtersActive() {
      return Boolean(
        this.filters.q.trim() ||
          this.filters.assignee.length ||
          this.filters.label.length ||
          this.filters.priority ||
          this.filters.status
      );
    },

    clearFilters() {
      this.filters = emptyTaskFilters();
      this.applyFilters();
    },

    isAssigneeFilter(id) {
      return this.filters.assignee.includes(String(id));
    },

    toggleAssigneeFilter(id) {
      id = String(id);
      this.filters.assignee = this.isAssigneeFilter(id)
        ? this.filters.assignee.filter((v) => v !== id)
        : this.filters.assignee.concat(id);
      this.applyFilters();
    },

    addAssigneeFilter(user) {
      if (!this.isAssigneeFilter(user.id)) this.toggleAssigneeFilter(user.id);
    },

    removeAssigneeFilter(id) {
      if (this.isAssigneeFilter(id)) this.toggleAssigneeFilter(id);
    },

    // The 'none' pseudo-assignee is rendered by the Unassigned toggle, so
    // the chips row only carries real users.
    assigneeFilterChips() {
      return this.filters.assignee.filter((id) => id !== 'none');
    },

    unfilteredMembers() {
      return this.members.filter((m) => !this.isAssigneeFilter(m.id));
    },

    filterAssigneeName(id) {
      const user = this.members.find((m) => String(m.id) === String(id));
      return user ? user.username : 'Unknown user';
    },

    addLabelFilter(label) {
      if (!this.filters.label.includes(label.uuid)) {
        this.filters.label = this.filters.label.concat(label.uuid);
        this.applyFilters();
      }
    },

    removeLabelFilter(uuid) {
      this.filters.label = this.filters.label.filter((v) => v !== uuid);
      this.applyFilters();
    },

    visibleBacklogUuids() {
      // Rendered rows already match the active filters server-side.
      return Array.from(
        document.querySelectorAll('#backlog [data-task-uuid]')
      ).map((el) => el.dataset.taskUuid);
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
      this.filters = taskFiltersFromUrl(window.location.href);
      const path = window.location.pathname;
      this.currentView = path.endsWith('/backlog')
        ? 'backlog'
        : path.endsWith('/tasks')
          ? 'tasks'
          : path.endsWith('/board')
            ? 'board'
            : path.endsWith('/settings')
              ? 'settings'
              : path.endsWith('/analytics')
                ? 'analytics'
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

    labelById(uuid) {
      return this.labels.find((l) => l.uuid === uuid) || null;
    },

    labelName(uuid) {
      const label = this.labelById(uuid);
      return label ? label.name : 'Unknown label';
    },

    labelColor(uuid) {
      const label = this.labelById(uuid);
      return label && label.color ? label.color : '';
    },

    onLabelCreated(label) {
      if (!this.labels.some((l) => l.uuid === label.uuid)) {
        this.labels.push(label);
      }
    },

    addFormLabel(label) {
      if (!this.form.labels.includes(label.uuid)) {
        this.form.labels.push(label.uuid);
      }
    },

    removeFormLabel(uuid) {
      this.form.labels = this.form.labels.filter((v) => v !== uuid);
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
            // '' means unestimated; '0' is a real estimate, hence no ||.
            estimate: this.form.estimate === '' ? null : this.form.estimate,
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
    ...window.attachmentInputMixin({
      pickerMessage: 'Select files to attach to the task.',
    }),
    data: {
      uuid: null,
      title: '',
      description: '',
      status: '',
      priority: 'medium',
      due_date: '',
      estimate: '',
      assignees: [],
      labels: [],
    },
    editing: null,
    draft: '',
    actions: [],
    users: [],
    assigneeNames: {},
    linkCopied: false,
    subtasks: [],
    newSubtask: '',
    savingSubtask: false,
    editingSubtask: null,
    subtaskDraft: '',
    draggingSubtask: null,
    attachments: [],
    _attachmentsUrl: '',
    _attachmentsSaving: false,
    links: [],
    linkRel: 'blocks',
    linkQuery: '',
    linkResults: [],
    linkDropdown: false,
    linkSaving: false,
    _linkSearchGeneration: 0,

    init() {
      this.data = JSON.parse(
        document.getElementById('task-panel-data').textContent
      );
      this.subtasks = this.data.subtasks || [];
      this.actions = JSON.parse(
        document.getElementById('task-panel-actions').textContent
      );
      this._attachmentsUrl = this.$el.dataset.attachmentsUrl;
      const attachmentsEl = document.getElementById('task-panel-attachments');
      this.attachments = attachmentsEl
        ? JSON.parse(attachmentsEl.textContent)
        : [];
      const linksEl = document.getElementById('task-panel-links');
      this.links = linksEl ? JSON.parse(linksEl.textContent) : [];
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
      // The shared project label list lives on the board shell and is loaded
      // once; refresh it from the panel's server-rendered copy so labels
      // created since page load resolve to names and colors. this.labels is
      // the parent projectBoard's array via Alpine's scope chain; splice
      // keeps the same reactive array.
      const labelsEl = document.getElementById('panel-labels-data');
      if (labelsEl && Array.isArray(this.labels)) {
        const fresh = JSON.parse(labelsEl.textContent);
        this.labels.splice(0, this.labels.length, ...fresh);
      }
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

    addLabel(label) {
      this.toggleMulti('labels', label.uuid, true);
    },

    removeLabel(uuid) {
      this.toggleMulti('labels', uuid, false);
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

    // ── Checklist ─────────────────────────────────────────
    // Mutations keep the checklist in local state and only re-render the
    // board content (refreshContent, for the card counters) instead of
    // reloading the whole panel: a panel reload would steal focus from the
    // add input between two quick entries.

    subtasksDone() {
      return this.subtasks.filter((s) => s.done).length;
    },

    subtaskUrl(uuid) {
      return this.data.subtasks_url + '/' + uuid;
    },

    async addSubtask() {
      const title = this.newSubtask.trim();
      if (!title || !this.can('edit') || this.savingSubtask) return;
      this.savingSubtask = true;
      try {
        const resp = await fetch(this.data.subtasks_url, {
          method: 'POST',
          headers: this.headers(),
          body: JSON.stringify({ title: title }),
        });
        if (!resp.ok) throw new Error('Create failed');
        this.subtasks.push(await resp.json());
        this.newSubtask = '';
        this.refreshContent();
      } catch (e) {
        if (window.AppAlert) AppAlert.error('Could not add the item.');
      } finally {
        this.savingSubtask = false;
      }
    },

    async toggleSubtask(st) {
      if (!this.can('edit')) return;
      st.done = !st.done;
      try {
        const resp = await fetch(this.subtaskUrl(st.uuid), {
          method: 'PATCH',
          headers: this.headers(),
          body: JSON.stringify({ done: st.done }),
        });
        if (!resp.ok) throw new Error('Save failed');
        this.refreshContent();
      } catch (e) {
        st.done = !st.done;
        if (window.AppAlert) AppAlert.error('Could not save the item.');
      }
    },

    startSubtaskEdit(st) {
      if (!this.can('edit')) return;
      this.editingSubtask = st.uuid;
      this.subtaskDraft = st.title;
    },

    async commitSubtaskEdit(st) {
      // Escape clears editingSubtask before blur fires; the guard makes
      // the trailing blur commit a no-op (same shape as commitDraft).
      if (this.editingSubtask !== st.uuid) return;
      this.editingSubtask = null;
      const title = this.subtaskDraft.trim();
      if (!title || title === st.title) return;
      const previous = st.title;
      st.title = title;
      try {
        const resp = await fetch(this.subtaskUrl(st.uuid), {
          method: 'PATCH',
          headers: this.headers(),
          body: JSON.stringify({ title: title }),
        });
        if (!resp.ok) throw new Error('Save failed');
      } catch (e) {
        st.title = previous;
        if (window.AppAlert) AppAlert.error('Could not save the item.');
      }
    },

    async removeSubtask(st) {
      if (!this.can('edit')) return;
      try {
        const resp = await fetch(this.subtaskUrl(st.uuid), {
          method: 'DELETE',
          headers: this.headers(),
        });
        if (!resp.ok) throw new Error('Delete failed');
        this.subtasks = this.subtasks.filter((s) => s.uuid !== st.uuid);
        this.refreshContent();
      } catch (e) {
        if (window.AppAlert) AppAlert.error('Could not remove the item.');
      }
    },

    onSubtaskDragStart(event, uuid) {
      if (!this.can('edit')) {
        event.preventDefault();
        return;
      }
      this.draggingSubtask = uuid;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', uuid);
    },

    onSubtaskDragOver(event) {
      if (!this.draggingSubtask) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
    },

    onSubtaskDrop(event) {
      const uuid = this.draggingSubtask;
      this.draggingSubtask = null;
      if (!uuid) return;
      event.preventDefault();
      const target = event.target.closest('[data-subtask-uuid]');
      const targetUuid = target ? target.dataset.subtaskUuid : null;
      if (targetUuid === uuid) return;
      // Optimistic local reorder (board onDrop precedent); the endpoint is
      // idempotent, and any failure refreshes back to server truth.
      const items = this.subtasks.slice();
      const from = items.findIndex((s) => s.uuid === uuid);
      if (from === -1) return;
      const moved = items.splice(from, 1)[0];
      const to = targetUuid
        ? items.findIndex((s) => s.uuid === targetUuid)
        : items.length;
      items.splice(to === -1 ? items.length : to, 0, moved);
      this.subtasks = items;
      this.saveSubtaskOrder();
    },

    async saveSubtaskOrder() {
      try {
        const resp = await fetch(this.data.subtasks_url + '/reorder', {
          method: 'POST',
          headers: this.headers(),
          body: JSON.stringify({ order: this.subtasks.map((s) => s.uuid) }),
        });
        if (!resp.ok) throw new Error('Reorder failed');
      } catch (e) {
        if (window.AppAlert) AppAlert.error('Could not reorder the checklist.');
        this.refresh();
      }
    },

    // ── Attachments ──────────────────────────────────────────
    // Immediate mode: staged files (local uploads and picked workspace
    // files alike) are flushed to the server as soon as they land.
    attachmentsAdded() {
      this.flushAttachments();
    },

    async flushAttachments() {
      if (!this.can('attach')) {
        this.clearAttachments();
        return;
      }
      if (this._attachmentsSaving || !this.hasPendingAttachments()) return;
      this._attachmentsSaving = true;
      const formData = new FormData();
      this.appendAttachmentsTo(formData);
      try {
        const resp = await fetch(this._attachmentsUrl, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
          body: formData,
        });
        if (!resp.ok) throw new Error('attach failed');
        const data = await resp.json();
        this.attachments = data.attachments;
      } catch (e) {
        if (window.AppAlert) AppAlert.error('Could not attach the files.');
      } finally {
        this.clearAttachments();
        this._attachmentsSaving = false;
        // Anything staged while the request was in flight.
        if (this.hasPendingAttachments()) this.flushAttachments();
      }
    },

    async unlinkAttachment(att) {
      if (!this.can('attach')) return;
      try {
        const resp = await fetch(`${this._attachmentsUrl}/${att.uuid}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (!resp.ok && resp.status !== 404) throw new Error('unlink failed');
        this.attachments = this.attachments.filter((a) => a.uuid !== att.uuid);
      } catch (e) {
        if (window.AppAlert) AppAlert.error('Could not remove the attachment.');
      }
    },

    openAttachment(att) {
      window.dispatchEvent(
        new CustomEvent('open-file-viewer', {
          detail: {
            uuid: att.file.uuid,
            name: att.file.name,
            type: att.file.type,
          },
        })
      );
    },

    async searchLinkTasks() {
      const query = this.linkQuery.trim();
      // Race protection (_loadGeneration precedent): a slow response must
      // not overwrite the results of a newer query. Claimed before the
      // empty-query return so clearing the input also invalidates an
      // in-flight search - its late response must not reopen the dropdown.
      const generation = ++this._linkSearchGeneration;
      if (!query) {
        this.linkDropdown = false;
        this.linkResults = [];
        return;
      }
      try {
        const resp = await fetch(
          this.data.link_search_url +
            '?q=' +
            encodeURIComponent(query) +
            '&exclude=' +
            this.data.uuid
        );
        if (!resp.ok) throw new Error('Search failed');
        const results = await resp.json();
        if (generation !== this._linkSearchGeneration) return;
        const linked = new Set(this.links.map((l) => l.task.uuid));
        this.linkResults = results.filter((r) => !linked.has(r.uuid));
        this.linkDropdown = true;
      } catch (e) {
        if (generation !== this._linkSearchGeneration) return;
        this.linkDropdown = false;
      }
    },

    async addLink(result) {
      if (!this.can('link') || this.linkSaving) return;
      this.linkSaving = true;
      try {
        const resp = await fetch(this.data.links_url, {
          method: 'POST',
          headers: this.headers(),
          body: JSON.stringify({ target: result.uuid, relation: this.linkRel }),
        });
        if (!resp.ok) {
          // Rule violations (cycle, duplicate) come back as a curated
          // detail message worth surfacing verbatim.
          const body = await resp.json().catch(() => ({}));
          throw new Error(body.detail || 'Could not link the tasks.');
        }
        this.links = await resp.json();
        this.linkQuery = '';
        this.linkResults = [];
        this.linkDropdown = false;
        // Blocked badges on the board may have changed.
        this.refresh();
      } catch (e) {
        if (window.AppAlert) {
          AppAlert.error(e.message || 'Could not link the tasks.');
        }
      } finally {
        this.linkSaving = false;
      }
    },

    async removeLink(uuid) {
      if (!this.can('link')) return;
      try {
        const resp = await fetch(this.data.links_url + '/' + uuid, {
          method: 'DELETE',
          headers: this.headers(),
        });
        if (!resp.ok) throw new Error('Unlink failed');
        this.links = this.links.filter((l) => l.uuid !== uuid);
      } catch (e) {
        if (window.AppAlert) AppAlert.error('Could not remove the link.');
      } finally {
        this.refresh();
      }
    },

    openLinkedTask(task) {
      if (task.project === this.data.project) {
        this.openTask(task.uuid);
      } else {
        // Cross-project: the other board owns the panel context.
        window.location.assign(task.url);
      }
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
window.selectDropdown = selectDropdown;
window.projectBoardHelpers = {
  listOrder: listOrder,
  taskParamUrl: taskParamUrl,
  fieldAction: fieldAction,
  taskFiltersFromUrl: taskFiltersFromUrl,
  taskFilterUrl: taskFilterUrl,
  pickLabelColor: pickLabelColor,
};
