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
// server has never read. Neither switching vault nor walking the tree is a
// navigation: the keys live in vaultSession's closure and a page load would
// take them with it, so the vault is swapped in place and the URL follows
// through replaceState. What a navigation used to reset for free, loadVault
// now resets on purpose - see resetNavigation. Every page load starts locked,
// which is why the vault is loaded by afterUnlock and never by init.
// Every id runAction can carry out. The registry answers what the caller may
// do; an id it offers that lands on no branch here would be a menu row that
// does nothing when clicked - worse than an absent one, and exactly the drift
// a server-driven menu exists to prevent. So the menu is narrowed to this
// list, and a test holds the list against the registry.
//
// `move` and `set_tags` are what the registry offers and this client does
// not do yet. They are hidden rather than shown dead.
window.VAULT_HANDLED_ENTRY_ACTIONS = [
  'edit',
  'copy_username',
  'copy_password',
  'copy_totp',
  'open_uri',
  'favorite',
  'unfavorite',
  'trash',
  'restore',
  'delete_forever',
];

// The swatches the vault offers. Not ICON_PICKER_COLORS: that list is written
// in full CSS classes, two of which the vault's colour column refuses, and the
// signed metadata holds a bare daisyUI role rather than a class.
window.VAULT_COLOR_SWATCHES = [
  { name: 'Primary', class: 'text-primary' },
  { name: 'Secondary', class: 'text-secondary' },
  { name: 'Accent', class: 'text-accent' },
  { name: 'Info', class: 'text-info' },
  { name: 'Success', class: 'text-success' },
  { name: 'Warning', class: 'text-warning' },
  { name: 'Error', class: 'text-error' },
  { name: 'Neutral', class: 'text-neutral' },
];

