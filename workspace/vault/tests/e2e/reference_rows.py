"""The account's rows as the reference implementation sees them.

Opens the account's keys from the password and the recovery key onboarding
displayed, and writes or patches rows the way another build of the app would,
straight into the database.
"""

import os
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from workspace.vault.models import AccountIdentity, Vault, VaultEntry, VaultKeyWrap
from workspace.vault.services.suites import hpke_format
from workspace.vault.tests.reference import ad, metadata, primitives, suites, wire
from workspace.vault.tests.reference.encoding import from_base64url, to_base64url

from .test_browser import GOOD_PASSWORD


class ReferenceRowsMixin:
    # ---- the account, as the reference sees it ----------------------------

    def _account_keys(self):
        """The account's private keys, opened from the password and the
        recovery key onboarding displayed, under the parameters it stored."""
        identity = AccountIdentity.objects.get(user=self.user)
        account_uuid = str(identity.uuid)
        amk = primitives.derive_amk(
            GOOD_PASSWORD,
            primitives.crockford_decode(self.secret),
            from_base64url(identity.kdf_salt),
            identity.kdf_params,
        )
        unwrap = primitives.hkdf(amk, ad.unwrap_info())
        kex_priv = primitives.aead_open(
            unwrap,
            from_base64url(identity.wrapped_kex_priv),
            ad.kex_priv_ad(account_uuid),
        )
        sig_priv = primitives.aead_open(
            unwrap,
            from_base64url(identity.wrapped_sig_priv),
            ad.sig_priv_ad(account_uuid),
        )
        return (
            account_uuid,
            X25519PrivateKey.from_private_bytes(kex_priv),
            Ed25519PrivateKey.from_private_bytes(sig_priv),
        )

    def _vault_key(self, vault, account_uuid, kex_priv):
        wrap = VaultKeyWrap.objects.get(vault=vault, recipient=self.user)
        return primitives.hpke_open(
            kex_priv,
            ad.vault_key_info(str(vault.uuid), account_uuid, wrap.hpke_suite),
            from_base64url(wrap.wrapped_key),
            hpke_suite=wrap.hpke_suite,
        )

    def _signature(self, entry, account_uuid, sig_priv, *, tag_uuids, fields):
        payload = metadata.entry_metadata_payload(
            entry_uuid=str(entry.uuid),
            vault_uuid=str(entry.vault_id),
            signer_account_uuid=account_uuid,
            entry_type=entry.type,
            folder_uuid=entry.folder_id,
            encrypted_name=entry.encrypted_name,
            encrypted_notes=entry.encrypted_notes,
            key_version=entry.key_version,
            entry_version=entry.entry_version,
            is_favorite=entry.is_favorite,
            tag_uuids=tag_uuids,
            fields=fields,
        )
        return to_base64url(primitives.sign(sig_priv, payload))

    def _write_entry_as_reference(self, name, *, format_version=None, aead_id=None):
        """An entry whose name the reference sealed, signed by the account.

        Sealed under the parameters given rather than under the current suite:
        this is how a row written by another build - older or newer - lands in
        the table.
        """
        self._write_entries_as_reference(
            [name], format_version=format_version, aead_id=aead_id
        )

    def _write_entries_as_reference(self, names, *, format_version=None, aead_id=None):
        """One entry per name, as _write_entry_as_reference writes it.

        The account keys are opened once for the lot: each opening runs the
        password KDF, which is what a few hundred rows would otherwise cost.
        """
        account_uuid, kex_priv, sig_priv = self._account_keys()
        vault = Vault.objects.get(uuid=self.vault_uuid)
        vault_key = self._vault_key(vault, account_uuid, kex_priv)
        aead = suites.CURRENT_SUITE["aead_id"] if aead_id is None else aead_id
        for name in names:
            entry = VaultEntry(vault=vault, key_version=vault.key_version)
            entry_key = primitives.hkdf(vault_key, ad.entry_key_info(str(entry.uuid)))
            entry.encrypted_name = to_base64url(
                primitives.aead_seal(
                    entry_key,
                    name.encode("utf-8"),
                    ad.entry_field_ad(str(entry.uuid), "name"),
                    iv=os.urandom(suites.entry("aead", aead)["iv_length"]),
                    key_version=vault.key_version,
                    kdf_id=wire.KDF_HKDF_SHA256,
                    format_version=format_version,
                    aead_id=aead,
                )
            )
            # An unsaved row has no tags or fields to ask for, and this one has
            # neither.
            entry.metadata_sig = self._signature(
                entry, account_uuid, sig_priv, tag_uuids=[], fields={}
            )
            entry.save()

    def _rewrite_header_byte(self, entry, field, index, value):
        """Patch one header byte of a stored ciphertext, then sign the row
        again with the account's own key.

        The page verifies a row before it opens anything in it, so an edit
        left unsigned would read as tampering whatever the byte said. Signed
        again, the byte is the only thing wrong with the row.
        """
        raw = bytearray(from_base64url(getattr(entry, field)))
        raw[index] = value
        setattr(entry, field, to_base64url(bytes(raw)))
        account_uuid, _, sig_priv = self._account_keys()
        entry.metadata_sig = self._signature(
            entry,
            account_uuid,
            sig_priv,
            tag_uuids=entry.tags.values_list("uuid", flat=True),
            fields=dict(entry.fields.values_list("field_id", "encrypted_value")),
        )
        VaultEntry.objects.filter(uuid=entry.uuid).update(
            **{field: getattr(entry, field), "metadata_sig": entry.metadata_sig}
        )

    # ---- what is stored -----------------------------------------------------

    def _formats(self, vault):
        """Every format the vault's rows are stored under: its entries' names,
        its own name and this account's key wrap."""
        values = list(
            VaultEntry.objects.filter(vault=vault).values_list(
                "encrypted_name", flat=True
            )
        )
        values.append(Vault.objects.get(pk=vault.pk).encrypted_name)
        formats = {from_base64url(value)[0] for value in values if value}
        wrap = VaultKeyWrap.objects.get(vault=vault, recipient=self.user)
        formats.add(hpke_format(wrap.hpke_suite))
        return formats

    def _wait_for_no_migration(self, timeout_ms=30000):
        """Wait until the server lists nothing left to migrate for the
        account the page is signed in as.

        The migration POSTs a few seconds after the unlock resolves, so the
        database is only worth reading once the listing has emptied. Polled
        from Python through evaluate, which awaits the fetch: wait_for_function
        takes the pending promise itself for a truthy answer.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            remaining = self.page.evaluate(
                """async () => {
                    const response = await fetch('/api/v1/vault/migration', {
                        headers: { Accept: 'application/json' },
                    });
                    return response.ok ? (await response.json()).vaults.length : -1;
                }"""
            )
            if remaining == 0:
                return
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"the migration listing still names {remaining} vault(s)"
                )
            self.page.wait_for_timeout(500)
