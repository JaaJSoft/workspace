// Managing a vault from the sidebar, which is the only place it happens.
//
// A mixin rather than its own component: the switcher lives in a sidebar whose
// scope is the page's component, and choosing a vault reloads that page's
// contents, so the state has to be on that component.
//
// Nothing here decides what a vault may do. `POST /api/v1/vault/actions` with
// `target: "vault"` answers that, and a menu built from anything else is a
// menu offering a request the server is about to refuse.
//
// Methods, never getters: object spread copies values, so a `get` here would
// be evaluated once at composition and frozen.
window.vaultSwitcherMixin = function vaultSwitcherMixin() {
  // Past a handful, a list stops being something you read and becomes
  // something you search. The threshold is a judgement rather than a
  // measurement - worth revisiting when sharing lands and an account can be
  // handed vaults it never created.
  const SEARCH_THRESHOLD = 6;

  return {
    switcherOpen: false,
    switcherSearch: '',
    vaultActions: {},
    vaultActionsGeneration: 0,
    vaultMenu: { open: false, vault: null, x: 0, y: 0 },
    vaultDialog: null,
    newVault: null,
    pendingVaultUuid: null,
    icons: [],
    colors: [],
    selectedIcon: 'lock',
    selectedColor: 'text-primary',

    // ---- the popover ------------------------------------------------------

    toggleSwitcher: function () {
      this.switcherOpen = !this.switcherOpen;
      if (!this.switcherOpen) this.switcherSearch = '';
    },

    closeSwitcher: function () {
      this.switcherOpen = false;
      this.switcherSearch = '';
    },

    switcherNeedsSearch: function () {
      return this.vaults.length > SEARCH_THRESHOLD;
    },

    switcherVaults: function () {
      const needle = this.switcherSearch.trim().toLowerCase();
      if (!needle) return this.vaults;
      return this.vaults.filter(function (vault) {
        return (vault.name || '').toLowerCase().includes(needle);
      });
    },

    vaultIsDegraded: function (vault) {
      return !!(vault && (vault.tampered || vault.unopenable || vault.unreadable));
    },

    switchVault: async function (vault) {
      if (!vault || String(vault.uuid) === String(this.vaultUuid)) return;
      // No name to show and no contents to read: opening one would swap a
      // working screen for a banner.
      if (this.vaultIsDegraded(vault)) return;
      this.closeSwitcher();
      this.rememberVault(vault.uuid);
      await this.load();
    },

    // ---- what each vault may do -------------------------------------------

    loadVaultActions: async function () {
      this.vaultActionsGeneration += 1;
      const generation = this.vaultActionsGeneration;
      const uuids = this.vaults.map(function (vault) { return vault.uuid; });
      if (!uuids.length) {
        this.vaultActions = {};
        return;
      }
      let answer;
      try {
        answer = await window.vaultApi.fetchVaultActions(uuids);
      } catch (err) {
        // The names are open and the switcher still switches. Blanking a
        // working page over a lost menu would cost the user more than the menu.
        if (generation === this.vaultActionsGeneration) this.vaultActions = {};
        return;
      }
      if (generation !== this.vaultActionsGeneration) return;
      if (!window.vaultSession.isUnlocked()) return;
      this.vaultActions = answer;
    },

    // Both favourite verbs come back: the registry answers what the caller may
    // do, not what the row is. Choosing between two exclusives from a flag the
    // client already holds is not a rule copied from the server.
    vaultActionsFor: function (vault) {
      const actions = (vault && this.vaultActions[vault.uuid]) || [];
      const favorite = vault && vault.is_favorite;
      return actions.filter(function (action) {
        if (action.id === 'favorite') return !favorite;
        if (action.id === 'unfavorite') return !!favorite;
        return true;
      });
    },

    canFavoriteVault: function (vault) {
      return this.vaultActionsFor(vault).some(function (action) {
        return action.id === 'favorite' || action.id === 'unfavorite';
      });
    },

    toggleVaultFavorite: function (vault) {
      const wanted = vault.is_favorite ? 'unfavorite' : 'favorite';
      return this.runVaultAction({ id: wanted }, vault);
    },

    // ---- the per-vault menu -----------------------------------------------

    openVaultMenu: function (event, vault) {
      if (event && event.preventDefault) event.preventDefault();
      if (event && event.stopPropagation) event.stopPropagation();
      this.vaultMenu = {
        open: true,
        vault: vault,
        x: (event && event.clientX) || 0,
        y: (event && event.clientY) || 0,
      };
      window.vaultMenu.fit(this, 'vault-switcher-menu', 'vaultMenu');
    },

    closeVaultMenu: function () {
      this.vaultMenu = { open: false, vault: null, x: 0, y: 0 };
    },

    runVaultAction: async function (action, vault) {
      // The menu was built from the endpoint, but it may have been built a
      // while ago: asking again here costs nothing and stops a stale menu
      // producing a request the server is about to refuse.
      const offered = this.vaultActionsFor(vault).some(function (candidate) {
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
          // Standing in the vault that just went: clearing the pointer sends
          // the reload back through the landing resolution instead of looking
          // up a row nobody will find, which would read as "out of reach".
          if (String(vault.uuid) === String(this.vaultUuid)) this.vaultUuid = null;
          await this.load();
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
        await this.load();
      } catch (err) {
        if (err && err.reason === 'locked') return;
        this.error = 'That change could not be saved. Try again.';
      }
    },

    // ---- rename and appearance --------------------------------------------

    openVaultDialog: function (mode, vault) {
      this.closeVaultMenu();
      this.vaultDialog = { vault: vault, mode: mode, name: vault.name };
      // The picker's markup works in CSS classes; the signed metadata holds
      // the bare role. Converting at the edges is what lets the shared partial
      // be reused without widening what the server accepts.
      this.selectedIcon = vault.icon || 'lock';
      this.selectedColor = 'text-' + (vault.color || 'primary');
    },

    closeVaultDialog: function () {
      this.vaultDialog = null;
    },

    // Named as the shared icon-picker markup expects, so
    // ui/partials/icon_picker.html renders against this component unchanged.
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
        await this.load();
      } catch (err) {
        if (err && err.reason === 'locked') return;
        this.error = 'That change could not be saved. Try again.';
      } finally {
        this.busy = false;
      }
    },

    // ---- creating ----------------------------------------------------------

    openCreateDialog: function () {
      this.closeSwitcher();
      this.newVault = { name: '', description: '', favorite: false };
      // The picker is shared with the appearance dialog and owns the
      // selection; the draft reads it back at submit. It works in CSS classes,
      // the signed metadata in bare roles - hence the two conversions, at the
      // edges.
      this.selectedIcon = 'lock';
      this.selectedColor = 'text-primary';
    },

    closeCreateDialog: function () {
      this.newVault = null;
      this.pendingVaultUuid = null;
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
        // Three awaits sit between the check at the top of this function and
        // here, so a lock can have emptied the list already. pendingVaultUuid
        // survives on purpose - the vault was written, and a retry after
        // re-unlocking must reuse it.
        if (!window.vaultSession.isUnlocked()) return;
        this.closeCreateDialog();
        // Straight into it: a vault created and then not opened would leave
        // the user on the one they were already in, wondering where it went.
        this.rememberVault(created.uuid);
        await this.load();
      } catch (err) {
        // The vault is written; only the local half was cut short. Saying it
        // could not be created would be false, and the retry after the next
        // unlock reuses pendingVaultUuid to find it.
        if (err && err.reason === 'locked') return;
        if (err.status === 409) {
          // The 409 says the UUID is taken, not that it is taken by us: it
          // comes from a globally unique primary key, so a row on another
          // account answers the same. Reading the reload back is what turns
          // the assumption that this is the vault a lost answer already wrote
          // into something checked.
          try {
            await this.load();
            if (!window.vaultSession.isUnlocked()) return;
            const pending = this.pendingVaultUuid;
            if (!this.vaults.some(function (v) { return v.uuid === pending; })) {
              this.error = 'The vault could not be created. Try again.';
              return;
            }
            this.closeCreateDialog();
          } catch (reloadErr) {
            this.error = 'Your vault was created, but it could not be opened.';
          }
        } else {
          this.error = 'The vault could not be created. Try again.';
        }
      } finally {
        this.busy = false;
      }
    },

    // The create endpoint sets is_favorite itself and refuses a signature over
    // anything else, so the checkbox is honoured by a second write. Its
    // failure is not the creation's: the vault exists, and saying it could not
    // be created would send the user to make another one.
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

    // ---- the lock ----------------------------------------------------------

    onSwitcherLocked: function () {
      this.closeSwitcher();
      this.closeVaultMenu();
      this.vaultActions = {};
      this.vaultDialog = null;
      this.newVault = null;
      this.pendingVaultUuid = null;
    },
  };
};
