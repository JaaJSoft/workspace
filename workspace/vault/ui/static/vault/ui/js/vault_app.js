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
  return function vaultApp() {
    return {
      ...window.vaultUnlockMixin(),
      ...window.vaultPrefsMixin(),
      ...window.vaultViewPrefsMixin('vault.list'),
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
      tileSize: 3,
      selected: [],
      // Which vault the context menu belongs to, and where it was raised.
      menu: { open: false, vault: null, x: 0, y: 0 },
      // The menu of the listing itself - what can be done here rather than to
      // a row. Creating a vault is not an action on an existing one, so it
      // cannot come from the action endpoint.
      backgroundMenu: { open: false, x: 0, y: 0 },
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

      init: function () {
        this.icons = window.ICON_PICKER_ICONS || [];
        this.colors = window.VAULT_COLOR_SWATCHES || [];
        this.restoreViewPrefs();
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

      // Overridable so a test can answer without a network. The endpoint is
      // the application's own settings API, which owns the cache the value
      // would otherwise go stale in.

      // Applied before it is stored, and put back if the store refuses: a
      // preference that changes nothing until the next reload is not one.

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
          entries: (a, b) => (a.entry_count || 0) - (b.entry_count || 0),
          created: (a, b) => String(a.created_at).localeCompare(String(b.created_at)),
          modified: (a, b) => String(a.updated_at).localeCompare(String(b.updated_at)),
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

      shortDate: function (value) {
        return window.vaultFormat.shortDate(value);
      },

      heading: function () {
        return this.filter === 'favorites' ? 'Favorites' : 'My vaults';
      },

      // The row is the link. A vault that will not open is not one: it has no
      // contents to show and no key to show them with, so following it would
      // land on a screen that can only report the same failure.
      openVault: function (vault) {
        if (!vault || vault.tampered || vault.unopenable || vault.unreadable) return;
        window.location.assign('/vault/' + vault.uuid);
      },

      // ---- the favourite star ----------------------------------------------

      // Whether the registry offers either verb for this vault. Asking it
      // rather than assuming keeps the star from being a button the server
      // will refuse.
      canFavorite: function (vault) {
        const actions = (vault && this.vaultActions[vault.uuid]) || [];
        return actions.some(function (action) {
          return action.id === 'favorite' || action.id === 'unfavorite';
        });
      },

      toggleFavorite: function (vault) {
        const wanted = vault.is_favorite ? 'unfavorite' : 'favorite';
        return this.runVaultAction({ id: wanted }, vault);
      },

      // ---- selection -------------------------------------------------------

      isSelected: function (uuid) {
        return this.selected.includes(uuid);
      },

      toggleSelection: function (uuid) {
        this.selected = this.isSelected(uuid)
          ? this.selected.filter(function (value) { return value !== uuid; })
          : [...this.selected, uuid];
      },

      clearSelection: function () {
        this.selected = [];
      },

      // Over what is on screen, never over everything the account holds: a
      // filter that narrows the listing has to narrow what "all" means.
      selectAll: function () {
        this.selected = this.visibleVaults().map(function (vault) { return vault.uuid; });
      },

      selectAllState: function () {
        const rows = this.visibleVaults();
        if (!rows.length) return 'none';
        const self = this;
        const picked = rows.filter(function (vault) { return self.isSelected(vault.uuid); });
        if (!picked.length) return 'none';
        return picked.length === rows.length ? 'all' : 'partial';
      },

      toggleSelectAll: function () {
        if (this.selectAllState() === 'all') return this.clearSelection();
        return this.selectAll();
      },

      // Only what is both selected and shown: a row the filter hides is a row
      // the bar must not count, and must not act on.
      selectedVaults: function () {
        const self = this;
        return this.visibleVaults().filter(function (vault) {
          return self.isSelected(vault.uuid);
        });
      },

      // The actions every selected vault was offered, kept to the ones the
      // registry marks as working on a batch. The favourite verbs are decided
      // over the selection as a whole, for the reason the entry bar does it:
      // intersecting the filtered lists would offer neither on a mixed one.
      bulkActions: function () {
        const rows = this.selectedVaults();
        if (!rows.length) return [];
        const self = this;
        const lists = rows.map(function (vault) {
          return self.vaultActions[vault.uuid] || [];
        });
        const shared = lists[0].filter(function (action) {
          return (
            action.bulk &&
            lists.every(function (list) {
              return list.some(function (other) { return other.id === action.id; });
            })
          );
        });
        const allFavorite = rows.every(function (vault) { return vault.is_favorite; });
        const noneFavorite = rows.every(function (vault) { return !vault.is_favorite; });
        return shared.filter(function (action) {
          if (action.id === 'favorite') return !allFavorite;
          if (action.id === 'unfavorite') return !noneFavorite;
          return true;
        });
      },

      // The bar addresses several vaults at once, and asks once for the
      // batch: one confirmation per row is what trains people to click
      // through the question that matters.
      runBulkVaultAction: async function (action) {
        const rows = this.selectedVaults();
        if (!rows.length) return;
        const offered = this.bulkActions().some(function (candidate) {
          return candidate.id === action.id;
        });
        if (!offered) return;

        if (action.id === 'delete') {
          const confirmed = await this.confirm(
            rows.length === 1
              ? 'Delete this vault and everything in it?'
              : 'Delete these ' + rows.length + ' vaults and everything in them?',
            {
              title: 'This cannot be undone',
              okLabel: 'Delete',
              okClass: 'btn-error',
            }
          );
          if (!confirmed) return;
        }

        this.busy = true;
        let failed = false;
        try {
          for (const vault of rows) {
            if (action.id === 'delete') {
              await window.vaultApi.deleteVault(vault.uuid);
            } else {
              const body = await window.buildVaultUpdateRequest(
                window.vaultSession, vault, { is_favorite: action.id === 'favorite' }
              );
              await window.vaultApi.updateVault(vault.uuid, body);
            }
          }
        } catch (err) {
          if (err && err.reason === 'locked') return;
          failed = true;
        } finally {
          this.busy = false;
        }
        // Reloaded before the message: the listing is what says where the
        // batch got to, and it clears the error line on its way in.
        await this.loadVaults();
        if (failed) {
          this.error = 'That change could not be applied to every vault. The listing is current.';
        }
      },

      // ---- menus -----------------------------------------------------------

      openVaultMenu: function (event, vault) {
        if (event && event.preventDefault) event.preventDefault();
        this.backgroundMenu = { open: false, x: 0, y: 0 };
        this.menu = {
          open: true,
          vault: vault,
          x: (event && event.clientX) || 0,
          y: (event && event.clientY) || 0,
        };
        window.vaultMenu.fit(this, 'vault-context-menu', 'menu');
      },

      closeVaultMenu: function () {
        this.menu = { open: false, vault: null, x: 0, y: 0 };
      },

      // Raised on the listing rather than on a row. The row handlers stop the
      // event, so reaching here means the click landed on empty space.
      openBackgroundMenu: function (event) {
        if (event && event.preventDefault) event.preventDefault();
        this.closeVaultMenu();
        this.backgroundMenu = {
          open: true,
          x: (event && event.clientX) || 0,
          y: (event && event.clientY) || 0,
        };
        window.vaultMenu.fit(this, 'vault-listing-menu', 'backgroundMenu');
      },

      closeBackgroundMenu: function () {
        this.backgroundMenu = { open: false, x: 0, y: 0 };
      },

      closeMenus: function () {
        this.closeVaultMenu();
        this.closeBackgroundMenu();
      },

      // Back to the listing this account would get on a fresh load: the
      // saved default sort, not "no sort", or the button would undo a
      // preference the user set on purpose.
      resetAll: function () {
        this.search = '';
        this.filter = 'all';
        this.sortField = this.prefs.defaultSort;
        this.sortDir = 'asc';
      },

      toggleSortDir: function () {
        this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
      },

      // The only way back out of "remember my recovery key on this device".
      // Until now the key was dropped solely when it failed to decode, which
      // left anyone who ticked the box with no way to untick it.

      onLocked: function () {
        this.vaults = [];
        this.vaultActions = {};
        this.selected = [];
        this.closeMenus();
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
        // The rows the selection named have just been replaced.
        this.selected = [];
        this.closeMenus();
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
          // The actions map is keyed by uuid and was answered before this
          // vault existed, so without this the new row offers nothing.
          await this.loadVaultActions();
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
