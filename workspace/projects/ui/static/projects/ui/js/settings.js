// Settings page components for the projects module. Each section is an
// independent Alpine component; pure helpers are exported on
// window.projectSettingsHelpers for unit tests.

function settingsHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-CSRFToken': getCSRFToken(),
  };
}

// Preselect target for "delete column": nearest other column of the same
// category (previous sibling wins over next), else null.
function defaultMoveTarget(columns, uuid) {
  const index = columns.findIndex(function (c) {
    return c.uuid === uuid;
  });
  if (index === -1) return null;
  const category = columns[index].category;
  for (let i = index - 1; i >= 0; i--) {
    if (columns[i].category === category) return columns[i].uuid;
  }
  for (let i = index + 1; i < columns.length; i++) {
    if (columns[i].category === category) return columns[i].uuid;
  }
  return null;
}

function projectSettingsGeneral(config) {
  return {
    name: '',
    description: '',
    group: null,
    groups: [],
    saving: false,
    saved: false,
    error: '',
    writable: config.writable !== false,

    init() {
      const data = JSON.parse(
        document.getElementById('project-settings-data').textContent
      );
      this.name = data.name;
      this.description = data.description;
      this.group = data.group;
      if (config.showGroup) this.loadGroups();
    },

    async loadGroups() {
      try {
        const resp = await fetch('/api/v1/users/groups');
        if (resp.ok) this.groups = await resp.json();
      } catch (e) {
        // Selector simply stays empty; saving without touching the
        // group keeps the current value.
      }
    },

    async save() {
      this.saving = true;
      this.error = '';
      this.saved = false;
      try {
        const body = { name: this.name, description: this.description };
        if (config.showGroup) body.group = this.group || null;
        const resp = await fetch(config.apiBase, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify(body),
        });
        if (!resp.ok) {
          const data = await resp.json().catch(function () {
            return {};
          });
          throw new Error(
            data.detail || (data.name && data.name[0]) || 'Could not save.'
          );
        }
        this.saved = true;
      } catch (e) {
        this.error = e.message;
      } finally {
        this.saving = false;
      }
    },
  };
}

