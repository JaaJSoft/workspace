// The browser: one vault, opened, with its folders, its tags and its entries.
//
// Three files meet here and the split is deliberate. vault_reader.js turns
// stored rows into readable ones and decides what may be decrypted;
// vault_store.js decides what belongs on screen over data already in the
// clear; this file is the controller between them and the network. Nothing
// here decrypts, and nothing here re-implements a rule the server owns - a
// menu is what POST /api/v1/vault/actions answers, never what a condition in
// this file concludes.
//
// The vault is in the URL and the folder is not: the server assigns a vault's
// UUID and it leaks nothing, whereas a folder is named by a ciphertext the
// server has never read. So switching vault is a navigation, and walking the
// tree is not.
window.vaultBrowser = (function () {
  const COLLAPSED_KEY = 'vault.sidebar.collapsed';
  const VIEW_MODE_KEY = 'vault.browser.viewMode';

  // Which actions stop and ask. The trash is not among them: it is reversible
  // and asking about a reversible thing is what teaches people to click
  // through the question that matters. Destroying is the one no restore
  // reaches, so it is the one that asks.
  // What an irreversible question looks like: its own title and a red button,
  // so it does not read like the reversible ones.
  const DESTRUCTIVE = {
    title: 'This cannot be undone',
    okLabel: 'Delete for good',
    okClass: 'btn-error',
  };

  const CONFIRMS = {
    delete_forever: (count) =>
      count === 1
        ? 'Destroy this entry? It is not in the trash afterwards - it is gone.'
        : 'Destroy these ' + count + ' entries? They are not in the trash afterwards - they are gone.',
  };

  function readJson(elementId) {
    const element = document.getElementById(elementId);
    if (!element) return null;
    try {
      return JSON.parse(element.textContent);
    } catch (err) {
      return null;
    }
  }

  function readPreference(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (err) {
      // Private browsing and a blocked-storage setting both throw on read.
      // A sidebar that remembers nothing is a smaller loss than a page that
      // does not mount.
      return null;
    }
  }

  function writePreference(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (err) {
      /* nothing to do: the preference simply does not survive the reload */
    }
  }

  return function vaultBrowser() {
    // Kept as well as spread: the component overrides `apply` and still needs
    // the store's own, and a spread copies the function rather than leaving a
    // way back to it.
    const store = window.vaultStore();

    return {
      ...window.vaultUnlockMixin(),
      ...store,

      // The keys live in vaultSession's closure, so every page load starts
      // locked and the vault is loaded by afterUnlock rather than by init.
      // That is also what a vault switch costs: the URL names the vault, so
      // moving to another one is a navigation, and a navigation drops the
      // keys.
      //
      // The vault this page was routed to. Null on /vault, where the listing
      // renders instead.
      vaultUuid: null,
      // Every vault the account can open, for the switcher.
      vaults: [],
      // The one being browsed, with its name opened, or null.
      openVault: null,
      // The stored rows behind `entries`, still sealed. A field opened on
      // demand is opened from one of these.
      entryRows: [],
      // The routed UUID names no vault this account can reach. Never a 404
      // from the server - that would say it exists in another account - so
      // saying it is the page's job.
      missing: false,
      // The field schema of each entry type, rendered by the server from the
      // Python registry. The New menu is built from it rather than from a
      // list written here, so adding a type stays one class.
      entryTypes: [],
      // A palette command asked for an entry before the vault was open. It
      // is honoured once the listing lands, because the form needs a vault to
      // write into.
      pendingNewEntry: false,
      // The entry being written, or null. It holds plaintext - it is a form -
      // so a lock drops it with everything else.
      draft: null,
      // The folder being created, or null. A folder carries a name and
      // nothing else the user picks, so its dialog is one input.
      folderDraft: null,
      busy: false,
      // A mirror of vaultClipboard's own state, because Alpine tracks
      // property reads and cannot see into another module's closure.
      clipboard: { active: false, label: '', secondsLeft: 0, note: '' },
      collapsed: false,
      // The sidebar is a column from md up and an overlay below it, so on a
      // phone it has to be opened before it is anything.
      mobileNav: false,
      loading: false,
      error: '',
      // What the server says may be done with each entry, keyed by uuid. It
      // is never computed here: a rule copied into the client is a rule that
      // drifts from the endpoint enforcing it.
      entryActions: {},
      // Two listings can be in flight at once - a refresh landing on a slow
      // one - and the slower answer must not describe rows that left the
      // screen. Only the newest generation is allowed to write.
      actionsGeneration: 0,
      // The row whose properties are on screen. Distinct from the selection:
      // the checkbox owns that, the row body opens this.
      panelEntry: null,
      // The context menu: which row it belongs to and where it was raised.
      menu: { open: false, entry: null, x: 0, y: 0 },
      // A menu asks the endpoint again as it opens, because the batch behind
      // the listing may be minutes old by then. Only the newest opening is
      // allowed to write, or a slow answer would re-describe a row the user
      // has already moved off.
      menuGeneration: 0,

      init: function () {
        this.vaultUuid = readJson('vault-uuid');
        this.entryTypes = readJson('entry-types') || [];
        this.collapsed = readPreference(COLLAPSED_KEY) === 'true';
        const viewMode = readPreference(VIEW_MODE_KEY);
        if (viewMode) this.viewMode = viewMode;
        this.initUnlock();
        this.readCommand();
        const self = this;
        window.vaultClipboard.onChange(function (state) {
          self.clipboard = state;
        });
      },

      // A palette command is a plain link, so the only thing it can carry is
      // a query string. Locking needs no vault; creating an entry needs the
      // dialog, which arrives with the entry form.
      readCommand: function () {
        const action = new URLSearchParams(window.location.search).get('action');
        if (action === 'lock') {
          window.vaultSession.lock();
          return;
        }
        if (action === 'new') this.pendingNewEntry = true;
      },

      afterUnlock: async function () {
        await this.load();
      },

      onLocked: function () {
        // A secret on the clipboard outlives the keys that opened it, so a
        // lock takes it back rather than leaving it for the next person at
        // this machine.
        window.vaultClipboard.cancel();
        this.setData({});
        this.vaults = [];
        this.entryRows = [];
        this.openVault = null;
        this.error = '';
        this.entryActions = {};
        this.panelEntry = null;
        this.closeMenu();
        this.pendingNewEntry = false;
        // The drafts hold typed-in plaintext, so they go with the keys.
        this.draft = null;
        this.folderDraft = null;
      },

      load: async function () {
        if (!this.vaultUuid) return;
        this.loading = true;
        this.error = '';
        try {
          await this.loadVault();
          if (this.openVault) await this.loadContents();
        } catch (err) {
          // A lock caught the rows mid-flight. There is nothing to report:
          // the user is looking at the password form, and a message here
          // would blame the listing for an idle timeout.
          if (err && err.reason === 'locked') return;
          this.error = 'This vault could not be loaded. Try again.';
        } finally {
          this.loading = false;
        }
      },

      loadVault: async function () {
        const rows = await window.vaultApi.listVaults();
        const vaults = [];
        for (const row of rows) {
          vaults.push(await this.readVault(row));
        }
        if (!window.vaultSession.isUnlocked()) return;
        this.vaults = vaults;
        const uuid = String(this.vaultUuid);
        this.openVault = vaults.find((vault) => String(vault.uuid) === uuid) || null;
        this.missing = this.openVault === null;
      },

      // Same two steps as the listing's cards: verify, then open the name.
      // A vault whose signature does not check keeps no name at all - showing
      // one "just to identify it" would render unverified data.
      readVault: async function (row) {
        const V = window.vaultCrypto;
        const payload = V.vaultMetadataPayload(
          Object.assign({}, row, { vault_uuid: row.uuid })
        );
        try {
          await window.vaultSession.verifyVaultMetadata(payload, row.metadata_sig);
        } catch (err) {
          if (err && err.reason === 'locked') throw err;
          return Object.assign({}, row, { tampered: true, name: '' });
        }
        if (!row.wrapped_key) {
          return Object.assign({}, row, { unopenable: true, name: '' });
        }
        try {
          const metaKey = await window.vaultSession.openVaultKey(row.uuid, row.wrapped_key);
          const plaintext = await V.open(
            metaKey,
            V.fromBase64Url(row.encrypted_name),
            V.AD.vaultFieldAd(row.uuid, 'name')
          );
          return Object.assign({}, row, { name: new TextDecoder().decode(plaintext) });
        } catch (err) {
          if (err && err.reason === 'locked') throw err;
          return Object.assign({}, row, { unreadable: true, name: '' });
        }
      },

      rowFor: function (uuid) {
        return this.entryRows.find(function (row) { return row.uuid === uuid; }) || null;
      },

      loadContents: async function () {
        const vault = this.openVault;
        // The trash is a view over rows this vault already holds, so they
        // travel with the rest: reaching for it must not cost a round trip,
        // and restoring an entry must not have to reconcile two listings.
        const [folderRows, tagRows, liveRows, trashedRows] = await Promise.all([
          window.vaultApi.listFolders(vault.uuid),
          window.vaultApi.listTags(vault.uuid),
          window.vaultApi.listEntries(vault.uuid),
          window.vaultApi.listEntries(vault.uuid, { trashed: true }),
        ]);
        const session = window.vaultSession;
        const reader = window.vaultReader;
        const folders = await reader.readFolders(session, vault, folderRows);
        const tags = await reader.readTags(session, vault, tagRows);
        const rows = [...liveRows, ...trashedRows];
        const entries = await reader.readEntries(session, vault, rows);
        // Neither await is atomic with the lock: an idle timeout can fire in
        // between, and by then onLocked has already emptied the store.
        // Assigning anyway would put opened names back into a locked page.
        if (!session.isUnlocked()) return;
        // Kept beside the opened rows: opening one field later needs the
        // ciphertexts, and an opened row deliberately carries none.
        this.entryRows = rows;
        this.setData({
          folders: folders.rows,
          tags: tags.rows,
          entries: entries.rows,
          // Only entries are counted: a folder or a tag that fails to verify
          // is a broken listing, not a row hidden from the user, and the
          // banner speaks about entries.
          tamperedCount: entries.tamperedCount,
        });
        this.panelEntry = null;
        await this.loadEntryActions();
        // The palette command reaches the page before there is a vault to
        // write into, so it waits here for one.
        if (this.pendingNewEntry) {
          this.pendingNewEntry = false;
          const first = this.entryTypes[0];
          if (first) this.newEntry(first.id);
        }
      },

      loadEntryActions: async function () {
        this.actionsGeneration += 1;
        const generation = this.actionsGeneration;
        const uuids = this.entries.map(function (entry) { return entry.uuid; });
        if (!uuids.length) {
          this.entryActions = {};
          return;
        }
        let answer;
        try {
          answer = await window.vaultApi.fetchEntryActions(uuids);
        } catch (err) {
          // The names are open and the listing is usable. Blanking a working
          // page over a lost menu would cost the user more than the menu.
          if (generation === this.actionsGeneration) this.entryActions = {};
          return;
        }
        if (generation !== this.actionsGeneration) return;
        if (!window.vaultSession.isUnlocked()) return;
        this.entryActions = answer;
      },

      refresh: function () {
        return this.load();
      },

      // ---- the shell -------------------------------------------------------

      isMember: function (vault) {
        return Boolean(
          vault && vault.owner_account_uuid !== window.vaultSession.accountUuid()
        );
      },

      // ---- actions ---------------------------------------------------------

      // Both favourite verbs come back from the registry: it answers what the
      // caller may do, not what the row is. Choosing between two exclusives
      // from a flag the client already holds is not a rule copied from the
      // server.
      actionsFor: function (entry) {
        const actions = (entry && this.entryActions[entry.uuid]) || [];
        const favorite = entry && entry.favorite;
        return actions.filter(function (action) {
          if (action.id === 'favorite') return !favorite;
          if (action.id === 'unfavorite') return !!favorite;
          return true;
        });
      },

      hasAction: function (entry, actionId) {
        return this.actionsFor(entry).some(function (action) {
          return action.id === actionId;
        });
      },

      // What the whole selection can be told to do: the actions the registry
      // marks as working on a batch, kept only where every selected row was
      // offered them. The unfiltered lists are intersected on purpose - the
      // favourite verbs are then decided over the selection as a whole, which
      // intersecting the filtered lists could not do (one row offers only
      // `favorite`, another only `unfavorite`, and the answer would be
      // neither).
      bulkActions: function () {
        const rows = this.selectedEntries();
        if (!rows.length) return [];
        const lists = rows.map((entry) => this.entryActions[entry.uuid] || []);
        const shared = lists[0].filter(
          (action) =>
            action.bulk &&
            lists.every((list) => list.some((other) => other.id === action.id)),
        );
        const allFavorite = rows.every((entry) => entry.favorite);
        const noneFavorite = rows.every((entry) => !entry.favorite);
        return shared.filter(function (action) {
          if (action.id === 'favorite') return !allFavorite;
          if (action.id === 'unfavorite') return !noneFavorite;
          return true;
        });
      },

      // ---- the menu --------------------------------------------------------

      // Opened on the row it names, and refilled from the endpoint as it
      // opens: what the listing fetched may be minutes old, and a menu is the
      // moment a stale answer turns into a request the server refuses.
      openMenu: async function (event, entry) {
        if (event && event.preventDefault) event.preventDefault();
        this.menuGeneration += 1;
        const generation = this.menuGeneration;
        this.menu = {
          open: true,
          entry: entry,
          x: (event && event.clientX) || 0,
          y: (event && event.clientY) || 0,
        };
        let answer;
        try {
          answer = await window.vaultApi.fetchEntryActions([entry.uuid]);
        } catch (err) {
          // The cached list is what the menu already shows; leaving it beats
          // blanking a menu the user just opened.
          return;
        }
        if (generation !== this.menuGeneration) return;
        if (!window.vaultSession.isUnlocked()) return;
        this.entryActions = Object.assign({}, this.entryActions, answer);
      },

      menuActions: function () {
        return this.actionsFor(this.menu.entry);
      },

      closeMenu: function () {
        // The generation moves too: a request in flight for the menu just
        // closed must not reopen anything when it lands.
        this.menuGeneration += 1;
        this.menu = { open: false, entry: null, x: 0, y: 0 };
      },

      // ---- the panel -------------------------------------------------------

      panelHasAction: function (actionId) {
        return this.hasAction(this.panelEntry, actionId);
      },

      // Which fields the row carries, learnt from the listing without opening
      // one: it is what tells a login with an authenticator key from one
      // without, and it is the whole reason the reader collects field ids.
      panelCarries: function (fieldId) {
        const entry = this.panelEntry;
        return Boolean(entry && (entry.fieldIds || []).includes(fieldId));
      },

      // ---- gestures --------------------------------------------------------

      // The gesture of the file browser: the checkbox selects, the row body
      // opens. A row that both selected and opened would make every attempt
      // to pick two rows navigate away from the first.
      openFolderFromRow: function (folder) {
        this.openFolder(folder.uuid);
      },

      openEntryFromRow: function (entry) {
        this.panelEntry = entry;
      },

      closePanel: function () {
        this.panelEntry = null;
      },

      // Every navigation the store knows about lands here - forward, back,
      // up, a view, a tag - so this is the one place that has to take the
      // panel with it. The row it describes has left the screen.
      apply: function (state) {
        store.apply.call(this, state);
        this.panelEntry = null;
        this.closeMenu();
      },

      // Every menu item and every panel button comes through here. The action
      // was offered by the endpoint, but the menu it came from may be old, so
      // the offer is checked again before anything runs - a stale menu must
      // not produce a request the server is about to refuse.
      runAction: async function (action, entry) {
        this.closeMenu();
        if (!entry || !this.hasAction(entry, action.id)) return;
        if (action.id === 'edit') return this.editEntry(entry);
        const copies = {
          copy_username: ['Username', 'username', false],
          copy_password: ['Password', 'password', true],
        }[action.id];
        if (copies) return this.copyField(entry, copies[0], copies[1], copies[2]);
        if (action.id === 'open_uri') return this.openUri(entry);
        if (CONFIRMS[action.id]) {
          const confirmed = await this.confirm(CONFIRMS[action.id](1), DESTRUCTIVE);
          if (!confirmed) return;
        }
        await this.applyTo(action.id, [entry]);
      },

      // The selection bar. The offer was already narrowed to what every
      // selected row supports, and the question is asked once for the batch -
      // one confirmation per row would train the user to click through them.
      runBulkAction: async function (action) {
        const rows = this.selectedEntries();
        if (!rows.length) return;
        const offered = this.bulkActions().some(function (candidate) {
          return candidate.id === action.id;
        });
        if (!offered) return;
        if (CONFIRMS[action.id]) {
          const confirmed = await this.confirm(
            CONFIRMS[action.id](rows.length), DESTRUCTIVE
          );
          if (!confirmed) return;
        }
        await this.applyTo(action.id, rows);
      },

      // One row or many, the same path. It stops at the first refusal rather
      // than pressing on: a batch that half-happened and said nothing is
      // worse than one that stopped and said where.
      applyTo: async function (actionId, rows) {
        const call = {
          trash: function (uuid) { return window.vaultApi.trashEntry(uuid); },
          restore: function (uuid) { return window.vaultApi.restoreEntry(uuid); },
          delete_forever: function (uuid) { return window.vaultApi.purgeEntry(uuid); },
        }[actionId];
        if (!call) return;
        this.busy = true;
        let failure = null;
        try {
          for (const row of rows) {
            await call(row.uuid);
          }
        } catch (err) {
          if (err && err.reason === 'locked') return;
          failure = err;
        } finally {
          this.busy = false;
        }
        // The reload comes first and the message after it: reloading is what
        // makes the listing describe what actually happened, and it clears
        // the error line on its way in.
        await this.load();
        if (failure) {
          this.error =
            'That change could not be applied to every entry. The listing above is current.';
        }
      },

      // The bare global, not window.AppDialog: dialogs.js declares it with a
      // top-level `const`, which lives in the global lexical scope and never
      // becomes a property of window. Reading it through window returns
      // undefined, and this function would then answer yes to every question
      // without asking one. Every other module calls it bare; so does this.
      //
      // AppDialog.confirm takes an options object rather than a string, and
      // handing it one leaves every field on its default - the user is then
      // asked "Are you sure?" about an entry they are about to destroy.
      confirm: function (message, options) {
        if (typeof AppDialog === 'undefined') return Promise.resolve(true);
        return AppDialog.confirm(Object.assign({ message: message }, options || {}));
      },

      // The one moment a secret is decrypted. It is opened, handed to the
      // platform and dropped: nothing on this side keeps a reference, which
      // is why the value never becomes component state on the way through.
      copyField: async function (entry, label, fieldId, transient) {
        const row = this.rowFor(entry.uuid);
        if (!row || !this.openVault) return;
        try {
          const value = await window.vaultReader.openField(
            window.vaultSession, this.openVault, row, fieldId
          );
          if (!value) return;
          await window.vaultClipboard.copy(label, value, { transient: transient });
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = 'That value could not be copied.';
        }
      },

      // Opened rather than rendered as a link: the address is a field of the
      // entry, and a link sitting in the page would leak it to a referrer
      // header and to anything reading the DOM.
      openUri: async function (entry) {
        const row = this.rowFor(entry.uuid);
        if (!row || !this.openVault) return;
        let uri;
        try {
          uri = await window.vaultReader.openField(
            window.vaultSession, this.openVault, row, 'uri'
          );
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = 'That website could not be opened.';
          return;
        }
        // A stored value decides the destination, so the scheme is checked
        // here: javascript: and data: are the two that turn a saved address
        // into code running on this page.
        if (!/^https?:\/\//i.test(uri || '')) {
          this.error = 'That entry does not hold a web address.';
          return;
        }
        window.open(uri, '_blank', 'noopener,noreferrer');
      },

      cancelCopy: function () {
        return window.vaultClipboard.cancel();
      },

      // ---- writing ---------------------------------------------------------

      // What the form shows: the type's own schema, minus the authenticator
      // key. A TOTP secret is a shared key rather than a value to type into a
      // box, and offering a text input for one is offering to overwrite it
      // with whatever was typed.
      formFields: function () {
        const type = this.draft && this.typeFor(this.draft.type);
        return (type ? type.fields : []).filter(function (field) {
          return field.kind !== 'totp';
        });
      },

      newEntry: function (typeId) {
        this.closeMenu();
        this.draft = {
          uuid: window.vaultCrypto.uuidV7(),
          // Created where the user is looking: a filtered listing is a claim
          // about where the row belongs, so it seeds the folder and the tag.
          folder: this.view === 'all' && !this.tagFilter ? this.folderUuid : null,
          tags: this.tagFilter ? [this.tagFilter] : [],
          type: typeId,
          favorite: false,
          name: '',
          notes: '',
          values: {},
          entryVersion: 1,
          isNew: true,
        };
      },

      // The row on screen holds a name and a login, never a secret, so the
      // form opens what it is about to let the user edit - and only that.
      editEntry: async function (entry) {
        this.closeMenu();
        const row = this.rowFor(entry.uuid);
        if (!row || !this.openVault) return;
        const values = {};
        const type = this.typeFor(entry.type);
        for (const field of type ? type.fields : []) {
          if (field.kind === 'totp') continue;
          if (!(entry.fieldIds || []).includes(field.field_id)) continue;
          values[field.field_id] = await window.vaultReader.openField(
            window.vaultSession, this.openVault, row, field.field_id
          );
        }
        if (!window.vaultSession.isUnlocked()) return;
        this.draft = {
          uuid: entry.uuid,
          type: entry.type,
          folder: entry.folder,
          tags: [...entry.tags],
          favorite: entry.favorite,
          name: entry.name,
          notes: '',
          values: values,
          entryVersion: row.entry_version || 1,
          isNew: false,
        };
      },

      closeEntryDialog: function () {
        this.draft = null;
      },

      toggleDraftTag: function (uuid) {
        const tags = this.draft.tags;
        this.draft.tags = tags.includes(uuid)
          ? tags.filter(function (value) { return value !== uuid; })
          : [...tags, uuid];
      },

      saveEntry: async function () {
        const draft = this.draft;
        if (!draft || !this.openVault) return;
        const name = (draft.name || '').trim();
        // The name is the only thing a listing shows: a row without one is a
        // row the user cannot tell from another.
        if (!name) return;
        this.busy = true;
        try {
          const body = await window.buildEntryWriteRequest(
            window.vaultSession,
            this.openVault,
            Object.assign({}, draft, { name: name }),
          );
          if (draft.isNew) {
            await window.vaultApi.createEntry(body);
          } else {
            await window.vaultApi.updateEntry(draft.uuid, body);
          }
          this.draft = null;
          await this.load();
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = 'That entry could not be saved. Try again.';
        } finally {
          this.busy = false;
        }
      },

      // Dropping a tag or a folder rewrites entries the user did not name, so
      // both go through vaultResign - which re-signs first and removes after.
      // The server cannot do the repair: producing one of those signatures
      // means forging the account's.
      deleteTag: async function (tag) {
        const confirmed = await this.confirm(
          'Remove the tag "' + tag.name + '" from every entry that carries it?',
          { title: 'Delete tag', okLabel: 'Delete', okClass: 'btn-error' }
        );
        if (!confirmed) return;
        this.busy = true;
        let failed = false;
        try {
          await window.vaultResign.deleteTagSafely(
            this.openVault, tag.uuid, this.entryRows
          );
        } catch (err) {
          if (err && err.reason === 'locked') return;
          failed = true;
        } finally {
          this.busy = false;
        }
        await this.load();
        if (failed) {
          this.error =
            'The tag was not removed. Every entry it touched is unchanged - nothing was left half-written.';
        }
      },

      deleteFolder: async function (folder) {
        const confirmed = await this.confirm(
          'Delete the folder "' + folder.name + '"? Its entries move to the vault root.',
          { title: 'Delete folder', okLabel: 'Delete', okClass: 'btn-error' }
        );
        if (!confirmed) return;
        this.busy = true;
        let failed = false;
        try {
          await window.vaultResign.deleteFolderSafely(
            this.openVault, folder.uuid, this.folders, this.entryRows
          );
        } catch (err) {
          if (err && err.reason === 'locked') return;
          failed = true;
        } finally {
          this.busy = false;
        }
        await this.load();
        if (failed) {
          this.error =
            'The folder was not deleted. Each of its levels is written whole or not at all, so nothing is half-moved.';
        }
      },

      newFolder: function () {
        this.closeMenu();
        this.folderDraft = {
          uuid: window.vaultCrypto.uuidV7(),
          parent: this.view === 'all' && !this.tagFilter ? this.folderUuid : null,
          name: '',
          position: this.folders.length,
        };
      },

      closeFolderDialog: function () {
        this.folderDraft = null;
      },

      saveFolder: async function () {
        const draft = this.folderDraft;
        if (!draft || !this.openVault) return;
        const name = (draft.name || '').trim();
        if (!name) return;
        this.busy = true;
        try {
          const body = await window.buildFolderWriteRequest(
            window.vaultSession,
            this.openVault,
            Object.assign({}, draft, { name: name }),
          );
          await window.vaultApi.createFolder(body);
          this.folderDraft = null;
          await this.load();
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = 'That folder could not be created. Try again.';
        } finally {
          this.busy = false;
        }
      },

      toggleCollapsed: function () {
        this.collapsed = !this.collapsed;
        writePreference(COLLAPSED_KEY, String(this.collapsed));
      },

      setViewMode: function (mode) {
        this.viewMode = mode;
        writePreference(VIEW_MODE_KEY, mode);
      },

      heading: function () {
        if (this.view === 'trash') return 'Trash';
        if (this.view === 'favorites') return 'Favorites';
        if (this.tagFilter) {
          const tag = this.tagById(this.tagFilter);
          return tag ? tag.name : 'Tag';
        }
        const folder = this.folderById(this.folderUuid);
        return folder ? folder.name : 'All entries';
      },

      // The type registry is the server's; the browser only looks a row's type
      // up in what it was handed. A row whose type no proxy claims - possible,
      // since `type` is a Python-side choice - still gets a row rather than
      // an exception.
      typeFor: function (typeId) {
        return this.entryTypes.find(function (type) {
          return type.id === typeId;
        }) || null;
      },

      typeLabel: function (typeId) {
        const type = this.typeFor(typeId);
        return type ? type.label : typeId;
      },

      typeIcon: function (typeId) {
        const type = this.typeFor(typeId);
        return type ? type.icon : 'file-question';
      },

      // The server sends ISO timestamps; the table has a 32-unit column. The
      // locale is the browser's, deliberately: nothing about a vault is
      // server-rendered, this included.
      shortDate: function (value) {
        if (!value) return '-';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '-';
        return date.toLocaleDateString(undefined, {
          year: 'numeric', month: 'short', day: 'numeric',
        });
      },

      vaultName: function () {
        return this.openVault ? this.openVault.name : '';
      },

      trail: function () {
        return this.breadcrumbs(this.vaultName());
      },
    };
  };
})();
