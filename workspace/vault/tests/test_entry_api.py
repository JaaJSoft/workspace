"""The entries endpoint.

Five of these tests are the five authorization holes the design doc names, and
each is written so that deleting the guard it covers makes it fail - a refusal
issued *after* the row was created returns the right status and leaves the
wrong database, so the status code is never asserted alone.
"""

import uuid

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.vault.models import (
    EntryField,
    EntryType,
    VaultEntry,
    VaultFolder,
    VaultTag,
)
from workspace.vault.services.entries import entry_signature_payload
from workspace.vault.tests.factories import make_account, make_vault, sign

LIST_URL = "/api/v1/vault/entries"


class EntryApiTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)
        self.folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQID", metadata_sig="AQ"
        )
        self.tag = VaultTag.objects.create(
            vault=self.vault, encrypted_name="AQID", metadata_sig="AQ"
        )
        self.entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name="AQID",
            metadata_sig="AQ",
        )

        self.other_user, self.other_signer, self.other_identity = make_account(
            "stranger"
        )
        self.other_vault = make_vault(self.other_user)
        self.other_vault_folder = VaultFolder.objects.create(
            vault=self.other_vault, encrypted_name="AQID", metadata_sig="AQ"
        )
        self.other_vault_tag = VaultTag.objects.create(
            vault=self.other_vault, encrypted_name="AQID", metadata_sig="AQ"
        )
        self.other_entry_name = "AQEBAAEFb3RoZXI"
        self.other_entry = VaultEntry.objects.create(
            vault=self.other_vault,
            type=EntryType.LOGIN,
            encrypted_name=self.other_entry_name,
            metadata_sig="AXNpZ25hdHVyZQ",
        )

    # --- fixtures ---------------------------------------------------------

    def signed_entry(
        self,
        *,
        entry_uuid=None,
        vault=None,
        folder=None,
        tags=(),
        fields=None,
        encrypted_name="AQID",
        encrypted_notes="",
        is_favorite=False,
        entry_type=EntryType.LOGIN,
        signer=None,
        identity=None,
        key_version=1,
        entry_version=1,
    ):
        vault = vault or self.vault
        fields = {"password": "Ag"} if fields is None else fields
        body = {
            "uuid": str(entry_uuid or uuid.uuid4()),
            "vault": str(vault.uuid),
            "type": entry_type,
            "folder": str(folder.uuid) if folder is not None else None,
            "tags": [str(tag.uuid) for tag in tags],
            "is_favorite": is_favorite,
            "encrypted_name": encrypted_name,
            "encrypted_notes": encrypted_notes,
            "fields": dict(fields),
        }
        unsaved = VaultEntry(
            uuid=body["uuid"],
            vault=vault,
            type=entry_type,
            folder=folder,
            is_favorite=is_favorite,
            encrypted_name=encrypted_name,
            encrypted_notes=encrypted_notes,
            key_version=key_version,
            entry_version=entry_version,
        )
        payload = entry_signature_payload(
            unsaved,
            signer_account_uuid=(identity or self.identity).uuid,
            tag_uuids=[tag.uuid for tag in tags],
            fields=body["fields"],
        )
        body["metadata_sig"] = sign(signer or self.signer, payload)
        return body

    def _create(self, body):
        return self.client.post(LIST_URL, body, "application/json")

    # --- the five authorization holes -------------------------------------

    def test_writing_an_entry_in_a_vault_the_caller_cannot_open_answers_404(self):
        """Hole 1 - writing an entry with no right to the vault."""
        body = self.signed_entry(vault=self.other_vault)
        response = self._create(body)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(VaultEntry.objects.filter(uuid=body["uuid"]).exists())

    def test_a_folder_from_another_vault_is_refused(self):
        """Hole 2 - a folder belonging to a different vault."""
        body = self.signed_entry(folder=self.other_vault_folder)
        response = self._create(body)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(VaultEntry.objects.filter(uuid=body["uuid"]).exists())

    def test_a_client_uuid_never_takes_over_an_existing_entry(self):
        """Hole 3 - the client-supplied identifier."""
        body = self.signed_entry(entry_uuid=self.other_entry.uuid)
        response = self._create(body)
        self.assertEqual(response.status_code, 409)
        self.other_entry.refresh_from_db()
        self.assertEqual(self.other_entry.vault_id, self.other_vault.pk)
        self.assertEqual(self.other_entry.encrypted_name, self.other_entry_name)

    def test_updating_an_entry_the_caller_cannot_reach_answers_404(self):
        """Hole 4 - writing to an entry with no right to it."""
        body = self.signed_entry(entry_uuid=self.other_entry.uuid)
        response = self.client.put(
            f"{LIST_URL}/{self.other_entry.uuid}", body, "application/json"
        )
        self.assertEqual(response.status_code, 404)
        self.other_entry.refresh_from_db()
        self.assertEqual(self.other_entry.encrypted_name, self.other_entry_name)

    def test_a_tag_from_another_vault_is_refused(self):
        """Hole 5, this project's own: VaultEntry.clean() cannot see it,
        because Django validates a many-to-many only once the row exists."""
        body = self.signed_entry(tags=[self.other_vault_tag])
        response = self._create(body)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(VaultEntry.objects.filter(uuid=body["uuid"]).exists())

    # --- the field catalogue ----------------------------------------------

    def test_a_field_outside_the_catalogue_is_refused(self):
        body = self.signed_entry(fields={"pin": "Ag"})
        self.assertEqual(self._create(body).status_code, 400)

    def test_a_prefixed_field_is_accepted(self):
        body = self.signed_entry(fields={"custom:pin": "Ag"})
        response = self._create(body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            {row["field_id"] for row in response.json()["entry_fields"]},
            {"custom:pin"},
        )

    def test_a_field_named_like_an_entry_column_is_refused(self):
        for field_id in ("name", "notes"):
            body = self.signed_entry(fields={field_id: "Ag"})
            self.assertEqual(self._create(body).status_code, 400, field_id)

    # --- the signature over the tag and field sets ------------------------

    def test_a_signed_entry_is_created_with_its_fields_and_tags(self):
        body = self.signed_entry(
            folder=self.folder, tags=[self.tag], fields={"password": "Ag", "totp": "Aw"}
        )
        response = self._create(body)
        self.assertEqual(response.status_code, 201)
        entry = VaultEntry.objects.get(uuid=body["uuid"])
        self.assertEqual(entry.folder_id, self.folder.pk)
        self.assertEqual(list(entry.tags.all()), [self.tag])
        self.assertEqual(
            {field.field_id: field.encrypted_value for field in entry.fields.all()},
            {"password": "Ag", "totp": "Aw"},
        )
        self.assertEqual(entry.metadata_sig, body["metadata_sig"])

    def test_an_entry_signed_over_a_shorter_field_set_is_refused(self):
        body = self.signed_entry(fields={"password": "Ag", "totp": "Aw"})
        del body["fields"]["totp"]
        response = self._create(body)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(VaultEntry.objects.filter(uuid=body["uuid"]).exists())

    def test_a_replayed_field_ciphertext_is_refused(self):
        body = self.signed_entry(fields={"password": "Ag"})
        body["fields"]["password"] = "Bg"
        self.assertEqual(self._create(body).status_code, 400)

    def test_an_entry_signed_over_a_different_tag_set_is_refused(self):
        body = self.signed_entry(tags=[self.tag])
        body["tags"] = []
        self.assertEqual(self._create(body).status_code, 400)

    def test_an_entry_signed_over_another_folder_is_refused(self):
        body = self.signed_entry(folder=self.folder)
        body["folder"] = None
        self.assertEqual(self._create(body).status_code, 400)

    def test_an_entry_signed_by_another_account_is_refused(self):
        body = self.signed_entry(signer=self.other_signer, identity=self.other_identity)
        self.assertEqual(self._create(body).status_code, 400)

    def test_an_unsigned_entry_is_refused(self):
        body = self.signed_entry()
        body["metadata_sig"] = ""
        self.assertEqual(self._create(body).status_code, 400)

    # --- updates ----------------------------------------------------------

    def test_a_put_replaces_the_field_set(self):
        created = self._create(
            self.signed_entry(fields={"password": "Ag", "totp": "Aw"})
        ).json()
        body = self.signed_entry(entry_uuid=created["uuid"], fields={"password": "Bg"})
        response = self.client.put(
            f"{LIST_URL}/{created['uuid']}", body, "application/json"
        )
        self.assertEqual(response.status_code, 200)
        entry = VaultEntry.objects.get(uuid=created["uuid"])
        self.assertEqual(
            {field.field_id: field.encrypted_value for field in entry.fields.all()},
            {"password": "Bg"},
        )

    def test_a_put_whose_body_names_another_entry_is_refused(self):
        body = self.signed_entry()
        response = self.client.put(
            f"{LIST_URL}/{self.entry.uuid}", body, "application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_deleting_an_entry_is_soft_and_leaves_the_signature_alone(self):
        created = self._create(self.signed_entry()).json()
        response = self.client.delete(f"{LIST_URL}/{created['uuid']}")
        self.assertEqual(response.status_code, 204)
        entry = VaultEntry.objects.get(uuid=created["uuid"])
        self.assertIsNotNone(entry.deleted_at)
        self.assertEqual(entry.metadata_sig, created["metadata_sig"])

    def test_deleting_an_entry_of_another_vault_answers_404(self):
        response = self.client.delete(f"{LIST_URL}/{self.other_entry.uuid}")
        self.assertEqual(response.status_code, 404)
        self.other_entry.refresh_from_db()
        self.assertIsNone(self.other_entry.deleted_at)

    # --- reads ------------------------------------------------------------

    def test_reading_an_entry_of_another_vault_answers_404(self):
        response = self.client.get(f"{LIST_URL}/{self.other_entry.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_a_malformed_entry_uuid_in_the_path_answers_404(self):
        response = self.client.get(f"{LIST_URL}/not-a-uuid")
        self.assertEqual(response.status_code, 404)

    def test_a_malformed_vault_filter_answers_400(self):
        self.assertEqual(
            self.client.get(f"{LIST_URL}?vault=not-a-uuid").status_code, 400
        )

    def test_listing_a_vault_the_caller_cannot_open_answers_404(self):
        response = self.client.get(f"{LIST_URL}?vault={self.other_vault.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_the_trash_is_excluded_unless_asked_for(self):
        self.entry.deleted_at = timezone.now()
        self.entry.save(update_fields=["deleted_at"])
        listing = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}")
        self.assertEqual(listing.json(), [])
        trashed = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}&trashed=true")
        self.assertEqual(len(trashed.json()), 1)

    def test_the_trashed_flag_is_read_with_is_truthy(self):
        """?trashed=false must not enable the filter - Python truthiness on a
        non-empty string would do exactly that."""
        self.entry.deleted_at = timezone.now()
        self.entry.save(update_fields=["deleted_at"])
        response = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}&trashed=false")
        self.assertEqual(response.json(), [])

    def _add_entries(self, count):
        for index in range(count):
            entry = VaultEntry.objects.create(
                vault=self.vault,
                type=EntryType.LOGIN,
                encrypted_name=f"AQID{index}",
                metadata_sig="AQ",
            )
            entry.tags.add(self.tag)

    def _listing_query_count(self):
        url = f"{LIST_URL}?vault={self.vault.uuid}"
        with CaptureQueriesContext(connection) as captured:
            self.client.get(url)
        return len(captured.captured_queries)

    def test_the_listing_does_not_grow_a_query_per_entry(self):
        """Two measurements rather than one pinned number: the absolute count
        moves with whatever Django has already cached in the process, so only
        its invariance under a growing result set means anything."""
        self._add_entries(2)
        self._listing_query_count()  # warm whatever the first request caches
        with_two = self._listing_query_count()
        self._add_entries(20)
        self.assertEqual(
            len(self.client.get(f"{LIST_URL}?vault={self.vault.uuid}").json()), 23
        )
        self.assertEqual(self._listing_query_count(), with_two)

    # --- authentication ---------------------------------------------------

    def test_an_anonymous_caller_is_refused(self):
        self.client.logout()
        response = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}")
        self.assertIn(response.status_code, (302, 403))

    def test_an_over_long_custom_field_id_is_refused_not_stored(self):
        """It would outgrow EntryField.field_id: silently truncated by SQLite,
        a DataError on PostgreSQL, which is where this runs in production."""
        body = self.signed_entry(fields={f"custom:{'x' * 200}": "Ag"})
        self.assertEqual(self._create(body).status_code, 400)
        self.assertFalse(VaultEntry.objects.filter(uuid=body["uuid"]).exists())

    def test_an_over_long_field_value_is_refused(self):
        """Every other opaque value in this module is capped at 4096; a field
        ciphertext is the one that travels inside a JSON object rather than as
        a serializer field, and it must not escape the cap by doing so."""
        body = self.signed_entry(fields={"password": "A" * 5000})
        self.assertEqual(self._create(body).status_code, 400)
        self.assertFalse(VaultEntry.objects.filter(uuid=body["uuid"]).exists())

    def test_a_new_entry_takes_its_key_generation_from_the_vault(self):
        """key_version is inside the signature, so a column default would make
        a rotated vault's first entry claim a generation it was never
        encrypted under."""
        rotated = make_vault(self.user, key_version=3)
        body = self.signed_entry(vault=rotated, key_version=3)
        response = self._create(body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(VaultEntry.objects.get(uuid=body["uuid"]).key_version, 3)

    def test_an_entry_signed_over_the_wrong_key_generation_is_refused(self):
        rotated = make_vault(self.user, key_version=3)
        body = self.signed_entry(vault=rotated, key_version=1)
        self.assertEqual(self._create(body).status_code, 400)
        self.assertFalse(VaultEntry.objects.filter(uuid=body["uuid"]).exists())

    def _trash(self, entry_uuid):
        self.assertEqual(
            self.client.delete(f"{LIST_URL}/{entry_uuid}").status_code, 204
        )

    def test_restoring_brings_an_entry_back_without_touching_its_signature(self):
        """deleted_at is outside the signed payload, so the round trip through
        the trash must leave metadata_sig exactly as the client wrote it."""
        created = self._create(self.signed_entry()).json()
        self._trash(created["uuid"])
        response = self.client.post(f"{LIST_URL}/{created['uuid']}/restore")
        self.assertEqual(response.status_code, 200)
        entry = VaultEntry.objects.get(uuid=created["uuid"])
        self.assertIsNone(entry.deleted_at)
        self.assertEqual(entry.metadata_sig, created["metadata_sig"])

    def test_a_restored_entry_is_listed_again(self):
        created = self._create(self.signed_entry()).json()
        self._trash(created["uuid"])
        self.client.post(f"{LIST_URL}/{created['uuid']}/restore")
        listed = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}").json()
        self.assertIn(created["uuid"], [row["uuid"] for row in listed])

    def test_restoring_an_entry_that_was_never_trashed_is_harmless(self):
        """Idempotent on purpose: a client that retries a lost answer must not
        be told it did something wrong."""
        created = self._create(self.signed_entry()).json()
        response = self.client.post(f"{LIST_URL}/{created['uuid']}/restore")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(VaultEntry.objects.get(uuid=created["uuid"]).deleted_at)

    def test_restoring_an_entry_of_another_vault_answers_404(self):
        response = self.client.post(f"{LIST_URL}/{self.other_entry.uuid}/restore")
        self.assertEqual(response.status_code, 404)

    def test_purging_removes_the_entry_and_its_fields(self):
        created = self._create(
            self.signed_entry(fields={"password": "Ag", "totp": "Aw"})
        ).json()
        self._trash(created["uuid"])
        response = self.client.post(f"{LIST_URL}/{created['uuid']}/purge")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(VaultEntry.objects.filter(uuid=created["uuid"]).exists())
        self.assertFalse(EntryField.objects.filter(entry_id=created["uuid"]).exists())

    def test_purging_an_entry_that_is_not_in_the_trash_is_refused(self):
        """The trash is the confirmation step. Skipping it would make one
        mistyped URL destroy a live entry with no way back."""
        created = self._create(self.signed_entry()).json()
        response = self.client.post(f"{LIST_URL}/{created['uuid']}/purge")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(VaultEntry.objects.filter(uuid=created["uuid"]).exists())

    def test_purging_an_entry_of_another_vault_answers_404(self):
        response = self.client.post(f"{LIST_URL}/{self.other_entry.uuid}/purge")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(VaultEntry.objects.filter(uuid=self.other_entry.uuid).exists())
