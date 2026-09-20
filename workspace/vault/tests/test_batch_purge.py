"""The batch purge endpoint.

Every test here is written so that removing the guard it covers makes it
fail, and none of them asserts a status code alone: a refusal issued after
the rows were destroyed returns the right number and leaves the wrong
database.
"""

import uuid
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.vault.models import EntryField, EntryType, VaultEntry, VaultRole
from workspace.vault.tests.factories import make_account, make_key_wrap, make_vault

PURGE_URL = "/api/v1/vault/entries/purge"


class BatchPurgeTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)
        self.other_user, _, _ = make_account("stranger")
        self.other_vault = make_vault(self.other_user)

    def _entry(self, vault=None, *, trashed=True, fields=()):
        entry = VaultEntry.objects.create(
            vault=vault or self.vault,
            type=EntryType.LOGIN,
            encrypted_name="AQID",
            metadata_sig="AQ",
            deleted_at=timezone.now() if trashed else None,
        )
        for field_id in fields:
            EntryField.objects.create(
                entry=entry, field_id=field_id, encrypted_value="Ag"
            )
        return entry

    def _post(self, body):
        return self.client.post(PURGE_URL, body, content_type="application/json")

    # ---- the uuid form ---------------------------------------------------

    def test_a_batch_destroys_every_named_entry_and_its_fields(self):
        first = self._entry(fields=["password"])
        second = self._entry(fields=["password", "totp"])

        response = self._post({"uuids": [str(first.uuid), str(second.uuid)]})

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            response.json()["destroyed"], [str(first.uuid), str(second.uuid)]
        )
        self.assertFalse(VaultEntry.objects.exists())
        self.assertFalse(EntryField.objects.exists())

    def test_one_live_entry_in_the_batch_destroys_none_of_them(self):
        """Atomic is the whole point: a caller told 409 must be able to
        believe its trash is exactly as it left it."""
        trashed = self._entry()
        live = self._entry(trashed=False)

        response = self._post({"uuids": [str(trashed.uuid), str(live.uuid)]})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(VaultEntry.objects.count(), 2)

    def test_one_unreachable_uuid_in_the_batch_destroys_none_of_them(self):
        mine = self._entry()
        theirs = self._entry(vault=self.other_vault)

        response = self._post({"uuids": [str(mine.uuid), str(theirs.uuid)]})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(VaultEntry.objects.count(), 2)

    def test_a_uuid_naming_nothing_destroys_none_of_them(self):
        mine = self._entry()

        response = self._post({"uuids": [str(mine.uuid), str(uuid.uuid4())]})

        self.assertEqual(response.status_code, 404)
        self.assertTrue(VaultEntry.objects.filter(uuid=mine.uuid).exists())

    def test_a_member_who_is_not_the_owner_is_refused(self):
        """delete_forever is declared owner-only on the registry, and the
        batch has to say the same thing the single-entry endpoint says."""
        make_key_wrap(self.other_vault, self.user)
        theirs = self._entry(vault=self.other_vault)

        response = self._post({"uuids": [str(theirs.uuid)]})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(VaultEntry.objects.filter(uuid=theirs.uuid).exists())

    def test_a_batch_over_the_cap_is_refused(self):
        response = self._post({"uuids": [str(uuid.uuid4()) for _ in range(201)]})

        self.assertEqual(response.status_code, 400)
        self.assertIn("200", response.json()["detail"])

    def test_a_malformed_uuid_is_refused(self):
        entry = self._entry()

        response = self._post({"uuids": [str(entry.uuid), "not-a-uuid"]})

        self.assertEqual(response.status_code, 400)
        self.assertTrue(VaultEntry.objects.filter(uuid=entry.uuid).exists())

    def test_an_empty_uuid_list_is_refused(self):
        response = self._post({"uuids": []})

        self.assertEqual(response.status_code, 400)

    # ---- the vault form --------------------------------------------------

    def test_the_vault_form_empties_the_whole_trash_in_one_request(self):
        trashed = [self._entry(fields=["password"]) for _ in range(3)]
        live = self._entry(trashed=False)

        response = self._post({"vault": str(self.vault.uuid)})

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            response.json()["destroyed"], [str(entry.uuid) for entry in trashed]
        )
        self.assertEqual(
            list(VaultEntry.objects.values_list("uuid", flat=True)), [live.uuid]
        )
        self.assertFalse(EntryField.objects.exists())

    def test_the_vault_form_is_not_capped(self):
        """The acceptance criterion the uuid form cannot meet on its own: a
        trash larger than the batch cap still goes in one request."""
        for _ in range(201):
            self._entry()

        response = self._post({"vault": str(self.vault.uuid)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["destroyed"]), 201)
        self.assertFalse(VaultEntry.objects.exists())

    def test_emptying_an_empty_trash_is_harmless(self):
        live = self._entry(trashed=False)

        response = self._post({"vault": str(self.vault.uuid)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["destroyed"], [])
        self.assertTrue(VaultEntry.objects.filter(uuid=live.uuid).exists())

    def test_the_vault_form_is_refused_to_a_member(self):
        """A key wrap opens a vault; it does not hand over the one action
        nothing can undo. The same claim the uuid form makes, read through
        the other door."""
        make_key_wrap(self.other_vault, self.user)
        theirs = self._entry(vault=self.other_vault)

        response = self._post({"vault": str(self.other_vault.uuid)})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(VaultEntry.objects.filter(uuid=theirs.uuid).exists())

    def test_the_vault_form_is_refused_to_a_member_with_an_empty_trash(self):
        """The case the test above cannot reach: with nothing to destroy the
        per-row loop never runs, so the owner check before it is the only
        thing standing between a member and a 200 saying their request
        worked."""
        make_key_wrap(self.other_vault, self.user)
        live = self._entry(vault=self.other_vault, trashed=False)

        response = self._post({"vault": str(self.other_vault.uuid)})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(VaultEntry.objects.filter(uuid=live.uuid).exists())

    def test_a_vault_out_of_reach_answers_404(self):
        theirs = self._entry(vault=self.other_vault)

        response = self._post({"vault": str(self.other_vault.uuid)})

        self.assertEqual(response.status_code, 404)
        self.assertTrue(VaultEntry.objects.filter(uuid=theirs.uuid).exists())

    def test_a_malformed_vault_uuid_answers_404(self):
        """404 rather than 400: `vault` names one resource, and telling
        malformed apart from not-yours is a distinction worth not making."""
        response = self._post({"vault": "not-a-uuid"})

        self.assertEqual(response.status_code, 404)

    # ---- the body itself -------------------------------------------------

    def test_sending_both_forms_is_refused(self):
        entry = self._entry()

        response = self._post(
            {"uuids": [str(entry.uuid)], "vault": str(self.vault.uuid)}
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(VaultEntry.objects.filter(uuid=entry.uuid).exists())

    def test_sending_neither_form_is_refused(self):
        response = self._post({})

        self.assertEqual(response.status_code, 400)

    def test_a_body_that_is_not_an_object_is_refused(self):
        """A JSON array arrives as a list, and asking a list for a key is a
        500 where the same body deserves a 400."""
        response = self._post(["nope"])

        self.assertEqual(response.status_code, 400)

    # ---- the race --------------------------------------------------------

    def test_an_entry_restored_between_the_check_and_the_delete_stops_it(self):
        """Check-then-act: the trash check reads rows outside any lock, so a
        restore landing before the delete would destroy an entry the user has
        just been told is back. The patched role resolver runs in exactly
        that window and stands in for the racing request."""
        first = self._entry()
        second = self._entry()

        def restore_then_answer(user, vault_ids):
            VaultEntry.objects.filter(uuid=second.uuid).update(deleted_at=None)
            return {self.vault.uuid: VaultRole.OWNER}

        with patch("workspace.vault.views.entries.vault_roles", restore_then_answer):
            response = self._post({"uuids": [str(first.uuid), str(second.uuid)]})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(VaultEntry.objects.count(), 2)

    def test_the_endpoint_does_not_scale_its_queries_with_the_batch(self):
        """Three rows must not cost three times what one costs: the role
        lookup and the field prefetch are batched on purpose, and a per-row
        rewrite would pass every other test in this file."""
        alone = self._entry(fields=["password"])
        several = [self._entry(fields=["password"]) for _ in range(3)]

        with CaptureQueriesContext(connection) as one_row:
            self.assertEqual(self._post({"uuids": [str(alone.uuid)]}).status_code, 200)
        with CaptureQueriesContext(connection) as three_rows:
            response = self._post({"uuids": [str(e.uuid) for e in several]})
            self.assertEqual(response.status_code, 200)

        self.assertEqual(len(three_rows), len(one_row))
