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
  // The metadata key is opened fresh per vault and is non-extractable: no
  // copy of its bytes exists on this side to be zeroed.
  async function decryptVault(row) {
    const V = window.vaultCrypto;
    const payload = V.vaultMetadataPayload(
      Object.assign({}, row, { vault_uuid: row.uuid })
    );
    try {
      await window.vaultSession.verifyVaultMetadata(payload, row.metadata_sig);
    } catch (err) {
      // A lock landing mid-listing fails this the same way a forged signature
      // does, and the tamper alert is the one message the user is told to act
      // on rather than retry - so it must never stand in for an idle timeout.
      if (err && err.reason === 'locked') throw err;
      // Signed by nobody the account trusts: never shown with a name that
      // came along for the ride.
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
      // Localised the same way a bad signature is: one row loses its name,
      // the rest of the list - and the Promise.all it resolves inside -
      // keeps going.
      return Object.assign({}, row, { unreadable: true, name: '' });
    }
  }

  return function vaultApp() {
    return {
      ...window.vaultUnlockMixin(),
      error: '',
      vaults: [],
      busy: false,
      showCreate: false,
      newVaultName: '',
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
      // Two listings can be in flight at once - a refresh landing on a slow
      // one - and the slower answer must not describe rows that left the
      // screen. Only the newest generation is allowed to write.
      actionsGeneration: 0,
      openMenuFor: null,

      init: function () {
        this.icons = window.ICON_PICKER_ICONS || [];
        this.colors = window.VAULT_COLOR_SWATCHES || [];
        this.initUnlock();
      },

      onLocked: function () {
        this.vaults = [];
        this.vaultActions = {};
        this.openMenuFor = null;
        this.vaultDialog = null;
        // showCreate has to go with the name: the dialog lives inside the
        // unlocked subtree, so a lock hides it without closing it, and the
        // next unlock reopens it on its own.
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
          decrypted = await Promise.all(rows.map(decryptVault));
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

      createVault: async function () {
        if (!window.vaultSession.isUnlocked()) return;
        const name = this.newVaultName.trim();
        if (!name) return;
        this.busy = true;
        this.error = '';
        if (!this.pendingVaultUuid) {
          this.pendingVaultUuid = window.vaultCrypto.uuidV7();
        }
        try {
          const body = await window.buildVaultCreateRequest(
            window.vaultSession, name, this.pendingVaultUuid
          );
          const row = await window.vaultApi.createVault(body);
          const created = await decryptVault(row);
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

      closeCreateDialog: function () {
        this.showCreate = false;
        this.newVaultName = '';
        this.pendingVaultUuid = null;
      },

    };
  };
})();
