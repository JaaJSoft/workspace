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

// Mirrors the backend normalization; the API re-validates the format.
function normalizeProjectKey(value) {
  return String(value || '')
    .trim()
    .toUpperCase();
}

// Stops of the done-retention slider; the stop after the last preset means
// "always visible" ('' / null).
var RETENTION_PRESETS = [1, 7, 14, 30, 90];

// Snaps to the nearest preset so an API-set value (e.g. 21) still lands on
// a valid stop instead of leaving the slider out of range.
function retentionSliderIndex(days) {
  if (days === '' || days == null) return RETENTION_PRESETS.length;
  const value = Number(days);
  let best = 0;
  for (let i = 1; i < RETENTION_PRESETS.length; i++) {
    if (
      Math.abs(RETENTION_PRESETS[i] - value) <
      Math.abs(RETENTION_PRESETS[best] - value)
    ) {
      best = i;
    }
  }
  return best;
}

function retentionDaysFromIndex(index) {
  const i = Number(index);
  return i >= RETENTION_PRESETS.length ? '' : String(RETENTION_PRESETS[i]);
}

function projectSettingsGeneral(config) {
  return {
    name: '',
    description: '',
    key: '',
    // Select model: preset day count as a string, '' = always visible.
    doneRetentionDays: '',
    // Select model: 'points' | 'hours', '' = estimation disabled.
    estimateUnit: '',
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
      this.key = data.key;
      this.doneRetentionDays =
        data.done_retention_days == null ? '' : String(data.done_retention_days);
      this.estimateUnit = data.estimate_unit || '';
    },

    retentionIndex() {
      return retentionSliderIndex(this.doneRetentionDays);
    },

    setRetentionIndex(index) {
      this.doneRetentionDays = retentionDaysFromIndex(index);
    },

    retentionSliderLabel() {
      if (this.doneRetentionDays === '') return 'Always';
      return (
        this.doneRetentionDays +
        ' day' +
        (this.doneRetentionDays === '1' ? '' : 's')
      );
    },

    async save() {
      this.saving = true;
      this.error = '';
      this.saved = false;
      try {
        const body = {
          name: this.name,
          description: this.description,
          key: normalizeProjectKey(this.key),
          done_retention_days:
            this.doneRetentionDays === '' ? null : Number(this.doneRetentionDays),
          estimate_unit: this.estimateUnit,
        };
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
            data.detail ||
              (data.name && data.name[0]) ||
              (data.key && data.key[0]) ||
              'Could not save.'
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

// Group attachment lives in the Access section: attached groups render as
// removable chips, additions come from the shared group selector. Every
// change PATCHes the full id list, no explicit save button.
function projectGroupAccess(config) {
  return {
    items: [],
    available: [],
    busy: false,
    saved: false,
    error: '',

    async init() {
      const data = JSON.parse(
        document.getElementById('project-settings-data').textContent
      );
      this.items = data.groups || [];
      try {
        const resp = await fetch('/api/v1/users/groups');
        if (resp.ok) this.available = await resp.json();
      } catch (e) {
        // Additions stay unavailable; attached chips still render and can
        // be removed.
      }
    },

    // Getter for the shared group selector: my groups not yet attached.
    selectableGroups() {
      const attached = this.items.map(function (g) {
        return String(g.id);
      });
      return this.available.filter(function (g) {
        return !attached.includes(String(g.id));
      });
    },

    // A change during an in-flight save would compute its list from items
    // that the pending PATCH is about to supersede, silently dropping that
    // change - hence the busy guard on both mutations.
    async addGroup(group) {
      if (this.busy) return;
      const already = this.items.some(function (g) {
        return String(g.id) === String(group.id);
      });
      if (already) return;
      await this.save(this.items.concat([{ id: group.id, name: group.name }]));
    },

    async removeGroup(group) {
      if (this.busy) return;
      await this.save(
        this.items.filter(function (g) {
          return String(g.id) !== String(group.id);
        })
      );
    },

    async save(next) {
      this.busy = true;
      this.error = '';
      this.saved = false;
      try {
        const resp = await fetch(config.apiBase, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({
            groups: next.map(function (g) {
              return g.id;
            }),
          }),
        });
        if (!resp.ok) {
          const data = await resp.json().catch(function () {
            return {};
          });
          throw new Error(
            data.detail || (data.groups && data.groups[0]) || 'Could not save.'
          );
        }
        this.items = next;
        this.saved = true;
      } catch (e) {
        this.error = e.message;
      } finally {
        this.busy = false;
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

    // Not named destroy(): Alpine auto-invokes a destroy() method as a
    // teardown hook when the element leaves the DOM (view swaps).
    async deleteProject() {
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

// Columns and labels pick from the shared <tag-chip> palette, so a label
// and a chip rendered from it can never drift apart.
const COLUMN_COLORS = window.TAG_CHIP_COLORS.map((c) => c.value);

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
          body: JSON.stringify({
            name: this.addForm.name.trim(),
            category: this.addForm.category,
            color: this.addForm.color,
          }),
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
      const name = this.editName.trim();
      if (!name || name === column.name) {
        this.editing = null;
        return;
      }
      try {
        await this.request(config.apiBase + '/statuses/' + column.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ name: name }),
        });
        column.name = name;
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
        if (!others.length) {
          this.error = 'The last column cannot be deleted while it holds tasks.';
          return;
        }
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
        if (!moveTarget) return;
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
          body: JSON.stringify({
            name: this.addForm.name.trim(),
            color: this.addForm.color,
          }),
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
      const name = this.editName.trim();
      if (!name || name === label.name) {
        this.editing = null;
        return;
      }
      try {
        await this.request(config.apiBase + '/labels/' + label.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ name: name }),
        });
        label.name = name;
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

function projectEpics(config) {
  return {
    items: [],
    colors: COLUMN_COLORS,
    query: '',
    openOnly: false,
    adding: false,
    addForm: { name: '', color: '', description: '' },
    editing: null,
    editName: '',
    editingDesc: null,
    descDraft: '',
    busy: false,
    error: '',

    async init() {
      try {
        const resp = await fetch(config.apiBase + '/epics');
        if (resp.ok) this.items = await resp.json();
      } catch (e) {
        this.error = 'Could not load epics.';
      }
    },

    syncBoardEpics() {
      // this.epics is the parent projectBoard's array via Alpine's scope
      // chain (same shape as the epics-data payload).
      this.epics = this.items.map(function (e) {
        return { uuid: e.uuid, name: e.name, color: e.color, closed: e.closed };
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

    progressPercent(epic) {
      if (!epic.task_count) return 0;
      return Math.round((epic.done_task_count / epic.task_count) * 100);
    },

    // List filter: quick name search + "open only" switch. Purely visual -
    // items stays the full list so counts and syncBoardEpics see everything.
    visibleEpics() {
      const needle = this.query.trim().toLowerCase();
      return this.items.filter(
        (e) =>
          (!this.openOnly || !e.closed) &&
          (!needle || e.name.toLowerCase().includes(needle))
      );
    },

    async addEpic() {
      try {
        const resp = await this.request(config.apiBase + '/epics', {
          method: 'POST',
          headers: settingsHeaders(),
          body: JSON.stringify({
            name: this.addForm.name.trim(),
            color: this.addForm.color,
            description: this.addForm.description.trim(),
          }),
        });
        this.items.push(await resp.json());
        this.adding = false;
        this.addForm = { name: '', color: '', description: '' };
        this.syncBoardEpics();
      } catch (e) {
        this.error = e.message;
      }
    },

    startEdit(epic) {
      this.editing = epic.uuid;
      this.editName = epic.name;
    },

    async saveEdit(epic) {
      if (this.editing !== epic.uuid) return;
      const name = this.editName.trim();
      if (!name || name === epic.name) {
        this.editing = null;
        return;
      }
      try {
        await this.request(config.apiBase + '/epics/' + epic.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ name: name }),
        });
        epic.name = name;
        this.editing = null;
        this.syncBoardEpics();
      } catch (e) {
        this.error = e.message;
      }
    },

    startDescEdit(epic) {
      this.editingDesc = epic.uuid;
      this.descDraft = epic.description || '';
    },

    async saveDescEdit(epic) {
      if (this.editingDesc !== epic.uuid) return;
      const description = this.descDraft.trim();
      if (description === (epic.description || '')) {
        this.editingDesc = null;
        return;
      }
      try {
        await this.request(config.apiBase + '/epics/' + epic.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ description: description }),
        });
        epic.description = description;
        // Only a successful save closes the editor: on failure the open
        // textarea is what keeps the user's draft from being lost.
        this.editingDesc = null;
      } catch (e) {
        this.error = e.message;
      }
    },

    async setColor(epic, color) {
      try {
        await this.request(config.apiBase + '/epics/' + epic.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ color: color }),
        });
        epic.color = color;
        this.syncBoardEpics();
      } catch (e) {
        this.error = e.message;
      }
    },

    async toggleClosed(epic) {
      try {
        await this.request(config.apiBase + '/epics/' + epic.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ closed: !epic.closed }),
        });
        epic.closed = !epic.closed;
        this.syncBoardEpics();
      } catch (e) {
        this.error = e.message;
      }
    },

    async removeEpic(epic) {
      const ok = await AppDialog.confirm({
        title: 'Delete epic',
        message:
          '"' +
          epic.name +
          '" will be deleted; its tasks are kept and ungrouped.',
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        await this.request(config.apiBase + '/epics/' + epic.uuid, {
          method: 'DELETE',
          headers: settingsHeaders(),
        });
        this.items = this.items.filter(function (e) {
          return e.uuid !== epic.uuid;
        });
        this.syncBoardEpics();
      } catch (e) {
        this.error = e.message;
      }
    },
  };
}

