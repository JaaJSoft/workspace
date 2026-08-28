"""Deleting a folder, which is never just a DELETE.

``VaultEntry.folder`` is RESTRICT, so the entries have to move first - and they
cannot move without being re-signed, because folder_uuid is inside the entry
signature. Both halves therefore travel in one request and commit or roll back
together.
"""

from unittest import mock

from django.db.models.deletion import RestrictedError
from django.test import TestCase
from django.utils import timezone

from workspace.vault.models import EntryField, EntryType, VaultEntry, VaultFolder
from workspace.vault.services.entries import entry_signature_payload
from workspace.vault.services.metadata import verify_record
from workspace.vault.tests.factories import make_account, make_vault, sign


class FolderDeleteTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)
        self.folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQID", metadata_sig="AQ"
        )
        self.empty_folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQIE", metadata_sig="AQ"
        )
        self.entries = [self._entry(self.folder, index) for index in range(3)]
        self.entry_elsewhere = self._entry(self.empty_folder, 9)
        self.entry_elsewhere.folder = None
        self.entry_elsewhere.save(update_fields=["folder"])

        self.other_user, self.other_signer, _ = make_account("stranger")
        self.other_vault = make_vault(self.other_user)
        self.other_folder = VaultFolder.objects.create(
            vault=self.other_vault, encrypted_name="AQID", metadata_sig="AQ"
        )
        # A well-formed signature made by the wrong key: refused for the right
        # reason, unlike a string of garbage the serializer would reject first.
        self.some_other_signature = sign(self.other_signer, {"v": 1, "type": "x"})

    def _entry(self, folder, index):
        entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            folder=folder,
            encrypted_name=f"AQID{index}",
            metadata_sig="AQ",
        )
        EntryField.objects.create(
            entry=entry, field_id="password", encrypted_value="Ag"
        )
        return entry

    def url(self, folder):
        return f"/api/v1/vault/folders/{folder.uuid}/delete"

    def resigned(self, entry):
        """The entry as it will be stored: same everything, no folder."""
        moved = VaultEntry.objects.get(uuid=entry.uuid)
        moved.folder = None
        payload = entry_signature_payload(
            moved,
            signer_account_uuid=self.identity.uuid,
            tag_uuids=list(moved.tags.values_list("uuid", flat=True)),
            fields=dict(moved.fields.values_list("field_id", "encrypted_value")),
        )
        return {"uuid": str(entry.uuid), "metadata_sig": sign(self.signer, payload)}

    def _post(self, folder, body):
        return self.client.post(self.url(folder), body, "application/json")

    # --- the happy path ---------------------------------------------------

    def test_the_composite_flow_empties_the_folder_and_removes_it(self):
        body = {"entries": [self.resigned(entry) for entry in self.entries]}
        response = self._post(self.folder, body)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(VaultFolder.objects.filter(uuid=self.folder.uuid).exists())
        for entry in self.entries:
            entry.refresh_from_db()
            self.assertIsNone(entry.folder_id)

    def test_every_entry_signature_still_verifies_afterwards(self):
        """The acceptance criterion of the issue: a legitimate deletion must
        not leave one row the client will read as tampered."""
        body = {"entries": [self.resigned(entry) for entry in self.entries]}
        self._post(self.folder, body)
        for entry in self.entries:
            entry.refresh_from_db()
            verify_record(
                entry_signature_payload(
                    entry,
                    signer_account_uuid=self.identity.uuid,
                    tag_uuids=list(entry.tags.values_list("uuid", flat=True)),
                    fields=dict(
                        entry.fields.values_list("field_id", "encrypted_value")
                    ),
                ),
                self.identity.sig_public,
                entry.metadata_sig,
            )

    def test_deleting_an_empty_folder_needs_no_entries(self):
        response = self._post(self.empty_folder, {"entries": []})
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            VaultFolder.objects.filter(uuid=self.empty_folder.uuid).exists()
        )

    # --- refusals ---------------------------------------------------------

    def test_deleting_a_populated_folder_without_its_entries_is_refused(self):
        response = self._post(self.folder, {"entries": []})
        self.assertEqual(response.status_code, 409)
        self.assertTrue(VaultFolder.objects.filter(uuid=self.folder.uuid).exists())

    def test_one_bad_signature_rolls_the_whole_thing_back(self):
        """Every position in turn, because the endpoint decides its own order.

        Tampering one fixed entry proves nothing on its own: if that one
        happens to be verified first, no write precedes the refusal and a
        `return` from inside the atomic block - which commits, where a raise
        rolls back - would pass just as well.
        """
        for tampered in range(len(self.entries)):
            with self.subTest(tampered=tampered):
                body = {"entries": [self.resigned(entry) for entry in self.entries]}
                body["entries"][tampered]["metadata_sig"] = self.some_other_signature
                response = self._post(self.folder, body)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(
                    VaultFolder.objects.filter(uuid=self.folder.uuid).exists()
                )
                for entry in self.entries:
                    entry.refresh_from_db()
                    self.assertEqual(entry.folder_id, self.folder.pk)
                    self.assertEqual(entry.metadata_sig, "AQ")

    def test_an_entry_from_another_folder_is_refused(self):
        body = {"entries": [self.resigned(self.entry_elsewhere)]}
        response = self._post(self.folder, body)
        self.assertEqual(response.status_code, 409)
        self.entry_elsewhere.refresh_from_db()
        self.assertIsNone(self.entry_elsewhere.folder_id)

    def test_a_trashed_entry_counts_too(self):
        """A soft-deleted entry still holds folder_id under RESTRICT, so a
        client that skipped the trash would hit a 409 it cannot explain."""
        self.entries[0].deleted_at = timezone.now()
        self.entries[0].save(update_fields=["deleted_at"])
        body = {"entries": [self.resigned(entry) for entry in self.entries[1:]]}
        response = self._post(self.folder, body)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(VaultFolder.objects.filter(uuid=self.folder.uuid).exists())

    def test_a_batch_above_the_cap_is_refused_not_truncated(self):
        body = {"entries": [self.resigned(self.entries[0])] * 501}
        response = self._post(self.folder, body)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(VaultFolder.objects.filter(uuid=self.folder.uuid).exists())

    def test_deleting_a_folder_of_another_vault_answers_404(self):
        response = self._post(self.other_folder, {"entries": []})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            VaultFolder.objects.filter(uuid=self.other_folder.uuid).exists()
        )

    def test_an_anonymous_caller_is_refused(self):
        self.client.logout()
        response = self._post(self.folder, {"entries": []})
        self.assertIn(response.status_code, (302, 403))

    def test_a_folder_that_still_has_children_is_refused(self):
        """Deleting it would CASCADE the children away - folders the client
        never named, whose own signatures would vanish with them - and, when a
        child still holds entries, the RESTRICT on VaultEntry.folder turns that
        cascade into a 500."""
        child = VaultFolder.objects.create(
            vault=self.vault,
            parent=self.folder,
            encrypted_name="AQIF",
            metadata_sig="AQ",
        )
        body = {"entries": [self.resigned(entry) for entry in self.entries]}
        response = self._post(self.folder, body)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(VaultFolder.objects.filter(uuid=child.uuid).exists())
        self.assertTrue(VaultFolder.objects.filter(uuid=self.folder.uuid).exists())
        for entry in self.entries:
            entry.refresh_from_db()
            self.assertEqual(entry.folder_id, self.folder.pk)

    def test_a_child_holding_entries_is_refused_rather_than_raising(self):
        child = VaultFolder.objects.create(
            vault=self.vault,
            parent=self.folder,
            encrypted_name="AQIF",
            metadata_sig="AQ",
        )
        self._entry(child, 7)
        body = {"entries": [self.resigned(entry) for entry in self.entries]}
        response = self._post(self.folder, body)
        self.assertEqual(response.status_code, 409)

    def test_an_entry_that_arrives_after_the_check_answers_409_not_500(self):
        """The occupants are read inside the transaction, but a concurrent
        write can still land between that read and the delete. RESTRICT then
        surfaces as an IntegrityError where the same state, seen in time, is a
        409 - and the caller must not be able to tell the two apart."""
        body = {"entries": [self.resigned(entry) for entry in self.entries]}
        with mock.patch.object(
            VaultFolder,
            "delete",
            side_effect=RestrictedError("still referenced", set()),
        ):
            response = self._post(self.folder, body)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(VaultFolder.objects.filter(uuid=self.folder.uuid).exists())
        for entry in self.entries:
            entry.refresh_from_db()
            self.assertEqual(entry.folder_id, self.folder.pk)
            self.assertEqual(entry.metadata_sig, "AQ")
