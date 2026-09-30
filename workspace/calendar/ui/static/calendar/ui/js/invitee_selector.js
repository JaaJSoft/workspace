/**
 * Event invitee picker: one search field over workspace accounts, address-book
 * persons and contact lists.
 *
 * Usage: x-data="inviteeSelector('my-event-name')"
 *
 * Selecting a row dispatches `eventName` on window with `{ detail: { item } }`,
 * where `item.kind` is 'user' (`item.user`), 'person' (`item.person`) or
 * 'list' (`item.list`). Turning a person or a list into accounts and external
 * guests is the server's job (POST /api/v1/events/invitees), not this one's.
 */

const INVITEE_MIN_QUERY_LENGTH = 2;
const INVITEE_MAX_USERS = 5;
const INVITEE_MAX_PERSONS = 6;
const INVITEE_MAX_LISTS = 4;

function inviteePrimaryEmail(person) {
  return ((person.emails || [])[0] || {}).value || '';
}

/**
 * Merge the three searches into one flat, grouped result list.
 *
 * A person linked to an account already listed among the users is dropped:
 * both rows would invite the same account. A person with neither an account
 * nor an email, and an empty list, stay visible but disabled, so the user sees
 * why the contact they typed cannot be picked.
 */
window.inviteeResults = function inviteeResults(query, users, persons, lists) {
  const needle = (query || '').trim().toLowerCase();
  const results = [];

  const userIds = new Set();
  for (const user of (users || []).slice(0, INVITEE_MAX_USERS)) {
    userIds.add(user.id);
    results.push({ kind: 'user', key: `user:${user.id}`, disabled: false, user });
  }

  let personCount = 0;
  for (const person of persons || []) {
    if (personCount >= INVITEE_MAX_PERSONS) break;
    const linkedId = person.linked_user ? person.linked_user.id : null;
    if (linkedId !== null && userIds.has(linkedId)) continue;
    if (linkedId !== null) userIds.add(linkedId);
    const email = inviteePrimaryEmail(person);
    results.push({
      kind: 'person',
      key: `person:${person.uuid}`,
      disabled: linkedId === null && !email,
      linked: linkedId !== null,
      email,
      person,
    });
    personCount += 1;
  }

  const matchingLists = (lists || [])
    .filter((list) => (list.name || '').toLowerCase().includes(needle))
    .slice(0, INVITEE_MAX_LISTS);
  for (const list of matchingLists) {
    results.push({
      kind: 'list',
      key: `list:${list.uuid}`,
      disabled: !list.member_count,
      list,
    });
  }
  return results;
};

window.inviteeSelector = function inviteeSelector(eventName) {
  return {
    query: '',
    results: [],
    loading: false,
    showDropdown: false,
    highlight: -1,
    eventName: eventName || 'invitee-selected',
    _lists: null,
    _searchGeneration: 0,

    async _fetchJson(url) {
      try {
        const resp = await fetch(url, { credentials: 'same-origin' });
        return resp.ok ? await resp.json() : null;
      } catch (e) {
        return null;
      }
    },

    async _loadLists() {
      // Lists are few and rarely change while a dialog is open: one fetch,
      // then filtered locally on every keystroke.
      if (this._lists === null) {
        const data = await this._fetchJson('/api/v1/people/lists');
        if (data) this._lists = data;
      }
      return this._lists || [];
    },

    async search() {
      const q = (this.query || '').trim();
      const generation = ++this._searchGeneration;
      if (q.length < INVITEE_MIN_QUERY_LENGTH) {
        this.reset(false);
        return;
      }
      this.loading = true;
      const encoded = encodeURIComponent(q);
      const [users, persons, lists] = await Promise.all([
        this._fetchJson(`/api/v1/users/search?q=${encoded}&limit=${INVITEE_MAX_USERS}`),
        this._fetchJson(`/api/v1/people?q=${encoded}`),
        this._loadLists(),
      ]);
      // A slower earlier search must not overwrite the results of the query
      // the user is now looking at.
      if (generation !== this._searchGeneration) return;
      this.results = window.inviteeResults(
        q,
        users ? users.results : [],
        persons ? persons.results : [],
        lists,
      );
      this.highlight = this.results.findIndex((item) => !item.disabled);
      this.showDropdown = true;
      this.loading = false;
      // Inside a scrolling dialog the field often sits at the bottom edge:
      // bring the dropdown into view rather than leave it clipped.
      this.$nextTick(() => requestAnimationFrame(
        () => this.$refs.dropdown?.scrollIntoView({ block: 'nearest' }),
      ));
    },

    isGroupStart(idx) {
      return idx === 0 || this.results[idx - 1].kind !== this.results[idx].kind;
    },

    groupLabel(kind) {
      return { user: 'Workspace', person: 'Contacts', list: 'Lists' }[kind];
    },

    moveHighlight(step) {
      const count = this.results.length;
      for (let i = 1; i <= count; i += 1) {
        const idx = (((this.highlight + step * i) % count) + count) % count;
        if (!this.results[idx].disabled) {
          this.highlight = idx;
          return;
        }
      }
    },

    handleKeydown(e) {
      const open = this.showDropdown && this.results.length > 0;
      if (e.key === 'ArrowDown' && open) {
        e.preventDefault();
        this.moveHighlight(1);
      } else if (e.key === 'ArrowUp' && open) {
        e.preventDefault();
        this.moveHighlight(-1);
      } else if (e.key === 'Enter' && open && this.highlight >= 0) {
        e.preventDefault();
        this.select(this.results[this.highlight]);
      }
    },

    select(item) {
      if (!item || item.disabled) return;
      window.dispatchEvent(new CustomEvent(this.eventName, { detail: { item } }));
      this.reset(true);
    },

    reset(clearQuery) {
      if (clearQuery) this.query = '';
      this._searchGeneration += 1;
      this.results = [];
      this.showDropdown = false;
      this.highlight = -1;
      this.loading = false;
    },
  };
};