window.vaultBrowser = (function () {
  // Which vault to come back to, per device. It never leaves the browser: the
  // server resolves no vault, and a stored setting would tell it which one is
  // in use.
  const LAST_VAULT_KEY = 'vault.lastVault';

  function readPreference(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (err) {
      // Private browsing and a blocked-storage setting both throw on read. A
      // page that forgets which vault was last open is a smaller loss than one
      // that does not mount.
      return null;
    }
  }

  function writePreference(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (err) {
      /* nothing to do: the choice does not survive the reload */
    }
  }

  function removePreference(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (err) {
      /* the same blocked storage that refused the write */
    }
  }

  // Its own title and a red button, so an irreversible question does not read
  // like a reversible one.
  const DESTRUCTIVE = {
    title: 'This cannot be undone',
    okLabel: 'Delete for good',
    okClass: 'btn-error',
  };

  // Which actions stop and ask. The trash is not among them: asking about a
  // thing a restore undoes is what teaches people to click through the
  // question that matters.
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

  return function vaultBrowser() {
    // Kept as well as spread: the component overrides `apply` and still needs
    // the store's own, and a spread copies the function rather than leaving a
    // way back to it.
    const store = window.vaultStore();

    return {
      ...window.vaultUnlockMixin(),
      ...window.vaultPrefsMixin(),
      ...window.vaultViewPrefsMixin(),
      ...window.vaultSwitcherMixin(),
      ...store,

      // The vault this page was routed to. Null on /vault, where the vault to
      // open is resolved once the account is unlocked.
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
      // The tag being written, or null. A tag is created from the sidebar
      // section that lists them, because that is where its absence is felt.
      tagDraft: null,
      busy: false,
      // A mirror of vaultClipboard's own state, because Alpine tracks
      // property reads and cannot see into another module's closure.
      clipboard: { active: false, label: '', secondsLeft: 0, note: '' },
      loading: false,
      // What the server says may be done with each entry, keyed by uuid. It
      // is never computed here: a rule copied into the client is a rule that
      // drifts from the endpoint enforcing it.
      entryActions: {},
      // A refresh landing on a slow listing must not let the slower answer
      // describe rows that have left the screen.
      actionsGeneration: 0,
      // The row whose properties are on screen. Distinct from the selection:
      // the checkbox owns that, the row body opens this.
      panelEntry: null,
      // Slots whose decryption is in flight, so a second click can take the
      // gesture back before the value ever lands.
      revealing: {},
      // Plaintexts the user asked to see, keyed by entry and field so a value
      // can never be shown under another row. There is no timer: the clipboard
      // needs one because it is invisible and machine-wide, whereas this is on
      // screen and one click undoes it. It is dropped when the panel closes,
      // when another entry is selected, and when the vault locks.
      revealed: {},
      // The authenticator key, held as a handle rather than as a secret: the
      // HMAC key is imported non-extractable, so what sits here is something
      // javascript cannot read back. `code` is a six-digit derivative that
      // expires within the period, which is why it may live in state at all.
      totp: null,
      // The context menu: which row it belongs to and where it was raised.
      menu: { open: false, entry: null, x: 0, y: 0 },
      // The folder menu is its own, and its rows are written rather than
      // fetched: the action endpoint answers for entries and vaults, which
      // carry per-row rules - a trashed entry, a field a type does not have,
      // a role floor. A folder carries none: whoever can open the vault can
      // write any folder in it, so there is nothing to ask.
      folderMenu: { open: false, folder: null, x: 0, y: 0 },
      // The menu the empty space carries. It addresses the listing rather
      // than a row, so the endpoint has nothing to say about it either:
      // creating an entry is not something done to an existing one.
      backgroundMenu: { open: false, x: 0, y: 0 },
      // Only the newest opening may write its answer; see openMenu.
      menuGeneration: 0,

      init: function () {
        this.vaultUuid = readJson('vault-uuid');
        this.entryTypes = readJson('entry-types') || [];
        this.icons = window.ICON_PICKER_ICONS || [];
        this.colors = window.VAULT_COLOR_SWATCHES || [];
        this.restoreViewPrefs();
        this.loadPrefs();
        this.initUnlock();
        this.readCommand();
        const self = this;
        window.vaultClipboard.onChange(function (state) {
          self.clipboard = state;
        });
        window.vaultSession.onTick(function () { self.refreshTotp(); });
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
        this.resetPanel();
        this.closeMenu();
        this.pendingNewEntry = false;
        // The drafts hold typed-in plaintext, so they go with the keys.
        this.draft = null;
        this.folderDraft = null;
        this.tagDraft = null;
        this.onSwitcherLocked();
      },

      load: async function () {
        this.loading = true;
        this.error = '';
        // Every row is about to be rebuilt, and a menu left open would go on
        // describing an object that has left the listing.
        this.closeMenu();
        try {
          await this.loadVault();
          if (this.openVault) {
            await this.loadContents();
          } else {
            // Nothing to browse, so nothing the sidebar may go on showing: its
            // tags and its trash count belong to a vault that is out of reach
            // or gone, and leaving them there offers a way into neither.
            this.setData({});
            this.entryRows = [];
            this.entryActions = {};
            this.resetPanel();
          }
          await this.loadVaultActions();
          // The palette command reaches the page before there is a vault to
          // write into, so it waits here for one.
          if (this.openVault && this.pendingNewEntry) {
            this.pendingNewEntry = false;
            const first = this.entryTypes[0];
            if (first) this.newEntry(first.id);
          }
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
        const leaving = this.openVault ? String(this.openVault.uuid) : null;
        const rows = await window.vaultApi.listVaults();
        const vaults = [];
        for (const row of rows) {
          vaults.push(await window.vaultReader.readVault(window.vaultSession, row));
        }
        if (!window.vaultSession.isUnlocked()) return;
        this.vaults = vaults;
        if (this.vaultUuid) {
          const uuid = String(this.vaultUuid);
          this.openVault = vaults.find((vault) => String(vault.uuid) === uuid) || null;
          // Asked for by UUID and not found: that vault is out of reach, which
          // is worth saying. Arriving with no UUID at all is not.
          this.missing = this.openVault === null;
        } else {
          this.openVault = this.resolveLandingVault(vaults);
          this.missing = false;
        }
        // A different vault than the one on screen: its folders and the trail
        // through them belong to the vault being left, and every UUID in them
        // is meaningless here. A plain refresh keeps them, which is the point
        // of comparing rather than resetting on every load.
        const arriving = this.openVault ? String(this.openVault.uuid) : null;
        if (arriving !== leaving) {
          this.resetNavigation();
        }
        if (this.openVault) {
          this.rememberVault(this.openVault.uuid);
        } else if (!this.vaultUuid) {
          this.forgetVault();
        }
      },

      // The first moment the choice can be made: before the unlock every name
      // is a ciphertext and no key exists, so neither the server nor the page
      // could have made it earlier.
      resolveLandingVault: function (vaults) {
        const openable = vaults.filter(function (vault) {
          return !(vault.tampered || vault.unopenable || vault.unreadable);
        });
        if (!openable.length) return null;
        const remembered = String(readPreference(LAST_VAULT_KEY) || '');
        return (
          openable.find((vault) => String(vault.uuid) === remembered) ||
          openable.find((vault) => vault.is_favorite) ||
          openable[0]
        );
      },

      // The account has nothing left to open. Naming a deleted vault in the
      // address bar would hand out a link that opens a banner, and a device
      // pointing at one costs the next visit a fallback it need not make.
      forgetVault: function () {
        removePreference(LAST_VAULT_KEY);
        if (window.history && window.history.replaceState) {
          window.history.replaceState({}, '', '/vault');
        }
      },

      rememberVault: function (uuid) {
        this.vaultUuid = uuid;
        writePreference(LAST_VAULT_KEY, String(uuid));
        // replaceState, never a navigation: the keys live in a closure that a
        // page load would take with it, and the master password with them.
        if (window.history && window.history.replaceState) {
          window.history.replaceState({}, '', '/vault/' + uuid);
        }
      },

      // Told apart from `missing` on purpose: one is an account with nothing in
      // it, the other is a vault that exists for somebody else.
      hasNoVault: function () {
        return !this.loading && !this.openVault && !this.missing && this.vaults.length === 0;
      },

      // The account holds vaults and not one of them opened: every signature
      // was refused or no key here unwraps them. Nothing was routed to, so
      // `missing` says nothing, and the listing is empty for a reason the
      // empty state would get wrong - hence a state of its own.
      hasNoOpenableVault: function () {
        return !this.loading && !this.openVault && !this.missing && this.vaults.length > 0;
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
        // Every row is rebuilt from the fresh listing, so whatever the panel
        // had decrypted belongs to a row this pass no longer vouches for - a
        // mutating action elsewhere (favourite, trash, a folder or tag save)
        // reloads through here just like a manual refresh does.
        //
        // The panel itself survives when its row does. Dropping it outright
        // discarded a panel the user had just opened: a reload runs for a
        // whole round trip after saveEntry closes its dialog, and a row
        // clicked in that window opened a panel this line then closed under
        // the user - reachable by hand on a slow link, and the reason both
        // authenticator walks failed on CI and neither did locally.
        const open = this.panelEntry;
        this.resetPanel();
        if (open) {
          const fresh = this.entries.find(function (entry) {
            return entry.uuid === open.uuid;
          });
          // Gone from the listing - trashed away, deleted elsewhere - is the
          // one case where the panel has nothing left to describe.
          if (fresh) this.panelEntry = fresh;
        }
        await this.loadEntryActions();
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
        // The rows reach the screen a round trip before this answer does, and
        // startTotp is gated on it: a row opened in between was refused by a
        // map that had no entry for it yet, and no other pass would ask again.
        if (this.panelEntry && !this.totp) await this.startTotp(this.panelEntry);
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
          if (!window.VAULT_HANDLED_ENTRY_ACTIONS.includes(action.id)) return false;
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
        const lists = rows.map((entry) =>
          (this.entryActions[entry.uuid] || []).filter((action) =>
            window.VAULT_HANDLED_ENTRY_ACTIONS.includes(action.id),
          ),
        );
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
        window.vaultMenu.fit(this, 'entry-context-menu', 'menu');
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

      openFolderMenu: function (event, folder) {
        if (event && event.preventDefault) event.preventDefault();
        this.closeMenu();
        this.folderMenu = {
          open: true,
          folder: folder,
          x: (event && event.clientX) || 0,
          y: (event && event.clientY) || 0,
        };
        window.vaultMenu.fit(this, 'folder-context-menu', 'folderMenu');
      },

      closeFolderMenu: function () {
        this.folderMenu = { open: false, folder: null, x: 0, y: 0 };
      },

      openBackgroundMenu: function (event) {
        if (event && event.preventDefault) event.preventDefault();
        this.closeMenu();
        this.backgroundMenu = {
          open: true,
          x: (event && event.clientX) || 0,
          y: (event && event.clientY) || 0,
        };
        window.vaultMenu.fit(this, 'entry-listing-menu', 'backgroundMenu');
      },

      closeBackgroundMenu: function () {
        this.backgroundMenu = { open: false, x: 0, y: 0 };
      },

      closeMenu: function () {
        this.folderMenu = { open: false, folder: null, x: 0, y: 0 };
        this.backgroundMenu = { open: false, x: 0, y: 0 };
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

      isRevealed: function (fieldId) {
        if (!this.panelEntry) return false;
        // `in`, not `hasOwnProperty.call`: the latter reads through the
        // reactive proxy's getOwnPropertyDescriptor trap, which Alpine does
        // not instrument, so a slot appearing in `revealed` would update
        // this method's return value without ever re-running the template
        // that calls it.
        return (this.panelEntry.uuid + '|' + fieldId) in this.revealed;
      },

      revealedValue: function (fieldId) {
        if (!this.panelEntry) return '';
        return this.revealed[this.panelEntry.uuid + '|' + fieldId] || '';
      },

      // The one other moment a secret is decrypted, alongside copyField: this
      // one keeps the plaintext in component state instead of handing it off,
      // because the point is to show it rather than move it. The panel may
      // have moved to another entry, or the vault may have locked, while the
      // decryption was in flight - the entry captured before the await is
      // checked against panelEntry again after it, so a slow answer can never
      // land under a different row's name.
      toggleReveal: async function (fieldId) {
        const entry = this.panelEntry;
        if (!entry) return;
        const slot = entry.uuid + '|' + fieldId;
        if (Object.prototype.hasOwnProperty.call(this.revealed, slot)) {
          delete this.revealed[slot];
          return;
        }
        // A second click while the first decryption is still in flight is the
        // gesture taken back, not a second reveal. `revealed` cannot see one
        // in flight, so without this the value lands after the click meant to
        // hide it, under a button already reading "hide".
        if (Object.prototype.hasOwnProperty.call(this.revealing, slot)) {
          delete this.revealing[slot];
          return;
        }
        this.revealing[slot] = true;
        const row = this.rowFor(entry.uuid);
        if (!row || !this.openVault) {
          delete this.revealing[slot];
          return;
        }
        try {
          const value = await window.vaultReader.openField(
            window.vaultSession, this.openVault, row, fieldId
          );
          if (this.panelEntry !== entry) return;
          // Taken back while this was in flight: the slot is gone, and so is
          // the only reason to show what just arrived.
          if (!Object.prototype.hasOwnProperty.call(this.revealing, slot)) return;
          delete this.revealing[slot];
          this.revealed[slot] = value;
        } catch (err) {
          delete this.revealing[slot];
          if (err && err.reason === 'locked') return;
          if (this.panelEntry !== entry) return;
          this.error = 'That value could not be revealed.';
        }
      },

      // Opens the key once, derives the HMAC handle from it and lets the
      // base32 bytes go: what stays in state afterward is the non-extractable
      // key plus the six-digit derivative, never the shared secret itself.
      startTotp: async function (entry) {
        this.totp = null;
        if (!entry || !(entry.fieldIds || []).includes('totp')) return;
        // The same gate the copy button reads: a trashed row's key is not
        // decrypted just because the panel opened, only because copy_totp is
        // still on offer for it.
        if (!this.hasAction(entry, 'copy_totp')) return;
        const row = this.rowFor(entry.uuid);
        if (!row || !this.openVault) return;
        let key;
        let parsed;
        try {
          const uri = await window.vaultReader.openField(
            window.vaultSession, this.openVault, row, 'totp'
          );
          parsed = window.vaultCrypto.parseOtpauth(uri);
          key = await window.vaultCrypto.importTotpKey(parsed);
        } catch (err) {
          if (err && err.reason === 'locked') return;
          // The panel may have moved to another entry, or closed, while this
          // was in flight - a slow failure must not land under a row that is
          // no longer the one on screen.
          if (this.panelEntry !== entry) return;
          // Localised like a failed signature: this line says it cannot be
          // read, and the rest of the entry is shown as usual.
          this.totp = { entryUuid: entry.uuid, unreadable: true };
          return;
        }
        // Same check on the success path: an identity comparison, not a uuid
        // one, so a panel closed mid-flight (panelEntry null) is caught too.
        if (this.panelEntry !== entry) return;
        this.totp = {
          entryUuid: entry.uuid,
          key: key,
          digits: parsed.digits,
          period: parsed.period,
          code: '',
          secondsLeft: 0,
          unreadable: false,
        };
        await this.refreshTotp();
      },

      // Driven by the session's own tick, which already beats once a second
      // for the lock countdown. A second interval could outlive the component;
      // this one dies with the session. It does not push the lock back either:
      // only real DOM events call noteActivity.
      refreshTotp: async function () {
        const totp = this.totp;
        if (!totp || totp.unreadable || !totp.key) return;
        const now = Date.now() / 1000;
        const code = await window.vaultCrypto.totpCode(totp.key, totp, now);
        if (this.totp !== totp) return;
        totp.code = code;
        totp.secondsLeft = window.vaultCrypto.totpSecondsRemaining(totp, now);
      },

      // Reached from the row menu as well as from the panel, so it opens and
      // derives on its own rather than reading panel state. It copies the
      // derived code: copyField would copy the stored value, which is the key.
      copyTotp: async function (entry) {
        const row = this.rowFor(entry.uuid);
        if (!row || !this.openVault) return;
        try {
          const uri = await window.vaultReader.openField(
            window.vaultSession, this.openVault, row, 'totp'
          );
          const parsed = window.vaultCrypto.parseOtpauth(uri);
          const key = await window.vaultCrypto.importTotpKey(parsed);
          const code = await window.vaultCrypto.totpCode(key, parsed, Date.now() / 1000);
          await window.vaultClipboard.copy('Authenticator code', code, { transient: true });
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = 'That authenticator code could not be copied.';
        }
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
        this.revealed = {};
        this.revealing = {};
        return this.startTotp(entry);
      },

      // The one place that takes the panel and everything it decrypted down
      // together - a partial reset would leave a revealed value or a totp key
      // handle attached to a row the screen no longer shows one for.
      resetPanel: function () {
        this.panelEntry = null;
        this.revealed = {};
        this.revealing = {};
        this.totp = null;
      },

      closePanel: function () {
        this.resetPanel();
      },

      // Every navigation the store knows about lands here - forward, back,
      // up, a view, a tag - so this is the one place that has to take the
      // panel with it. The row it describes has left the screen.
      apply: function (state) {
        store.apply.call(this, state);
        this.resetPanel();
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
        if (action.id === 'copy_totp') return this.copyTotp(entry);
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
        const self = this;
        const call = {
          trash: function (uuid) { return window.vaultApi.trashEntry(uuid); },
          restore: function (uuid) { return window.vaultApi.restoreEntry(uuid); },
          delete_forever: function (uuid) { return window.vaultApi.purgeEntry(uuid); },
          // Not a flag the server flips: is_favorite is inside the signed
          // payload, so changing it is a re-signature of the whole record.
          favorite: function (uuid) { return self.setFavorite(uuid, true); },
          unfavorite: function (uuid) { return self.setFavorite(uuid, false); },
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

      // The row as the server stores it, not as the listing decrypted it: the
      // signature covers the ciphertexts, so they are what has to be signed
      // again.
      setFavorite: async function (uuid, value) {
        const row = this.rowFor(uuid);
        if (!row || !this.openVault) return;
        const body = await window.buildEntryResignRequest(
          window.vaultSession,
          this.openVault,
          row,
          { is_favorite: value },
        );
        await window.vaultApi.updateEntry(uuid, body);
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

      // The authenticator key is not a value to type over. The dialog shows
      // whether one is set and offers two deliberate gestures; the ciphertext
      // travels untouched until one of them is used.
      totpFieldState: function () {
        if (!this.draft) return 'none';
        // The same schema formFields() reads. Without it a type that declares
        // no authenticator key is still offered one, and saveEntry seals a
        // field the type does not have.
        const type = this.typeFor(this.draft.type);
        const declared = (type ? type.fields : []).some(function (field) {
          return field.kind === 'totp';
        });
        if (!declared) return 'unsupported';
        if (this.draft.totpInput !== null && this.draft.totpInput !== undefined) {
          return 'editing';
        }
        return this.draft.hasTotp && !this.draft.totpRemoved ? 'set' : 'none';
      },

      startTotpEntry: function () {
        this.draft.totpInput = '';
        this.draft.totpRemoved = false;
      },

      cancelTotpEntry: function () {
        this.draft.totpInput = null;
      },

      removeTotp: function () {
        this.draft.totpInput = null;
        this.draft.totpRemoved = true;
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
          carriedFields: {},
          keyVersion: (this.openVault && this.openVault.key_version) || 1,
          hasTotp: false,
          totpInput: null,
          totpRemoved: false,
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
        const carriedFields = {};
        const type = this.typeFor(entry.type);
        const editable = new Set(
          (type ? type.fields : [])
            .filter(function (field) { return field.kind !== 'totp'; })
            .map(function (field) { return field.field_id; }),
        );
        try {
          for (const field of type ? type.fields : []) {
            if (field.kind === 'totp') continue;
            if (!(entry.fieldIds || []).includes(field.field_id)) continue;
            values[field.field_id] = await window.vaultReader.openField(
              window.vaultSession, this.openVault, row, field.field_id
            );
          }
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = 'That entry could not be opened for editing.';
          return;
        }
        if (!window.vaultSession.isUnlocked()) return;
        // Everything the dialog does not edit - the authenticator key, and any
        // field a future type declares - travels as its ciphertext. Rebuilding
        // the record from the form alone would delete it.
        (row.entry_fields || []).forEach(function (field) {
          if (!editable.has(field.field_id)) {
            carriedFields[field.field_id] = field.encrypted_value;
          }
        });
        this.draft = {
          uuid: entry.uuid,
          type: entry.type,
          folder: entry.folder,
          tags: [...entry.tags],
          favorite: entry.favorite,
          name: entry.name,
          notes: '',
          // The row's own ciphertext, carried rather than opened: the write is
          // a full signed replacement, so a draft that dropped it would sign
          // an entry whose notes are gone.
          encryptedNotes: row.encrypted_notes || '',
          values: values,
          carriedFields: carriedFields,
          keyVersion: row.key_version || 1,
          hasTotp: (entry.fieldIds || []).includes('totp'),
          totpInput: null,
          totpRemoved: false,
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
        // Three outcomes, and only one of them seals: a typed key becomes the
        // uri that will be stored, a removed one is dropped from both maps so
        // the write deletes it, and an untouched one stays in carriedFields.
        const values = Object.assign({}, draft.values);
        const carriedFields = Object.assign({}, draft.carriedFields);
        if (draft.totpInput !== null && draft.totpInput !== undefined
            && String(draft.totpInput).trim()) {
          try {
            values.totp = window.vaultCrypto.normalizeTotpInput(
              draft.totpInput, { label: name }
            );
          } catch (err) {
            this.error = 'That authenticator key could not be read. Paste the key or '
              + 'the otpauth:// address the service showed you.';
            return;
          }
          delete carriedFields.totp;
        } else if (draft.totpRemoved) {
          delete carriedFields.totp;
          delete values.totp;
        }
        this.busy = true;
        try {
          const body = await window.buildEntryWriteRequest(
            window.vaultSession,
            this.openVault,
            Object.assign({}, draft, {
              name: name, values: values, carriedFields: carriedFields,
            }),
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

      renameFolder: function (folder) {
        this.closeMenu();
        // Everything the signature covers travels with the draft: a rename
        // re-signs the whole record, so dropping the parent or the position
        // here would move the folder to the root as a side effect of
        // renaming it.
        this.folderDraft = {
          uuid: folder.uuid,
          parent: folder.parent || null,
          name: folder.name,
          position: folder.position || 0,
          existing: true,
        };
      },

      newFolder: function () {
        this.closeMenu();
        const parent = this.view === 'all' && !this.tagFilter ? this.folderUuid : null;
        this.folderDraft = {
          uuid: window.vaultCrypto.uuidV7(),
          parent: parent,
          name: '',
          // position orders siblings, so it counts siblings: the whole-tree
          // count would put every new folder last in its own level and grow
          // without meaning.
          position: this.folders.filter((row) => row.parent === parent).length,
        };
      },

      newTag: function () {
        this.closeMenu();
        this.tagDraft = {
          uuid: window.vaultCrypto.uuidV7(),
          name: '',
          color: window.TAG_CHIP_COLORS[1].value,
        };
      },

      closeTagDialog: function () {
        this.tagDraft = null;
      },

      saveTag: async function () {
        const draft = this.tagDraft;
        if (!draft || !this.openVault) return;
        const name = (draft.name || '').trim();
        if (!name) return;
        this.busy = true;
        try {
          const body = await window.buildTagWriteRequest(
            window.vaultSession,
            this.openVault,
            Object.assign({}, draft, { name: name }),
          );
          await window.vaultApi.createTag(body);
          this.tagDraft = null;
          await this.load();
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = 'That tag could not be created. Try again.';
        } finally {
          this.busy = false;
        }
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
          if (draft.existing) {
            await window.vaultApi.updateFolder(draft.uuid, body);
          } else {
            await window.vaultApi.createFolder(body);
          }
          this.folderDraft = null;
          await this.load();
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = draft.existing
            ? 'That folder could not be renamed. Try again.'
            : 'That folder could not be created. Try again.';
        } finally {
          this.busy = false;
        }
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

      shortDate: function (value) {
        return window.vaultFormat.shortDate(value);
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
