// The People page: sidebar scope and lists, the searchable list, the panel.
// The list and the panel are server-rendered fragments swapped by alpine-ajax.

const MOBILE_QUERY = '(max-width: 1023px)';

window.peopleHelpers = {
  // The state a server-rendered fragment embeds, or the fallback when the
  // block is missing (the panel placeholder carries none).
  readJson(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    try {
      return JSON.parse(el.textContent);
    } catch (_) {
      return fallback;
    }
  },

  // A /people URL carrying the filters, and the open contact when there is one.
  listUrl(base, { query = '', scope = '', listUuid = '', person = '' } = {}) {
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    if (scope) params.set('scope', scope);
    if (listUuid) params.set('list', listUuid);
    if (person) params.set('person', person);
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
    groups: [],
    personForm: { name: '', email: '', scope: 'mine' },
    listForm: { uuid: '', name: '', original: '', scope: 'mine' },
    saving: false,
    collapsed: window.sidebarPreference.initial(),
    ctxMenu: { open: false, x: 0, y: 0, type: null, data: null, actions: null },
    _menuGeneration: 0,

    init() {
      this.lists = window.peopleHelpers.readJson('people-lists-data', []);
      this.groups = window.peopleHelpers.readJson('people-groups-data', []);
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

    // What the address bar should read for the current state. The view reads
    // all four back, so a reload or a shared link reopens the same listing.
    pageUrl() {
      return window.peopleHelpers.listUrl('/people', {
        query: this.query,
        scope: this.scope,
        listUuid: this.listUuid,
        person: this.current,
      });
    },

    swapList() {
      return this.$ajax(this.listFragmentUrl(), { target: 'person-list' });
    },

    refreshList() {
      history.replaceState(history.state, '', this.pageUrl());
      return this.swapList();
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
      if (push) history.pushState({ person: uuid }, '', this.pageUrl());
      return this.$ajax(`/people/${uuid}/panel`, { target: 'person-panel' });
    },

    closePanel({ push = true } = {}) {
      this.current = '';
      this.panelOpen = false;
      if (push) history.pushState({}, '', this.pageUrl());
    },

    onPopState() {
      const params = new URLSearchParams(window.location.search);
      this.query = params.get('q') || '';
      this.scope = params.get('scope') || '';
      this.listUuid = params.get('list') || '';
      this.swapList();
      const uuid = params.get('person');
      if (uuid) this.openPerson(uuid, { push: false });
      else this.closePanel({ push: false });
    },

    // ---- context menu ---------------------------------------------------

    // One menu for the rows, the panel button and the sidebar lists. A
    // person's rows are fetched as the menu opens: a stale list is what turns
    // into a request the server refuses. The generation drops a late answer
    // for a menu that was closed or reopened elsewhere in the meantime.
    openCtxMenu(event, type, data) {
      event.preventDefault();
      const menuW = 224;
      const menuH = 240;
      let x = event.clientX;
      let y = event.clientY;
      if (x + menuW > window.innerWidth) x = window.innerWidth - menuW;
      if (y + menuH > window.innerHeight) y = window.innerHeight - menuH;
      this._menuGeneration += 1;
      const generation = this._menuGeneration;
      this.ctxMenu = { open: true, x, y, type, data, actions: type === 'person' ? null : [] };
      if (type !== 'person') return;
      fetch('/api/v1/people/actions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ uuids: [data.uuid] }),
      })
        .then((res) => (res.ok ? res.json() : {}))
        .catch(() => ({}))
        .then((answer) => {
          if (generation !== this._menuGeneration) return;
          this.ctxMenu.actions = answer[data.uuid] || [];
          this.$nextTick(() => {
            if (window.lucide) window.lucide.createIcons();
          });
        });
    },

    closeCtxMenu() {
      this._menuGeneration += 1;
      this.ctxMenu = { open: false, x: 0, y: 0, type: null, data: null, actions: null };
    },

    ctxPersonAction(action) {
      const data = this.ctxMenu.data;
      this.closeCtxMenu();
      if (!data) return;
      if (action.id === 'edit') {
        this.openPerson(data.uuid);
        return;
      }
      if (action.id === 'delete') {
        this.deletePerson(data);
        return;
      }
      // The other actions open a dialog the panel owns: open the contact
      // first when it is not the one on screen, then hand the action over.
      const run = () =>
        this.$dispatch('people-panel-run', { uuid: data.uuid, id: action.id });
      if (this.current === data.uuid) run();
      else Promise.resolve(this.openPerson(data.uuid)).then(run);
    },

    ctxListAction(id) {
      const list = this.ctxMenu.data;
      this.closeCtxMenu();
      if (!list) return;
      if (id === 'rename') this.renameList(list);
      if (id === 'delete') this.deleteList(list);
    },

    async deletePerson(data) {
      const ok = await AppDialog.confirm({
        title: 'Delete contact',
        message: `Delete "${data.name}"? This cannot be undone.`,
        okLabel: 'Delete',
        okClass: 'btn-error',
      });
      if (!ok) return;
      const res = await fetch(`/api/v1/people/${data.uuid}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (!res.ok) {
        AppAlert.show({ type: 'error', message: 'Could not delete the contact.' });
        return;
      }
      this.onScopeChanged({ from: data.scope, to: null });
      if (this.current === data.uuid) this.closePanel();
      this.refreshList();
    },

    // A group earns its sidebar row with its first contact and loses it with
    // the last one; the dialogs keep offering every group.
    visibleGroups() {
      return this.groups.filter((g) => g.person_count > 0);
    },

    _bumpGroup(scope, delta) {
      if (typeof scope !== 'string' || !scope.startsWith('group:')) return;
      const id = Number.parseInt(scope.slice('group:'.length), 10);
      const group = this.groups.find((g) => g.id === id);
      if (group) group.person_count = Math.max(0, group.person_count + delta);
    },

    // { from, to } are scopes ('mine' | 'group:<id>'); `to` is null on a delete.
    onScopeChanged({ from, to }) {
      if (from === to) return;
      this._bumpGroup(from, -1);
      this._bumpGroup(to, 1);
    },

    // The sidebar selection is the default book of a new contact or list;
    // "All" and a list filter mean the personal one.
    defaultScope() {
      return this.scope.startsWith('group:') ? this.scope : 'mine';
    },

    resetPersonForm() {
      this.personForm = { name: '', email: '', scope: this.defaultScope() };
    },

    newPerson() {
      this.resetPersonForm();
      this.$refs.personDialog.showModal();
      this.$nextTick(() => this.$refs.personName.focus());
    },

    async createPerson() {
      const name = this.personForm.name.trim();
      if (!name || this.saving) return;
      const body = { display_name: name, scope: this.personForm.scope };
      const email = this.personForm.email.trim();
      if (email) body.emails = [{ value: email, type: 'other' }];
      this.saving = true;
      try {
        const res = await fetch('/api/v1/people', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          const first = Object.values(data).flat()[0];
          AppAlert.show({
            type: 'error',
            message: typeof first === 'string' ? first : 'Could not create the contact.',
          });
          return;
        }
        const person = await res.json();
        this.$refs.personDialog.close();
        this.onScopeChanged({ from: null, to: person.scope });
        // A fresh contact belongs to no list, so a list filter would hide it.
        this.listUuid = '';
        await this.refreshList();
        this.openPerson(person.uuid);
      } finally {
        this.saving = false;
      }
    },

    resetListForm() {
      this.listForm = { uuid: '', name: '', original: '', scope: this.defaultScope() };
    },

    newList() {
      this.resetListForm();
      this.$refs.listDialog.showModal();
      this.$nextTick(() => this.$refs.listName.focus());
    },

    renameList(list) {
      this.listForm = { uuid: list.uuid, name: list.name, original: list.name, scope: list.scope };
      this.$refs.listDialog.showModal();
      this.$nextTick(() => this.$refs.listName.select());
    },

    async saveList() {
      const name = this.listForm.name.trim();
      if (!name || this.saving) return;
      const renaming = Boolean(this.listForm.uuid);
      if (renaming && name === this.listForm.original) {
        this.$refs.listDialog.close();
        return;
      }
      const body = renaming ? { name } : { name, scope: this.listForm.scope };
      this.saving = true;
      try {
        const res = await fetch(
          renaming ? `/api/v1/people/lists/${this.listForm.uuid}` : '/api/v1/people/lists',
          {
            method: renaming ? 'PATCH' : 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
            body: JSON.stringify(body),
          }
        );
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          const first = Object.values(data).flat()[0];
          AppAlert.show({
            type: 'error',
            message: typeof first === 'string' ? first : 'Could not save the list.',
          });
          return;
        }
        const saved = await res.json();
        if (renaming) {
          const list = this.lists.find((l) => l.uuid === saved.uuid);
          if (list) list.name = saved.name;
        } else {
          this.lists.push(saved);
        }
        this.lists.sort((a, b) => a.name.localeCompare(b.name));
        this.$refs.listDialog.close();
      } finally {
        this.saving = false;
      }
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

    // Membership is changed from the panel, which knows nothing of the
    // sidebar's counts: it says so and the shell re-reads them.
    async reloadLists() {
      const res = await fetch('/api/v1/people/lists');
      if (!res.ok) {
        AppAlert.show({ type: 'error', message: 'Could not refresh the lists.' });
        return;
      }
      this.lists = await res.json();
    },
  };
};

// The detail panel. alpine-ajax replaces the whole #person-panel section, so
// the component is built afresh on every open and seeds itself from the
// json_script blocks the fragment carries.
window.personPanel = function personPanel() {
  return {
    person: window.peopleHelpers.readJson('person-panel-data', {}),
    actions: window.peopleHelpers.readJson('person-panel-actions', []),
    lists: window.peopleHelpers.readJson('person-panel-lists', []),
    groups: window.peopleHelpers.readJson('person-panel-groups', []),
    avatarStamp: Date.now(),
    listPick: '',
    movePick: '',
    uploading: false,
    cropper: null,
    selectedFile: null,
    _patchGen: {},

    can(id) {
      return this.actions.some((a) => a.id === id);
    },

    // Reached from the page's context menu (people-panel-run) for the
    // actions whose dialogs live in the panel.
    runAction(id) {
      if (id === 'move') return this.move();
      if (id === 'add_to_list') return this.addToList();
      if (id === 'unlink_user') return this.unlinkUser();
      return undefined;
    },

    // A reply publishes a field only if that field has not moved since the
    // request left. Blurring an input starts a PATCH and the click that blurred
    // it runs while the reply is in flight, so `emails` can already hold a row
    // the server never saw; adopting the reply there would delete it. Per key
    // rather than per request, because two fields can be in flight at once.
    _claimPatch(body) {
      const claims = {};
      for (const key of Object.keys(body)) {
        this._patchGen[key] = (this._patchGen[key] || 0) + 1;
        claims[key] = {
          gen: this._patchGen[key],
          before: JSON.stringify(this.person[key]),
        };
      }
      return claims;
    },

    _adopt(updated, claims) {
      for (const [key, claim] of Object.entries(claims)) {
        if (!(key in updated)) continue;
        // A newer request owns the field, or the user changed it meanwhile.
        if (this._patchGen[key] !== claim.gen) continue;
        if (JSON.stringify(this.person[key]) !== claim.before) continue;
        this.person[key] = updated[key];
      }
    },

    async patch(body) {
      const claims = this._claimPatch(body);
      const res = await fetch(`/api/v1/people/${this.person.uuid}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const first = Object.values(data).flat()[0];
        AppAlert.show({ type: 'error', message: typeof first === 'string' ? first : 'Could not save.' });
        return null;
      }
      const updated = await res.json();
      // Only the fields this request carried, the way a save() names its
      // update_fields. `move` and `linkUser` send fields the reply does not
      // echo back; they reload the panel from the server instead.
      this._adopt(updated, claims);
      return updated;
    },

    saveField(name) {
      if (!this.can('edit')) return;
      const value = this.person[name] === '' && name === 'birthday' ? null : this.person[name];
      this.patch({ [name]: value }).then((updated) => {
        if (updated && name === 'display_name') this.refreshList();
      });
    },

    addEntry(kind) {
      if (kind === 'addresses') {
        this.person.addresses.push({ street: '', city: '', region: '', postal_code: '', country: '', type: 'home' });
      } else {
        this.person[kind].push({ value: '', type: kind === 'phones' ? 'cell' : 'home' });
      }
    },

    removeEntry(kind, index) {
      this.person[kind].splice(index, 1);
      this.saveEntries(kind);
    },

    // A blank row is one the user is still filling in: it is dropped from the
    // payload rather than sent, so pressing Add never fails the whole save.
    saveEntries(kind) {
      if (!this.can('edit')) return;
      const complete = this.person[kind].filter((entry) =>
        kind === 'addresses'
          ? [entry.street, entry.city, entry.region, entry.postal_code, entry.country].some(Boolean)
          : Boolean(entry.value)
      );
      this.patch({ [kind]: complete }).then((updated) => {
        if (updated && kind === 'emails') this.refreshList();
      });
    },

    linkUser(user) {
      this.patch({ linked_user_id: user.id }).then((updated) => {
        if (updated) this.reloadPanel();
      });
    },

    unlinkUser() {
      this.patch({ linked_user_id: null }).then((updated) => {
        if (updated) this.reloadPanel();
      });
    },

    reloadPanel() {
      this.$ajax(`/people/${this.person.uuid}/panel`, { target: 'person-panel' });
      this.refreshList();
    },

    move() {
      this.movePick = this.person.scope;
      this.$refs.moveDialog.showModal();
    },

    async confirmMove() {
      const choice = this.movePick;
      if (!choice || choice === this.person.scope) return;
      const from = this.person.scope;
      const updated = await this.patch({ scope: choice });
      this.$refs.moveDialog.close();
      if (updated) {
        this.$dispatch('people-scope-changed', { from, to: updated.scope });
        this.reloadPanel();
      }
    },

    addToList() {
      const first = this.lists.find((l) => !l.member);
      this.listPick = first ? first.uuid : '';
      this.$refs.listPickDialog.showModal();
    },

    async confirmAddToList() {
      const choice = this.listPick;
      if (!choice) return;
      const res = await fetch(`/api/v1/people/lists/${choice}/members`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ uuids: [this.person.uuid] }),
      });
      if (!res.ok) {
        AppAlert.show({ type: 'error', message: 'Could not add to the list.' });
        return;
      }
      const list = this.lists.find((l) => l.uuid === choice);
      if (list) list.member = true;
      this.$refs.listPickDialog.close();
      this.$dispatch('people-lists-changed');
    },

    async removeFromList(uuid) {
      const res = await fetch(`/api/v1/people/lists/${uuid}/members`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ uuids: [this.person.uuid] }),
      });
      if (!res.ok) {
        AppAlert.show({ type: 'error', message: 'Could not remove from the list.' });
        return;
      }
      const list = this.lists.find((l) => l.uuid === uuid);
      if (list) list.member = false;
      this.$dispatch('people-lists-changed');
    },

    onFileSelect(event) {
      const file = event.target.files[0];
      if (!file) return;
      this.selectedFile = file;
      const reader = new FileReader();
      reader.onload = (e) => {
        this.$refs.cropImage.src = e.target.result;
        this.$refs.cropDialog.showModal();
        this.$nextTick(() => {
          if (this.cropper) this.cropper.destroy();
          this.cropper = new Cropper(this.$refs.cropImage, {
            aspectRatio: 1,
            viewMode: 1,
            movable: true,
            zoomable: true,
            rotatable: false,
            scalable: false,
            guides: true,
            center: true,
            highlight: false,
            background: true,
          });
        });
      };
      reader.readAsDataURL(file);
      event.target.value = '';
    },

    cancelCrop() {
      this.$refs.cropDialog.close();
      if (this.cropper) {
        this.cropper.destroy();
        this.cropper = null;
      }
      this.selectedFile = null;
    },

    async confirmCrop() {
      if (!this.cropper || !this.selectedFile) return;
      this.uploading = true;
      const data = this.cropper.getData(true);
      const formData = new FormData();
      formData.append('image', this.selectedFile);
      formData.append('crop_x', data.x);
      formData.append('crop_y', data.y);
      formData.append('crop_w', data.width);
      formData.append('crop_h', data.height);
      try {
        const res = await fetch(`/api/v1/people/${this.person.uuid}/avatar`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          body: formData,
        });
        if (res.ok) {
          this.person.has_avatar = true;
          this.person.avatar_url = `/api/v1/people/${this.person.uuid}/avatar`;
          this.avatarStamp = Date.now();
          this.refreshList();
        } else {
          AppAlert.show({ type: 'error', message: 'Upload failed.' });
        }
      } finally {
        this.uploading = false;
        this.cancelCrop();
      }
    },

    async deleteAvatar() {
      if (!this.can('edit')) return;
      const res = await fetch(`/api/v1/people/${this.person.uuid}/avatar`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      if (!res.ok) {
        AppAlert.show({ type: 'error', message: 'Could not remove the photo.' });
        return;
      }
      this.person.has_avatar = false;
      this.person.avatar_url = null;
      this.avatarStamp = Date.now();
      this.refreshList();
    },
  };
};
