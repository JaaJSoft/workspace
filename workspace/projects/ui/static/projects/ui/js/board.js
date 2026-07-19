// Kanban board and backlog interactions for the projects module.
// Pure list helpers are exported on window.projectBoardHelpers for unit tests.

function listOrder(listEl) {
  return Array.from(listEl.querySelectorAll('[data-task-uuid]')).map(
    (el) => el.dataset.taskUuid
  );
}

function emptyTaskForm() {
  return {
    uuid: null,
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
    taskActions: [],
    _actionsGeneration: 0,

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
      const view = this.currentView === 'backlog' ? 'backlog' : 'board';
      this.$ajax(config.projectBase + '/' + view, { target: 'project-content' });
    },

    async sendToBoard(uuid) {
      if (!config.writable) return;
      const firstActive = this.statuses.find((s) => s.category === 'active');
      if (!firstActive) return;
      try {
        const resp = await fetch(config.apiBase + '/tasks/' + uuid, {
          method: 'PATCH',
          headers: this.headers(),
          body: JSON.stringify({ status: firstActive.uuid }),
        });
        if (!resp.ok) throw new Error('Send to board failed');
      } catch (e) {
        // Swallow: the finally refresh restores server truth, card stays in backlog
      } finally {
        this.refresh();
      }
    },

    newTask(statusUuid) {
      if (!config.writable) return;
      this.form = emptyTaskForm();
      this.form.status = statusUuid;
      this.formError = '';
      this.taskActions = ['edit', 'move', 'assign', 'set_due', 'set_labels'];
      this.$refs.taskDialog.showModal();
    },

    async openTask(uuid) {
      const generation = ++this._actionsGeneration;
      let data;
      try {
        const resp = await fetch(config.apiBase + '/tasks/' + uuid);
        if (!resp.ok) return;
        data = await resp.json();
      } catch (e) {
        // Modal is not open yet: nothing to display, silently keep the board.
        return;
      }
      if (generation !== this._actionsGeneration) return;
      this.form = {
        uuid: data.uuid,
        title: data.title,
        description: data.description,
        status: data.status,
        priority: data.priority,
        due_date: data.due_date || '',
        assignees: data.assignees.map(String),
        labels: data.labels.map(String),
      };
      this.formError = '';
      this.taskActions = [];
      this.$refs.taskDialog.showModal();
      this.fetchActions(uuid, generation);
    },

    async fetchActions(uuid, generation) {
      try {
        const resp = await fetch('/api/v1/projects/actions', {
          method: 'POST',
          headers: this.headers(),
          body: JSON.stringify({ uuids: [uuid] }),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        if (generation !== this._actionsGeneration) return;
        this.taskActions = (data[uuid] || []).map((a) => a.id);
      } catch (e) {
        // Leave taskActions empty: buttons stay disabled (fail-safe).
      }
    },

    can(actionId) {
      return this.taskActions.includes(actionId);
    },

    async saveTask() {
      if (this.saving) return;
      if (this.form.uuid && !this.can('edit')) return;
      const url = this.form.uuid
        ? config.apiBase + '/tasks/' + this.form.uuid
        : config.apiBase + '/tasks';
      this.saving = true;
      try {
        const resp = await fetch(url, {
          method: this.form.uuid ? 'PATCH' : 'POST',
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

    async deleteTask() {
      if (!this.form.uuid || !this.can('delete')) return;
      const ok = await AppDialog.confirm({
        title: 'Delete task',
        message: 'Are you sure you want to delete "' + this.form.title + '"?',
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        const resp = await fetch(config.apiBase + '/tasks/' + this.form.uuid, {
          method: 'DELETE',
          headers: this.headers(),
        });
        if (!resp.ok) {
          this.formError = 'Could not delete the task.';
          return;
        }
        this.$refs.taskDialog.close();
        this.refresh();
      } catch (e) {
        this.formError = 'Could not delete the task.';
      }
    },
  };
}

window.projectBoard = projectBoard;
window.projectBoardHelpers = { listOrder: listOrder };
