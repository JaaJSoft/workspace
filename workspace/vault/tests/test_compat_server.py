"""Every stored row still passes the server's own verifiers, and still serves.

Loaded through loaddata rather than rebuilt by a factory: a factory would
sign with today's code, which is the circularity this corpus exists to break.

test_compat_reference.py proves the corpus against the Python reference
implementation. This file proves it against the server's own
code - workspace.vault.services.metadata and .attestation - which shares no
line with the reference beyond both using cbor2 to encode. That shared
encoder is a known limit of this layer, closed by the browser replay
elsewhere, not by anything here.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from ..models import AccountIdentity, Vault, VaultEntry, VaultFolder, VaultTag
from ..services.attestation import verify_kex_pub_attestation
from ..services.metadata import (
    entry_metadata_payload,
    folder_metadata_payload,
    tag_metadata_payload,
    vault_metadata_payload,
    verify_record,
)
from . import compat

# The corpora this file's replays actually open, and the versions derived
# from them. test_compat_frozen checks COVERED against compat.versions() so a
# new corpus directory can never go unread - deriving the list from the loads
# themselves is what stops a version being *declared* covered by a replay that
# never reads it.
CORPORA = (compat.load("v1"),)
COVERED = [corpus.root.name for corpus in CORPORA]


class ServerReplayTests(TestCase):
    fixtures = [str(CORPORA[0].rows)]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.corpus,) = CORPORA
        cls.manifest_vaults = cls.corpus.manifest["vaults"]

    def setUp(self):
        self.identity = AccountIdentity.objects.get()
        self.user = self.identity.user

    def test_the_account_attestation_verifies(self):
        # No try/except: an AttestationError here fails the test with the
        # real traceback rather than a silently-swallowed reason.
        verify_kex_pub_attestation(
            str(self.identity.uuid),
            self.identity.kex_public,
            self.identity.sig_public,
            self.identity.sig_over_kex_pub,
        )

    def test_every_vault_record_verifies(self):
        vaults = list(Vault.objects.all())
        self.assertEqual(len(vaults), len(self.manifest_vaults))
        self.assertGreater(len(vaults), 0)

        verified = 0
        for vault in vaults:
            payload = vault_metadata_payload(
                vault_uuid=vault.uuid,
                owner_account_uuid=self.identity.uuid,
                encrypted_name=vault.encrypted_name,
                encrypted_description=vault.encrypted_description,
                icon=vault.icon,
                color=vault.color,
                key_version=vault.key_version,
                is_favorite=vault.is_favorite,
            )
            verify_record(payload, self.identity.sig_public, vault.metadata_sig)
            verified += 1
        self.assertEqual(verified, len(vaults))

    def test_every_folder_record_verifies(self):
        folders = list(VaultFolder.objects.all())
        expected = sum(len(v["folders"]) for v in self.manifest_vaults)
        self.assertEqual(len(folders), expected)
        self.assertGreater(len(folders), 0)

        verified = 0
        for folder in folders:
            payload = folder_metadata_payload(
                folder_uuid=folder.uuid,
                vault_uuid=folder.vault_id,
                signer_account_uuid=self.identity.uuid,
                parent_uuid=folder.parent_id,
                position=folder.position,
                encrypted_name=folder.encrypted_name,
            )
            verify_record(payload, self.identity.sig_public, folder.metadata_sig)
            verified += 1
        self.assertEqual(verified, len(folders))

    def test_every_tag_record_verifies(self):
        tags = list(VaultTag.objects.all())
        expected = sum(len(v["tags"]) for v in self.manifest_vaults)
        self.assertEqual(len(tags), expected)
        self.assertGreater(len(tags), 0)

        verified = 0
        for tag in tags:
            payload = tag_metadata_payload(
                tag_uuid=tag.uuid,
                vault_uuid=tag.vault_id,
                signer_account_uuid=self.identity.uuid,
                encrypted_name=tag.encrypted_name,
                color=tag.color,
            )
            verify_record(payload, self.identity.sig_public, tag.metadata_sig)
            verified += 1
        self.assertEqual(verified, len(tags))

    def test_every_entry_record_verifies(self):
        # The default manager: a trashed entry (deleted_at set) still carries
        # a metadata_sig and must still verify, same as a live one.
        entries = list(VaultEntry.objects.all())
        expected = sum(len(v["entries"]) for v in self.manifest_vaults)
        self.assertEqual(len(entries), expected)
        self.assertGreater(len(entries), 0)

        verified = 0
        for entry in entries:
            fields = {f.field_id: f.encrypted_value for f in entry.fields.all()}
            tag_uuids = list(entry.tags.values_list("uuid", flat=True))
            payload = entry_metadata_payload(
                entry_uuid=entry.uuid,
                vault_uuid=entry.vault_id,
                signer_account_uuid=self.identity.uuid,
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
            verify_record(payload, self.identity.sig_public, entry.metadata_sig)
            verified += 1
        self.assertEqual(verified, len(entries))

    def test_every_row_serves_unchanged_through_the_api(self):
        """The ciphertexts a client would read back, compared to the row's own
        stored bytes - never a count. A handler returning the right number of
        empty rows would still pass a count-only check.
        """
        client = APIClient()
        client.force_authenticate(self.user)

        stored_vaults = {str(v.uuid): v for v in Vault.objects.all()}
        self.assertEqual(len(stored_vaults), len(self.manifest_vaults))
        self.assertGreater(len(stored_vaults), 0)

        resp = client.get("/api/v1/vault/vaults")
        self.assertEqual(resp.status_code, 200)
        served_vaults = {row["uuid"]: row for row in resp.json()}
        self.assertEqual(set(served_vaults), set(stored_vaults))

        reconciled_vaults = reconciled_folders = reconciled_tags = 0
        reconciled_entries = reconciled_fields = 0

        for vault_uuid, stored_vault in stored_vaults.items():
            served_vault = served_vaults[vault_uuid]
            self.assertEqual(
                served_vault["encrypted_name"], stored_vault.encrypted_name
            )
            self.assertEqual(
                served_vault["encrypted_description"],
                stored_vault.encrypted_description,
            )
            self.assertEqual(served_vault["metadata_sig"], stored_vault.metadata_sig)
            reconciled_vaults += 1

            stored_folders = {
                str(f.uuid): f for f in VaultFolder.objects.filter(vault=stored_vault)
            }
            folder_resp = client.get("/api/v1/vault/folders", {"vault": vault_uuid})
            self.assertEqual(folder_resp.status_code, 200)
            served_folders = {row["uuid"]: row for row in folder_resp.json()}
            self.assertEqual(set(served_folders), set(stored_folders))
            for folder_uuid, stored_folder in stored_folders.items():
                served_folder = served_folders[folder_uuid]
                self.assertEqual(
                    served_folder["encrypted_name"], stored_folder.encrypted_name
                )
                self.assertEqual(
                    served_folder["metadata_sig"], stored_folder.metadata_sig
                )
                reconciled_folders += 1

            stored_tags = {
                str(t.uuid): t for t in VaultTag.objects.filter(vault=stored_vault)
            }
            tag_resp = client.get("/api/v1/vault/tags", {"vault": vault_uuid})
            self.assertEqual(tag_resp.status_code, 200)
            served_tags = {row["uuid"]: row for row in tag_resp.json()}
            self.assertEqual(set(served_tags), set(stored_tags))
            for tag_uuid, stored_tag in stored_tags.items():
                served_tag = served_tags[tag_uuid]
                self.assertEqual(
                    served_tag["encrypted_name"], stored_tag.encrypted_name
                )
                self.assertEqual(served_tag["metadata_sig"], stored_tag.metadata_sig)
                reconciled_tags += 1

            stored_entries = {
                str(e.uuid): e for e in VaultEntry.objects.filter(vault=stored_vault)
            }
            live_resp = client.get(
                "/api/v1/vault/entries", {"vault": vault_uuid, "trashed": "false"}
            )
            trashed_resp = client.get(
                "/api/v1/vault/entries", {"vault": vault_uuid, "trashed": "true"}
            )
            self.assertEqual(live_resp.status_code, 200)
            self.assertEqual(trashed_resp.status_code, 200)
            served_entries = {row["uuid"]: row for row in live_resp.json()}
            for row in trashed_resp.json():
                self.assertNotIn(row["uuid"], served_entries)
                served_entries[row["uuid"]] = row
            self.assertEqual(set(served_entries), set(stored_entries))

            for entry_uuid, stored_entry in stored_entries.items():
                served_entry = served_entries[entry_uuid]
                self.assertEqual(
                    served_entry["encrypted_name"], stored_entry.encrypted_name
                )
                self.assertEqual(
                    served_entry["encrypted_notes"], stored_entry.encrypted_notes
                )
                self.assertEqual(
                    served_entry["metadata_sig"], stored_entry.metadata_sig
                )
                reconciled_entries += 1

                stored_fields = {
                    f.field_id: f.encrypted_value for f in stored_entry.fields.all()
                }
                served_fields = {
                    row["field_id"]: row["encrypted_value"]
                    for row in served_entry["entry_fields"]
                }
                self.assertEqual(served_fields, stored_fields)
                reconciled_fields += len(stored_fields)

        self.assertEqual(reconciled_vaults, len(stored_vaults))
        self.assertEqual(
            reconciled_folders,
            sum(len(v["folders"]) for v in self.manifest_vaults),
        )
        self.assertEqual(
            reconciled_tags, sum(len(v["tags"]) for v in self.manifest_vaults)
        )
        self.assertEqual(
            reconciled_entries,
            sum(len(v["entries"]) for v in self.manifest_vaults),
        )
        self.assertEqual(
            reconciled_fields,
            sum(len(e["fields"]) for v in self.manifest_vaults for e in v["entries"]),
        )