// Sprints are ordered by creation, so the last item is the reference for
// "what comes next": its trailing number is incremented ("Sprint 6" ->
// "Sprint 7", "2026-S3" -> "2026-S4", padding preserved), skipping names
// already taken. Without a trailing number the fallback is
// "Sprint <count+1>", subject to the same collision skip.
function nextSprintName(sprints) {
  const taken = new Set(sprints.map((s) => s.name));
  const last = sprints.length ? sprints[sprints.length - 1].name : '';
  const m = last.match(/^(.*?)(\d+)\s*$/);
  const base = m ? m[1] : 'Sprint ';
  let n = m ? parseInt(m[2], 10) + 1 : sprints.length + 1;
  const pad = m && m[2][0] === '0' ? m[2].length : 0;
  let candidate = base + String(n).padStart(pad, '0');
  while (taken.has(candidate)) {
    n += 1;
    candidate = base + String(n).padStart(pad, '0');
  }
  return candidate;
}

function addDays(isoDate, days) {
  const d = new Date(isoDate + 'T00:00:00');
  d.setDate(d.getDate() + days);
  const pad2 = (x) => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

// Suggested dates for a new sprint: it starts where the running sprint
// ends (or today), and lasts as long as the previous sprint did (two
// weeks when no sprint has both dates yet).
function suggestSprintDates(sprints, today) {
  const active = sprints.find((s) => s.state === 'active');
  const start = (active && active.end_date) || today;
  let length = 14;
  for (let i = sprints.length - 1; i >= 0; i--) {
    const s = sprints[i];
    if (s.start_date && s.end_date) {
      const days = Math.round(
        (new Date(s.end_date) - new Date(s.start_date)) / 86400000
      );
      if (days > 0) length = days;
      break;
    }
  }
  return { start_date: start, end_date: addDays(start, length) };
}

function projectSprints(config) {
  return {
    items: [],
    query: '',
    hideClosed: true,
    adding: false,
    addForm: { name: '', goal: '', start_date: '', end_date: '' },
    editing: null,
    editName: '',
    editingGoal: null,
    goalDraft: '',
    busy: false,
    error: '',

    async init() {
      try {
        const resp = await fetch(config.apiBase + '/sprints');
        if (resp.ok) this.items = await resp.json();
      } catch (e) {
        this.error = 'Could not load sprints.';
      }
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
            data.detail ||
              (data.name && data.name[0]) ||
              (data.end_date && data.end_date[0]) ||
              'Request failed.'
          );
        }
        return resp;
      } finally {
        this.busy = false;
      }
    },

    // List filter mirroring the sprint switcher: a query searches the
    // whole list, closed history included; without one, closed sprints
    // obey the toggle. Purely visual - items stays the full list.
    visibleSprints() {
      const needle = this.query.trim().toLowerCase();
      if (needle) {
        return this.items.filter((s) => s.name.toLowerCase().includes(needle));
      }
      if (!this.hideClosed) return this.items;
      return this.items.filter((s) => s.state !== 'closed');
    },

    // The prefills are suggestions only - the name is focused and
    // selected in the template so typing replaces it wholesale.
    toggleAdd() {
      if (this.adding) {
        this.adding = false;
        return;
      }
      const now = new Date();
      const pad2 = (x) => String(x).padStart(2, '0');
      const today =
        now.getFullYear() + '-' + pad2(now.getMonth() + 1) + '-' + pad2(now.getDate());
      const dates = suggestSprintDates(this.items, today);
      this.addForm = {
        name: nextSprintName(this.items),
        goal: '',
        start_date: dates.start_date,
        end_date: dates.end_date,
      };
      this.adding = true;
    },

    async addSprint() {
      try {
        const resp = await this.request(config.apiBase + '/sprints', {
          method: 'POST',
          headers: settingsHeaders(),
          body: JSON.stringify({
            name: this.addForm.name.trim(),
            goal: this.addForm.goal.trim(),
            start_date: this.addForm.start_date || null,
            end_date: this.addForm.end_date || null,
          }),
        });
        this.items.push(await resp.json());
        this.adding = false;
        this.addForm = { name: '', goal: '', start_date: '', end_date: '' };
      } catch (e) {
        this.error = e.message;
      }
    },

    startEdit(sprint) {
      this.editing = sprint.uuid;
      this.editName = sprint.name;
    },

    async saveEdit(sprint) {
      if (this.editing !== sprint.uuid) return;
      const name = this.editName.trim();
      if (!name || name === sprint.name) {
        this.editing = null;
        return;
      }
      try {
        await this.request(config.apiBase + '/sprints/' + sprint.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ name: name }),
        });
        sprint.name = name;
        this.editing = null;
      } catch (e) {
        this.error = e.message;
      }
    },

    startGoalEdit(sprint) {
      this.editingGoal = sprint.uuid;
      this.goalDraft = sprint.goal || '';
    },

    async saveGoalEdit(sprint) {
      if (this.editingGoal !== sprint.uuid) return;
      const goal = this.goalDraft.trim();
      if (goal === (sprint.goal || '')) {
        this.editingGoal = null;
        return;
      }
      try {
        await this.request(config.apiBase + '/sprints/' + sprint.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ goal: goal }),
        });
        sprint.goal = goal;
        // Only a successful save closes the editor: on failure the open
        // textarea is what keeps the user's draft from being lost.
        this.editingGoal = null;
      } catch (e) {
        this.error = e.message;
      }
    },

    async setDate(sprint, field, value) {
      try {
        await this.request(config.apiBase + '/sprints/' + sprint.uuid, {
          method: 'PATCH',
          headers: settingsHeaders(),
          body: JSON.stringify({ [field]: value || null }),
        });
        sprint[field] = value || null;
      } catch (e) {
        this.error = e.message;
        // Refetch from server truth: the rejected date input would
        // otherwise keep displaying the refused value.
        await this.init();
      }
    },

    async removeSprint(sprint) {
      const ok = await AppDialog.confirm({
        title: 'Delete sprint',
        message:
          '"' +
          sprint.name +
          '" will be deleted; its tasks are kept and leave the sprint.',
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        await this.request(config.apiBase + '/sprints/' + sprint.uuid, {
          method: 'DELETE',
          headers: settingsHeaders(),
        });
        this.items = this.items.filter(function (s) {
          return s.uuid !== sprint.uuid;
        });
      } catch (e) {
        this.error = e.message;
      }
    },
  };
}

function projectMembers(config) {
  return {
    items: [],
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

    async addMember(user) {
      // The shared user selector does not know who is already a member.
      const already = this.items.some(function (m) {
        return String(m.user) === String(user.id);
      });
      if (already) return;
      try {
        const resp = await this.request(config.apiBase + '/members', {
          method: 'POST',
          headers: settingsHeaders(),
          body: JSON.stringify({ user: user.id, role: 'member' }),
        });
        this.items.push(await resp.json());
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
window.projectGroupAccess = projectGroupAccess;
window.projectSettingsDanger = projectSettingsDanger;
window.projectColumns = projectColumns;
window.projectLabels = projectLabels;
window.projectEpics = projectEpics;
window.projectSprints = projectSprints;
window.projectMembers = projectMembers;
window.projectSettingsHelpers = {
  defaultMoveTarget: defaultMoveTarget,
  normalizeProjectKey: normalizeProjectKey,
  retentionSliderIndex: retentionSliderIndex,
  retentionDaysFromIndex: retentionDaysFromIndex,
  nextSprintName: nextSprintName,
  suggestSprintDates: suggestSprintDates,
};
