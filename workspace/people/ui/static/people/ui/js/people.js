// The People page: sidebar scope and lists, the searchable list, the panel.
// The list and the panel are server-rendered fragments swapped by alpine-ajax.

const MOBILE_QUERY = '(max-width: 1023px)';

window.peopleHelpers = {
  // Group persons by the first letter of their display name, '#' for the rest.
  groupPersons(persons) {
    const groups = new Map();
    for (const person of persons) {
      const first = (person.display_name || '').slice(0, 1).toUpperCase();
      const letter = /^[A-Z]$/.test(first) ? first : '#';
      if (!groups.has(letter)) groups.set(letter, []);
      groups.get(letter).push(person);
    }
    return Array.from(groups, ([letter, items]) => ({ letter, items }));
  },

  // Query string for the list fragment from the current filters.
  listUrl(base, { query = '', scope = '', listUuid = '' } = {}) {
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    if (scope) params.set('scope', scope);
    if (listUuid) params.set('list', listUuid);
    const qs = params.toString();
    return qs ? `${base}?${qs}` : base;
  },
};

window.peopleApp = function peopleApp(config) {
  return {
    query: config.query || '',
    scope: config.scope || '',
    listUuid: config.listUuid || '',
    current: config.initialPerson || '',
    panelOpen: Boolean(config.initialPerson),
    lists: [],
    collapsed: window.sidebarPreference.initial(),

    init() {
      const el = document.getElementById('people-lists-data');
      this.lists = el ? JSON.parse(el.textContent) : [];
      if (this.current) this.openPerson(this.current, { push: false });
      const params = new URLSearchParams(window.location.search);
      if (params.get('action') === 'new-person') this.newPerson();
    },

    sidebarCollapsed() {
      if (window.matchMedia(MOBILE_QUERY).matches) return false;
      return this.collapsed;
    },

    toggleCollapse() {
      if (window.matchMedia(MOBILE_QUERY).matches) return;
      this.collapsed = !this.collapsed;
      window.sidebarPreference.save('people', this.collapsed);
    },

    listFragmentUrl() {
      return window.peopleHelpers.listUrl('/people', {
        query: this.query,
        scope: this.scope,
        listUuid: this.listUuid,
      });
    },

    refreshList() {
      return this.$ajax(this.listFragmentUrl(), { target: 'person-list' });
    },

    setScope(scope) {
      this.scope = scope;
      this.listUuid = '';
      this.refreshList();
    },

    setList(uuid) {
      this.listUuid = uuid;
      this.scope = '';
      this.refreshList();
    },

    openPerson(uuid, { push = true } = {}) {
      this.current = uuid;
      this.panelOpen = true;
      if (push) history.pushState({ person: uuid }, '', `/people?person=${uuid}`);
      return this.$ajax(`/people/${uuid}/panel`, { target: 'person-panel' });
    },

    closePanel() {
      this.current = '';
      this.panelOpen = false;
      history.pushState({}, '', '/people');
    },

    onPopState() {
      const uuid = new URLSearchParams(window.location.search).get('person');
      if (uuid) this.openPerson(uuid, { push: false });
      else this.closePanel();
    },

    async newPerson() {
      const name = await AppDialog.prompt({
        title: 'New contact',
        placeholder: 'Display name',
        okLabel: 'Create',
      });
      if (!name) return;
      const body = { display_name: name };
      if (this.scope) body.scope = this.scope;
      const res = await fetch('/api/v1/people', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        AppAlert.show({ type: 'error', message: 'Could not create the contact.' });
        return;
      }
      const person = await res.json();
      await this.refreshList();
      this.openPerson(person.uuid);
    },

    async newList() {
      const name = await AppDialog.prompt({
        title: 'New list',
        placeholder: 'Name',
        okLabel: 'Create',
      });
      if (!name) return;
      const body = { name };
      if (this.scope) body.scope = this.scope;
      const res = await fetch('/api/v1/people/lists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        AppAlert.show({ type: 'error', message: 'Could not create the list.' });
        return;
      }
      this.lists.push(await res.json());
      this.lists.sort((a, b) => a.name.localeCompare(b.name));
    },

    async renameList(list) {
      const name = await AppDialog.prompt({
        title: 'Rename list',
        value: list.name,
        okLabel: 'Rename',
      });
      if (!name || name === list.name) return;
      const res = await fetch(`/api/v1/people/lists/${list.uuid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ name }),
      });
      if (res.ok) list.name = name;
      else AppAlert.show({ type: 'error', message: 'Could not rename the list.' });
    },

    async deleteList(list) {
      const ok = await AppDialog.confirm({
        title: 'Delete list',
        message: `Delete "${list.name}"? Contacts stay in your address book.`,
        okLabel: 'Delete',
        okClass: 'btn-error',
      });
      if (!ok) return;
      const res = await fetch(`/api/v1/people/lists/${list.uuid}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (!res.ok) {
        AppAlert.show({ type: 'error', message: 'Could not delete the list.' });
        return;
      }
      this.lists = this.lists.filter((entry) => entry.uuid !== list.uuid);
      if (this.listUuid === list.uuid) this.setList('');
    },
  };
};
