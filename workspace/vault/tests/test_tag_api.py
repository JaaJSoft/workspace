"""The tags endpoint.

A tag's colour is plaintext, so its signature is the only thing covering it.
Deleting one is allowed here, unlike deleting a folder: nothing points at a tag
under RESTRICT, and the entries that lose it are left for their owner to
re-sign - the server must never rewrite a signature on a client's behalf.
"""

import uuid

from django.test import TestCase

from workspace.vault.models import EntryType, VaultEntry, VaultTag
from workspace.vault.services.metadata import tag_metadata_payload
from workspace.vault.tests.factories import make_account, make_vault, sign

LIST_URL = "/api/v1/vault/tags"


class TagApiTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)

        self.other_user, self.other_signer, self.other_identity = make_account(
            "stranger"
        )
        self.other_vault = make_vault(self.other_user)
        self.other_vault_tag = VaultTag.objects.create(
            vault=self.other_vault,
            encrypted_name="AQEBAAEDdGFn",
            metadata_sig="AXNpZ25hdHVyZQ",
        )

        self.tag = VaultTag.objects.create(
            vault=self.vault, encrypted_name="AQID", metadata_sig="AQ"
        )
        self.original_sig = "AXNpZ25hdHVyZQ"
        self.entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name="AQID",
            metadata_sig=self.original_sig,
        )

    def sign_tag(self, body, *, vault=None, signer=None, identity=None):
        payload = tag_metadata_payload(
            tag_uuid=body["uuid"],
            vault_uuid=(vault or self.vault).uuid,
            signer_account_uuid=(identity or self.identity).uuid,
            encrypted_name=body["encrypted_name"],
            color=body["color"],
        )
        return sign(signer or self.signer, payload)

    def signed_tag(
        self,
        *,
        encrypted_name="AQID",
        color="primary",
        vault=None,
        signer=None,
        identity=None,
        tag_uuid=None,
    ):
        body = {
            "uuid": str(tag_uuid or uuid.uuid4()),
            "vault": str((vault or self.vault).uuid),
            "encrypted_name": encrypted_name,
            "color": color,
        }
        body["metadata_sig"] = self.sign_tag(
            body, vault=vault, signer=signer, identity=identity
        )
        return body

    def _create(self, body):
        return self.client.post(LIST_URL, body, "application/json")

    # --- reads ------------------------------------------------------------

    def test_listing_returns_the_tags_of_a_vault_the_caller_can_open(self):
        response = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["uuid"] for row in response.json()], [str(self.tag.uuid)])

    def test_listing_requires_a_vault_the_caller_can_open(self):
        response = self.client.get(f"{LIST_URL}?vault={self.other_vault.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_a_malformed_vault_filter_answers_400(self):
        self.assertEqual(
            self.client.get(f"{LIST_URL}?vault=not-a-uuid").status_code, 400
        )

    # --- creation ---------------------------------------------------------

    def test_creating_a_tag_stores_the_signature(self):
        body = self.signed_tag()
        response = self._create(body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            VaultTag.objects.get(uuid=body["uuid"]).metadata_sig, body["metadata_sig"]
        )

    def test_a_tag_signed_over_another_colour_is_refused(self):
        body = self.signed_tag(color="primary")
        body["color"] = "error"
        response = self._create(body)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(VaultTag.objects.filter(uuid=body["uuid"]).exists())

    def test_an_unsigned_tag_is_refused(self):
        body = self.signed_tag()
        body["metadata_sig"] = ""
        self.assertEqual(self._create(body).status_code, 400)

    def test_a_tag_signed_by_another_account_is_refused(self):
        body = self.signed_tag(signer=self.other_signer, identity=self.other_identity)
        self.assertEqual(self._create(body).status_code, 400)

    def test_creating_a_tag_in_another_vault_answers_404(self):
        body = self.signed_tag(vault=self.other_vault)
        self.assertEqual(self._create(body).status_code, 404)
        self.assertFalse(VaultTag.objects.filter(uuid=body["uuid"]).exists())

    def test_a_client_supplied_uuid_never_takes_over_an_existing_row(self):
        body = self.signed_tag(tag_uuid=self.other_vault_tag.uuid)
        response = self._create(body)
        self.assertEqual(response.status_code, 409)
        self.other_vault_tag.refresh_from_db()
        self.assertEqual(self.other_vault_tag.vault_id, self.other_vault.pk)
        self.assertEqual(self.other_vault_tag.encrypted_name, "AQEBAAEDdGFn")

    # --- updates and deletion ---------------------------------------------

    def test_recolouring_a_tag_rewrites_its_signature(self):
        body = self.signed_tag(tag_uuid=self.tag.uuid, color="error")
        response = self.client.patch(
            f"{LIST_URL}/{self.tag.uuid}", body, "application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.color, "error")
        self.assertEqual(self.tag.metadata_sig, body["metadata_sig"])

    def test_recolouring_with_a_stale_signature_is_refused(self):
        body = self.signed_tag(tag_uuid=self.tag.uuid, color="error")
        body["color"] = "accent"
        response = self.client.patch(
            f"{LIST_URL}/{self.tag.uuid}", body, "application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.color, "neutral")

    def test_renaming_a_tag_in_a_vault_the_caller_cannot_open_answers_404(self):
        body = self.signed_tag(tag_uuid=self.other_vault_tag.uuid)
        response = self.client.patch(
            f"{LIST_URL}/{self.other_vault_tag.uuid}", body, "application/json"
        )
        self.assertEqual(response.status_code, 404)

    def test_deleting_a_tag_detaches_it_from_its_entries(self):
        self.entry.tags.add(self.tag)
        response = self.client.delete(f"{LIST_URL}/{self.tag.uuid}")
        self.assertEqual(response.status_code, 204)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.tags.count(), 0)
        # The entry's signature now covers a tag set the row no longer has.
        # That is the client's to repair; what must not happen is the server
        # rewriting metadata_sig on its behalf.
        self.assertEqual(self.entry.metadata_sig, self.original_sig)

    def test_deleting_a_tag_of_another_vault_answers_404(self):
        response = self.client.delete(f"{LIST_URL}/{self.other_vault_tag.uuid}")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            VaultTag.objects.filter(uuid=self.other_vault_tag.uuid).exists()
        )

    # --- authentication ---------------------------------------------------

    def test_an_anonymous_caller_is_refused(self):
        self.client.logout()
        response = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}")
        self.assertIn(response.status_code, (302, 403))