function projectSettingsDanger(config) {
  return {
    busy: false,
    error: '',

    async archive(action) {
      this.busy = true;
      this.error = '';
      try {
        const resp = await fetch(config.apiBase + '/' + action, {
          method: 'POST',
          headers: settingsHeaders(),
        });
        if (!resp.ok) throw new Error('Could not ' + action + ' the project.');
        window.location.reload();
      } catch (e) {
        this.error = e.message;
        this.busy = false;
      }
    },

    async destroy() {
      const ok = await AppDialog.confirm({
        title: 'Delete project',
        message:
          '"' +
          config.projectName +
          '" and all of its tasks will be permanently deleted.',
        okLabel: 'Delete project',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      this.busy = true;
      this.error = '';
      try {
        const resp = await fetch(config.apiBase, {
          method: 'DELETE',
          headers: settingsHeaders(),
        });
        if (!resp.ok) throw new Error('Could not delete the project.');
        window.location.href = '/projects';
      } catch (e) {
        this.error = e.message;
        this.busy = false;
      }
    },
  };
}

const COLUMN_COLORS = ['', '#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#a855f7'];

function projectColumns(config) {
  return {
    columns: [],
    colors: COLUMN_COLORS,
    draggingColumn: null,
    adding: false,
    addForm: { name: '', category: 'active', color: '' },
    editing: null,
    editName: '',
    busy: false,
    error: '',

    init() {
      this.columns = JSON.parse(
        document.getElementById('columns-settings-data').textContent
      );
    },

    // Keeps the task modal's status select in sync: `statuses` lives on
    // the parent projectBoard scope.
    syncBoardStatuses() {
      this.statuses = this.columns.map(function (c) {
        return { uuid: c.uuid, name: c.name, category: c.category };
      });
    },

    async request(url, options) {
      this.busy = true;
      this.error = '';
      try {
        const resp = await fetch(url, options);
        if (!resp.ok) {
          const data = await resp.json().catch(function () {
            return {};
          });
          throw new Error(
            data.detail || (data.name && data.name[0]) || 'Request failed.'
          );
        }
        return resp;
      } finally {
        this.busy = false;
      }
    },

    async addColumn() {
      try {
        const resp = await this.request(config.apiBase + '/statuses', {
          method: 'POST',
          headers: settingsHeaders(),
          body: JSON.stringify(this.addForm),
        });
        const created = await resp.json();
        created.task_count = 0;
        this.columns.push(created);
        this.adding = false;
        this.addForm = { name: '', category: 'active', color: '' };
        this.syncBoardStatuses();
      } catch (e) {
        this.error = e.message;
      }
    },

    startEdit(column) {
      this.editing = column.uuid;
      this.editName = column.name;
    },

    async saveEdit(column) {
      if (this.editing !== column.uuid) return;
      if (!this.editName.trim() || this.editName === column.name) {
        this.editing = null;
        return;
      }
      try {
        await this.request(config.apiBase + '/statuses/' + column.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ name: this.editName }),
        });
        column.name = this.editName;
        this.editing = null;
        this.syncBoardStatuses();
      } catch (e) {
        this.error = e.message;
      }
    },

    async setColor(column, color) {
      try {
        await this.request(config.apiBase + '/statuses/' + column.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ color: color }),
        });
        column.color = color;
      } catch (e) {
        this.error = e.message;
      }
    },

    onColumnDragStart(event, uuid) {
      this.draggingColumn = uuid;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', uuid);
    },

    async onColumnDrop(event, targetUuid) {
      const uuid = this.draggingColumn;
      this.draggingColumn = null;
      if (!uuid || uuid === targetUuid) return;
      const from = this.columns.findIndex(function (c) {
        return c.uuid === uuid;
      });
      const to = this.columns.findIndex(function (c) {
        return c.uuid === targetUuid;
      });
      if (from === -1 || to === -1) return;
      const previous = this.columns.slice();
      this.columns.splice(to, 0, this.columns.splice(from, 1)[0]);
      try {
        await this.request(config.apiBase + '/statuses/reorder', {
          method: 'POST',
          headers: settingsHeaders(),
          body: JSON.stringify({
            order: this.columns.map(function (c) {
              return c.uuid;
            }),
          }),
        });
        this.syncBoardStatuses();
      } catch (e) {
        this.error = e.message;
        this.columns = previous;
      }
    },

    async deleteColumn(column) {
      let moveTarget = null;
      if (column.task_count > 0) {
        const others = this.columns.filter(function (c) {
          return c.uuid !== column.uuid;
        });
        moveTarget = await AppDialog.select({
          title: 'Delete column',
          message:
            column.task_count +
            ' task' +
            (column.task_count === 1 ? '' : 's') +
            ' will move to:',
          options: others.map(function (c) {
            return { value: c.uuid, label: c.name };
          }),
          value: defaultMoveTarget(this.columns, column.uuid) || others[0].uuid,
          okLabel: 'Delete',
          okClass: 'btn-error',
          icon: 'trash-2',
          iconClass: 'bg-error/10 text-error',
        });
        if (moveTarget === null) return;
      } else {
        const ok = await AppDialog.confirm({
          title: 'Delete column',
          message: 'Delete the empty column "' + column.name + '"?',
          okLabel: 'Delete',
          okClass: 'btn-error',
          icon: 'trash-2',
          iconClass: 'bg-error/10 text-error',
        });
        if (!ok) return;
      }
      let url = config.apiBase + '/statuses/' + column.uuid;
      if (moveTarget) {
        url += '?move_to=' + moveTarget;
      }
      try {
        await this.request(url, {
          method: 'DELETE',
          headers: settingsHeaders(),
        });
        const target = this.columns.find(function (c) {
          return c.uuid === moveTarget;
        });
        if (target) target.task_count += column.task_count;
        this.columns = this.columns.filter(function (c) {
          return c.uuid !== column.uuid;
        });
        this.syncBoardStatuses();
      } catch (e) {
        this.error = e.message;
      }
    },
  };
}

