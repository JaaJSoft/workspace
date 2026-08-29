// The vault listing: every vault the account can open, as a card carrying the
// menu the server says it may offer. The unlock gate in front of it is
// vault_unlock.js, shared with the browser.

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

window.vaultApp = (function () {
  const VIEW_MODE_KEY = 'vault.list.viewMode';
  const DEFAULT_PREFS = { lockAfterMinutes: 5, defaultSort: 'default' };

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
      // Private browsing and a blocked-storage setting both throw on read. A
      // listing that forgets its view is a smaller loss than one that does
      // not mount.
      return null;
    }
  }

  function writePreference(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (err) {
      /* nothing to do: the preference does not survive the reload */
    }
  }

  return function vaultApp() {
    return {
      ...window.vaultUnlockMixin(),
      error: '',
      vaults: [],
      busy: false,
      // Which sidebar view is on. The toolbar deliberately carries no filter
      // of its own: two places to narrow the same listing is two places to
      // disagree about what it shows.
      filter: 'all',
      search: '',
      sortField: 'default',
      sortDir: 'asc',
      viewMode: 'list',
      prefs: Object.assign({}, DEFAULT_PREFS),
      // The vault being created, or null. It carries everything the form
      // offers, because all of it is inside the signed payload.
      newVault: null,
      // Minted on the first attempt and kept until one succeeds: it is the
      // server's idempotency key, so every retry of the same vault has to
      // reuse it.
      pendingVaultUuid: null,
      // What the server says may be done with each vault, keyed by uuid. It
      // is never computed here: a rule copied into the client is a rule that
      // drifts from the endpoint enforcing it.
      vaultActions: {},
      // { vault, mode, name, icon, color } while a rename or an appearance
      // change is open, null otherwise.
      vaultDialog: null,
      // Read by ui/partials/icon_picker.html, which renders against whatever
      // component it is included in.
      icons: [],
      colors: [],
      selectedIcon: 'lock',
      selectedColor: 'text-primary',
      // Optional context of ui/partials/icon_picker.html: it renders a saving
      // indicator when a component offers one. Both vault dialogs save behind
      // their own button, so the indicator stays off - but the names have to
      // exist, or every render of the picker logs against them.
      saving: false,
      saved: false,
      // Two listings can be in flight at once - a refresh landing on a slow
      // one - and the slower answer must not describe rows that left the
      // screen. Only the newest generation is allowed to write.
      actionsGeneration: 0,
      openMenuFor: null,

      init: function () {
        this.icons = window.ICON_PICKER_ICONS || [];
        this.colors = window.VAULT_COLOR_SWATCHES || [];
        const viewMode = readPreference(VIEW_MODE_KEY);
        if (viewMode) this.viewMode = viewMode;
        this.loadPrefs();
        this.initUnlock();
      },

      // ---- the sidebar -----------------------------------------------------

      isMember: function (vault) {
        return Boolean(
          vault && vault.owner_account_uuid !== window.vaultSession.accountUuid()
        );
      },

      // ---- preferences -----------------------------------------------------

      loadPrefs: function () {
        const stored = readJson('vault-prefs') || {};
        this.prefs = {
          lockAfterMinutes: Number(stored.lock_after_minutes) || DEFAULT_PREFS.lockAfterMinutes,
          defaultSort: stored.default_sort || DEFAULT_PREFS.defaultSort,
        };
        this.sortField = this.prefs.defaultSort;
        window.vaultSession.setIdleTimeout(this.prefs.lockAfterMinutes);
      },

      // Overridable so a test can answer without a network. The endpoint is
      // the application's own settings API, which owns the cache the value
      // would otherwise go stale in.
      putSetting: function (key, value) {
        return fetch('/api/v1/settings/vault/' + key, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          body: JSON.stringify({ value: value }),
        }).then(function (response) {
          if (!response.ok) throw new Error('the setting was refused');
        });
      },

      // Applied before it is stored, and put back if the store refuses: a
      // preference that changes nothing until the next reload is not one.
      updatePref: async function (key, value) {
        const previous = Object.assign({}, this.prefs);
        if (key === 'default_sort') {
          this.prefs.defaultSort = value;
          this.sortField = value;
        }
        if (key === 'lock_after_minutes') {
          this.prefs.lockAfterMinutes = value;
          window.vaultSession.setIdleTimeout(value);
        }
        try {
          await this.putSetting(key, value);
        } catch (err) {
          this.prefs = previous;
          this.sortField = previous.defaultSort;
          window.vaultSession.setIdleTimeout(previous.lockAfterMinutes);
          this.error = 'That preference could not be saved. Try again.';
        }
      },

      // ---- what the listing shows -----------------------------------------

      // A vault whose signature failed, or that this device holds no key for,
      // never joins the listing: it has no name to sort or search on, and
      // showing it among the others would dress it up as an ordinary vault.
      unavailableVaults: function () {
        return this.vaults.filter(function (vault) {
          return vault.tampered || vault.unopenable || vault.unreadable;
        });
      },

      openableVaults: function () {
        return this.vaults.filter(function (vault) {
          return !(vault.tampered || vault.unopenable || vault.unreadable);
        });
      },

      visibleVaults: function () {
        const needle = this.search.trim().toLowerCase();
        let rows = this.openableVaults();
        if (this.filter === 'favorites') {
          rows = rows.filter(function (vault) { return vault.is_favorite; });
        }
        if (needle) {
          rows = rows.filter(function (vault) {
            return (
              String(vault.name || '').toLowerCase().includes(needle) ||
              String(vault.description || '').toLowerCase().includes(needle)
            );
          });
        }
        if (this.sortField === 'default') return rows;
        const direction = this.sortDir === 'asc' ? 1 : -1;
        const compare = {
          name: (a, b) => a.name.localeCompare(b.name),
          favorite: (a, b) => Number(b.is_favorite) - Number(a.is_favorite),
          created: (a, b) => String(a.created_at).localeCompare(String(b.created_at)),
        }[this.sortField];
        if (!compare) return rows;
        // A copy: sorting the array the caller handed us would reorder the
        // component's own data as a side effect of asking what to display.
        return [...rows].sort((a, b) => direction * compare(a, b));
      },

      statusLine: function () {
        const shown = this.visibleVaults().length;
        const favourites = this.visibleVaults().filter(function (vault) {
          return vault.is_favorite;
        }).length;
        const unavailable = this.unavailableVaults().length;
        const parts = [shown + (shown === 1 ? ' vault' : ' vaults')];
        if (favourites) {
          parts.push(favourites + ' favourite' + (favourites === 1 ? '' : 's'));
        }
        if (unavailable) parts.push(unavailable + ' unavailable');
        return parts.join(' \u00b7 ');
      },

      setViewMode: function (mode) {
        this.viewMode = mode;
        writePreference(VIEW_MODE_KEY, mode);
      },

      clearFilter: function () {
        this.search = '';
        this.filter = 'all';
      },

      toggleSortDir: function () {
        this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
      },

      // The only way back out of "remember my recovery key on this device".
      // Until now the key was dropped solely when it failed to decode, which
      // left anyone who ticked the box with no way to untick it.
      forgetDevice: async function () {
        const confirmed = await this.confirm(
          'Forget the recovery key stored in this browser? You will need your '
            + 'emergency kit the next time you unlock here.',
          { title: 'Forget the key on this device', okLabel: 'Forget it', okClass: 'btn-error' }
        );
        if (!confirmed) return;
        window.vaultSession.forgetDevice();
        this.secretRequired = true;
        this.secretRemembered = false;
        this.secretText = '';
      },

      onLocked: function () {
        this.vaults = [];
        this.vaultActions = {};
        this.openMenuFor = null;
        this.vaultDialog = null;
        // The dialog lives inside the unlocked subtree, so a lock hides it
        // without closing it, and the next unlock would reopen it on its own.
        this.closeCreateDialog();
      },

      afterUnlock: async function () {
        try {
          await this.loadVaults();
        } catch (err) {
          // The session opened; only the listing failed. Reporting it in
          // place is what keeps the countdown and the Lock now button
          // reachable, which a fall back to the password form would not.
          this.error = 'The vault list could not be loaded. Try again.';
        }
      },

      loadVaults: async function () {
        const rows = await window.vaultApi.listVaults();
        let decrypted;
        try {
          decrypted = await Promise.all(
            rows.map((row) => window.vaultReader.readVault(window.vaultSession, row))
          );
        } catch (err) {
          // A lock caught the rows mid-flight. There is nothing to report:
          // the user is looking at the password form, and the caller's
          // message would blame the listing for an idle timeout.
          if (err && err.reason === 'locked') return;
          throw err;
        }
        // Neither await is atomic with the lock: an idle timeout or a hidden
        // tab can fire in between, and by then the onLock callback has already
        // emptied the list. Assigning anyway would put decrypted names back
        // into a locked component - reachable state a lock exists to clear,
        // even though nothing renders it.
        if (!window.vaultSession.isUnlocked()) return;
        this.vaults = decrypted;
        await this.loadVaultActions();
      },

      loadVaultActions: async function () {
        this.actionsGeneration += 1;
        const generation = this.actionsGeneration;
        const uuids = this.vaults.map(function (vault) { return vault.uuid; });
        if (!uuids.length) {
          this.vaultActions = {};
          return;
        }
        let answer;
        try {
          answer = await window.vaultApi.fetchVaultActions(uuids);
        } catch (err) {
          // The names are open and the list is usable. Blanking a working
          // page over a lost menu would cost the user more than the menu.
          if (generation === this.actionsGeneration) this.vaultActions = {};
          return;
        }
        if (generation !== this.actionsGeneration) return;
        if (!window.vaultSession.isUnlocked()) return;
        this.vaultActions = answer;
      },

      // Both favourite verbs come back: the registry answers what the caller
      // may do, not what the row is. Choosing between two exclusives from a
      // flag the client already holds is not a rule copied from the server.
      actionsFor: function (vault) {
        const actions = (vault && this.vaultActions[vault.uuid]) || [];
        const favorite = vault && vault.is_favorite;
        return actions.filter(function (action) {
          if (action.id === 'favorite') return !favorite;
          if (action.id === 'unfavorite') return !!favorite;
          return true;
        });
      },

      toggleMenu: function (uuid) {
        this.openMenuFor = this.openMenuFor === uuid ? null : uuid;
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

      runVaultAction: async function (action, vault) {
        this.openMenuFor = null;
        // The menu was built from the endpoint, but it may have been built a
        // while ago: asking again here costs nothing and stops a stale menu
        // producing a request the server is about to refuse.
        const offered = this.actionsFor(vault).some(function (candidate) {
          return candidate.id === action.id;
        });
        if (!offered) return;

        if (action.id === 'delete') {
          const confirmed = await this.confirm(
            'Delete this vault and everything in it?',
            {
              title: 'This cannot be undone',
              okLabel: 'Delete the vault',
              okClass: 'btn-error',
            }
          );
          if (!confirmed) return;
          try {
            await window.vaultApi.deleteVault(vault.uuid);
            await this.loadVaults();
          } catch (err) {
            if (err && err.reason === 'locked') return;
            this.error = 'The vault could not be deleted. Try again.';
          }
          return;
        }

        if (action.id === 'rename' || action.id === 'set_appearance') {
          this.openVaultDialog(action.id, vault);
          return;
        }

        const changes = {
          favorite: { is_favorite: true },
          unfavorite: { is_favorite: false },
        }[action.id];
        if (!changes) return;

        try {
          const body = await window.buildVaultUpdateRequest(
            window.vaultSession, vault, changes
          );
          await window.vaultApi.updateVault(vault.uuid, body);
          await this.loadVaults();
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = 'That change could not be saved. Try again.';
        }
      },

      openCreateDialog: function () {
        this.newVault = { name: '', description: '', favorite: false };
        // The picker is shared with the appearance dialog and owns the
        // selection; the draft reads it back at submit. It works in CSS
        // classes, the signed metadata in bare roles - hence the two
        // conversions, at the edges.
        this.selectedIcon = 'lock';
        this.selectedColor = 'text-primary';
      },

      createVault: async function () {
        if (!window.vaultSession.isUnlocked() || !this.newVault) return;
        const draft = this.newVault;
        const name = (draft.name || '').trim();
        if (!name) return;
        this.busy = true;
        this.error = '';
        if (!this.pendingVaultUuid) {
          this.pendingVaultUuid = window.vaultCrypto.uuidV7();
        }
        try {
          const body = await window.buildVaultCreateRequest(
            window.vaultSession,
            Object.assign({}, draft, {
              name: name,
              icon: this.selectedIcon,
              color: String(this.selectedColor).replace(/^text-/, ''),
            }),
            this.pendingVaultUuid
          );
          const row = await window.vaultApi.createVault(body);
          const created = await window.vaultReader.readVault(window.vaultSession, row);
          if (draft.favorite) await this.favouriteAfterCreate(created);
          // Same race loadVaults guards against: three awaits sit between the
          // check at the top of this function and here, so a lock can have
          // emptied the list already. pendingVaultUuid survives on purpose -
          // the vault was written, and a retry after re-unlocking must reuse
          // it.
          if (!window.vaultSession.isUnlocked()) return;
          this.vaults.push(created);
          this.closeCreateDialog();
        } catch (err) {
          // The vault is written; only the local half was cut short. Saying
          // it could not be created would be false, and the retry after the
          // next unlock reuses pendingVaultUuid to find it.
          if (err && err.reason === 'locked') return;
          if (err.status === 409) {
            // The 409 says the UUID is taken, not that it is taken by us: it
            // comes from a globally unique primary key, so a row on another
            // account answers the same. Reading the reload back is what turns
            // the assumption that this is the vault a lost answer already
            // wrote into something checked.
            try {
              await this.loadVaults();
              if (!window.vaultSession.isUnlocked()) return;
              const pending = this.pendingVaultUuid;
              if (!this.vaults.some(function (v) { return v.uuid === pending; })) {
                this.error = 'The vault could not be created. Try again.';
                return;
              }
              this.closeCreateDialog();
            } catch (reloadErr) {
              this.error = 'Your vault was created, but the list could not be reloaded.';
            }
          } else {
            this.error = 'The vault could not be created. Try again.';
          }
        } finally {
          this.busy = false;
        }
      },

      openVaultDialog: function (mode, vault) {
        this.vaultDialog = { vault: vault, mode: mode, name: vault.name };
        // The picker's markup works in CSS classes; the signed metadata holds
        // the bare role. Converting at the edges is what lets the shared
        // partial be reused without widening what the server accepts.
        this.selectedIcon = vault.icon || 'lock';
        this.selectedColor = 'text-' + (vault.color || 'primary');
      },

      closeVaultDialog: function () {
        this.vaultDialog = null;
      },

      // Named as the shared icon-picker markup expects, so ui/partials/
      // icon_picker.html renders against this component unchanged.
      selectIcon: function (icon) {
        this.selectedIcon = icon;
      },

      selectColor: function (colorClass) {
        this.selectedColor = colorClass;
      },

      saveVaultDialog: async function () {
        const dialog = this.vaultDialog;
        if (!dialog) return;
        const name = (dialog.name || '').trim();
        // A vault with no name is one the user cannot tell from another.
        if (!name) return;
        this.busy = true;
        try {
          const body = await window.buildVaultUpdateRequest(
            window.vaultSession,
            dialog.vault,
            {
              name: name,
              icon: this.selectedIcon,
              color: String(this.selectedColor).replace(/^text-/, ''),
            }
          );
          await window.vaultApi.updateVault(dialog.vault.uuid, body);
          this.closeVaultDialog();
          await this.loadVaults();
        } catch (err) {
          if (err && err.reason === 'locked') return;
          this.error = 'That change could not be saved. Try again.';
        } finally {
          this.busy = false;
        }
      },

      // The create endpoint sets is_favorite itself and refuses a signature
      // over anything else, so the checkbox is honoured by a second write.
      // Its failure is not the creation's: the vault exists, and saying it
      // could not be created would send the user to make another one.
      favouriteAfterCreate: async function (vault) {
        try {
          const body = await window.buildVaultUpdateRequest(
            window.vaultSession, vault, { is_favorite: true }
          );
          await window.vaultApi.updateVault(vault.uuid, body);
          vault.is_favorite = true;
        } catch (err) {
          /* the vault stands; only the flag did not land */
        }
      },

      closeCreateDialog: function () {
        this.newVault = null;
        this.pendingVaultUuid = null;
      },

    };
  };
})();