function projectLabels(config) {
  return {
    items: [],
    colors: COLUMN_COLORS,
    adding: false,
    addForm: { name: '', color: '' },
    editing: null,
    editName: '',
    busy: false,
    error: '',

    async init() {
      try {
        const resp = await fetch(config.apiBase + '/labels');
        if (resp.ok) this.items = await resp.json();
      } catch (e) {
        this.error = 'Could not load labels.';
      }
    },

    syncBoardLabels() {
      this.labels = this.items.map(function (l) {
        return { uuid: l.uuid, name: l.name, color: l.color };
      });
    },

    async request(url, options) {
      this.busy = true;
      this.error = '';
      try {
        const resp = await fetch(url, options);
        if (!resp.ok) {
          const data = await resp.json().catch(function () {
            return {};
          });
          throw new Error(
            data.detail || (data.name && data.name[0]) || 'Request failed.'
          );
        }
        return resp;
      } finally {
        this.busy = false;
      }
    },

    async addLabel() {
      try {
        const resp = await this.request(config.apiBase + '/labels', {
          method: 'POST',
          headers: settingsHeaders(),
          body: JSON.stringify(this.addForm),
        });
        this.items.push(await resp.json());
        this.adding = false;
        this.addForm = { name: '', color: '' };
        this.syncBoardLabels();
      } catch (e) {
        this.error = e.message;
      }
    },

    startEdit(label) {
      this.editing = label.uuid;
      this.editName = label.name;
    },

    async saveEdit(label) {
      if (this.editing !== label.uuid) return;
      if (!this.editName.trim() || this.editName === label.name) {
        this.editing = null;
        return;
      }
      try {
        await this.request(config.apiBase + '/labels/' + label.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ name: this.editName }),
        });
        label.name = this.editName;
        this.editing = null;
        this.syncBoardLabels();
      } catch (e) {
        this.error = e.message;
      }
    },

    async setColor(label, color) {
      try {
        await this.request(config.apiBase + '/labels/' + label.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ color: color }),
        });
        label.color = color;
        this.syncBoardLabels();
      } catch (e) {
        this.error = e.message;
      }
    },

    async removeLabel(label) {
      const ok = await AppDialog.confirm({
        title: 'Delete label',
        message:
          '"' + label.name + '" will be removed from every task using it.',
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        await this.request(config.apiBase + '/labels/' + label.uuid, {
          method: 'DELETE',
          headers: settingsHeaders(),
        });
        this.items = this.items.filter(function (l) {
          return l.uuid !== label.uuid;
        });
        this.syncBoardLabels();
      } catch (e) {
        this.error = e.message;
      }
    },
  };
}

function projectMembers(config) {
  return {
    items: [],
    search: '',
    results: [],
    searching: false,
    busy: false,
    error: '',

    async init() {
      try {
        const resp = await fetch(config.apiBase + '/members');
        if (resp.ok) this.items = await resp.json();
      } catch (e) {
        this.error = 'Could not load members.';
      }
    },

    syncBoardMembers() {
      this.members = this.items.map(function (m) {
        return { id: String(m.user), username: m.username };
      });
    },

    async request(url, options) {
      this.busy = true;
      this.error = '';
      try {
        const resp = await fetch(url, options);
        if (!resp.ok) {
          const data = await resp.json().catch(function () {
            return {};
          });
          throw new Error(data.detail || 'Request failed.');
        }
        return resp;
      } finally {
        this.busy = false;
      }
    },

    async runSearch() {
      const query = this.search.trim();
      if (query.length < 2) {
        this.results = [];
        return;
      }
      this.searching = true;
      try {
        const resp = await fetch(
          '/api/v1/users/search?q=' + encodeURIComponent(query)
        );
        if (resp.ok) {
          const data = await resp.json();
          const existing = new Set(
            this.items.map(function (m) {
              return m.user;
            })
          );
          this.results = data.results.filter(function (u) {
            return !existing.has(u.id);
          });
        }
      } finally {
        this.searching = false;
      }
    },

    async addMember(user) {
      try {
        const resp = await this.request(config.apiBase + '/members', {
          method: 'POST',
          headers: settingsHeaders(),
          body: JSON.stringify({ user: user.id, role: 'member' }),
        });
        this.items.push(await resp.json());
        this.search = '';
        this.results = [];
        this.syncBoardMembers();
      } catch (e) {
        this.error = e.message;
      }
    },

    async changeRole(member, role) {
      try {
        const resp = await this.request(
          config.apiBase + '/members/' + member.uuid,
          {
            method: 'PATCH',
            headers: settingsHeaders(),
            body: JSON.stringify({ role: role }),
          }
        );
        const updated = await resp.json();
        member.role = updated.role;
      } catch (e) {
        this.error = e.message;
        // Refetch from server truth: same-reference slice() would not
        // re-render the keyed x-for, leaving the refused value displayed.
        await this.init();
      }
    },

    async removeMember(member) {
      const ok = await AppDialog.confirm({
        title: 'Remove member',
        message: 'Remove ' + member.username + ' from this project?',
        okLabel: 'Remove',
        okClass: 'btn-error',
        icon: 'user-minus',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        await this.request(config.apiBase + '/members/' + member.uuid, {
          method: 'DELETE',
          headers: settingsHeaders(),
        });
        this.items = this.items.filter(function (m) {
          return m.uuid !== member.uuid;
        });
        this.syncBoardMembers();
      } catch (e) {
        this.error = e.message;
      }
    },
  };
}

window.projectSettingsGeneral = projectSettingsGeneral;
window.projectSettingsDanger = projectSettingsDanger;
window.projectColumns = projectColumns;
window.projectLabels = projectLabels;
window.projectMembers = projectMembers;
window.projectSettingsHelpers = { defaultMoveTarget: defaultMoveTarget };
