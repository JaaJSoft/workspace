"""The folders endpoint.

A folder's parent and its position are plaintext, so the signature is the only
thing covering them: every test that changes one of those without re-signing
must be refused, and that is what most of this file asserts.
"""

import uuid

from django.test import TestCase

from workspace.vault.models import VaultFolder
from workspace.vault.services.metadata import folder_metadata_payload
from workspace.vault.tests.factories import make_account, make_vault, sign

LIST_URL = "/api/v1/vault/folders"


class FolderApiTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)

        self.other_user, self.other_signer, self.other_identity = make_account(
            "stranger"
        )
        self.other_vault = make_vault(self.other_user)
        self.other_vault_folder = VaultFolder.objects.create(
            vault=self.other_vault,
            encrypted_name="AQEBAAEGb3RoZXI",
            metadata_sig="AXNpZ25hdHVyZQ",
        )

    def sign_folder(self, body, *, vault=None, signer=None, identity=None):
        """The signature over the payload the server will rebuild from *body*."""
        payload = folder_metadata_payload(
            folder_uuid=body["uuid"],
            vault_uuid=(vault or self.vault).uuid,
            signer_account_uuid=(identity or self.identity).uuid,
            parent_uuid=body["parent"],
            position=body["position"],
            encrypted_name=body["encrypted_name"],
        )
        return sign(signer or self.signer, payload)

    def signed_folder(
        self,
        *,
        encrypted_name="AQID",
        position=0,
        parent=None,
        vault=None,
        signer=None,
        identity=None,
        folder_uuid=None,
    ):
        body = {
            "uuid": str(folder_uuid or uuid.uuid4()),
            "vault": str((vault or self.vault).uuid),
            "parent": str(parent.uuid) if parent is not None else None,
            "encrypted_name": encrypted_name,
            "position": position,
        }
        body["metadata_sig"] = self.sign_folder(
            body, vault=vault, signer=signer, identity=identity
        )
        return body

    def _create(self, body):
        return self.client.post(LIST_URL, body, "application/json")

    # --- reads ------------------------------------------------------------

    def test_listing_returns_the_folders_of_a_vault_the_caller_can_open(self):
        folder = VaultFolder.objects.create(
            vault=self.vault, encrypted_name="AQID", metadata_sig="AQ"
        )
        response = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["uuid"] for row in response.json()], [str(folder.uuid)])

    def test_listing_requires_a_vault_the_caller_can_open(self):
        response = self.client.get(f"{LIST_URL}?vault={self.other_vault.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_a_malformed_vault_filter_answers_400(self):
        response = self.client.get(f"{LIST_URL}?vault=not-a-uuid")
        self.assertEqual(response.status_code, 400)

    def test_a_missing_vault_filter_answers_400(self):
        self.assertEqual(self.client.get(LIST_URL).status_code, 400)

    # --- creation ---------------------------------------------------------

    def test_creating_a_folder_stores_the_signature(self):
        body = self.signed_folder()
        response = self._create(body)
        self.assertEqual(response.status_code, 201)
        folder = VaultFolder.objects.get(uuid=body["uuid"])
        self.assertEqual(folder.metadata_sig, body["metadata_sig"])
        self.assertEqual(folder.vault_id, self.vault.pk)

    def test_creating_a_folder_under_a_parent_of_the_same_vault_is_accepted(self):
        created = self._create(self.signed_folder()).json()
        parent = VaultFolder.objects.get(uuid=created["uuid"])
        body = self.signed_folder(parent=parent, position=1)
        self.assertEqual(self._create(body).status_code, 201)
        self.assertEqual(
            VaultFolder.objects.get(uuid=body["uuid"]).parent_id, parent.pk
        )

    def test_a_folder_signed_over_another_position_is_refused(self):
        body = self.signed_folder(position=0)
        body["position"] = 7
        response = self._create(body)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(VaultFolder.objects.filter(uuid=body["uuid"]).exists())

    def test_a_folder_signed_over_another_name_is_refused(self):
        body = self.signed_folder()
        body["encrypted_name"] = "AQIE"
        self.assertEqual(self._create(body).status_code, 400)

    def test_an_unsigned_folder_is_refused(self):
        body = self.signed_folder()
        body["metadata_sig"] = ""
        self.assertEqual(self._create(body).status_code, 400)

    def test_a_folder_signed_by_another_account_is_refused(self):
        """The server names the caller in the payload it rebuilds, so a
        signature made over anyone else's identity simply fails to verify."""
        body = self.signed_folder(
            signer=self.other_signer, identity=self.other_identity
        )
        self.assertEqual(self._create(body).status_code, 400)

    def test_creating_a_folder_in_another_vault_answers_404(self):
        body = self.signed_folder(vault=self.other_vault)
        self.assertEqual(self._create(body).status_code, 404)
        self.assertFalse(VaultFolder.objects.filter(uuid=body["uuid"]).exists())

    def test_a_parent_from_another_vault_is_refused(self):
        body = self.signed_folder(parent=self.other_vault_folder)
        self.assertEqual(self._create(body).status_code, 400)

    def test_a_parent_that_does_not_exist_answers_400_not_500(self):
        body = self.signed_folder()
        body["parent"] = str(uuid.uuid4())
        body["metadata_sig"] = self.sign_folder(body)
        self.assertEqual(self._create(body).status_code, 400)

    def test_a_client_supplied_uuid_never_takes_over_an_existing_row(self):
        body = self.signed_folder(folder_uuid=self.other_vault_folder.uuid)
        response = self._create(body)
        self.assertEqual(response.status_code, 409)
        self.other_vault_folder.refresh_from_db()
        self.assertEqual(self.other_vault_folder.vault_id, self.other_vault.pk)
        self.assertEqual(self.other_vault_folder.encrypted_name, "AQEBAAEGb3RoZXI")

    # --- updates ----------------------------------------------------------

    def test_renaming_a_folder_rewrites_its_signature(self):
        created = self._create(self.signed_folder()).json()
        body = self.signed_folder(
            folder_uuid=created["uuid"], encrypted_name="AQIE", position=3
        )
        response = self.client.patch(
            f"{LIST_URL}/{created['uuid']}", body, "application/json"
        )
        self.assertEqual(response.status_code, 200)
        folder = VaultFolder.objects.get(uuid=created["uuid"])
        self.assertEqual(folder.encrypted_name, "AQIE")
        self.assertEqual(folder.position, 3)
        self.assertEqual(folder.metadata_sig, body["metadata_sig"])

    def test_renaming_with_a_stale_signature_is_refused(self):
        created = self._create(self.signed_folder()).json()
        body = self.signed_folder(folder_uuid=created["uuid"], encrypted_name="AQIE")
        body["encrypted_name"] = "AQIF"
        response = self.client.patch(
            f"{LIST_URL}/{created['uuid']}", body, "application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            VaultFolder.objects.get(uuid=created["uuid"]).encrypted_name, "AQID"
        )

    def test_renaming_a_folder_in_a_vault_the_caller_cannot_open_answers_404(self):
        body = self.signed_folder(folder_uuid=self.other_vault_folder.uuid)
        response = self.client.patch(
            f"{LIST_URL}/{self.other_vault_folder.uuid}", body, "application/json"
        )
        self.assertEqual(response.status_code, 404)

    def test_a_folder_cannot_be_moved_into_another_vault(self):
        created = self._create(self.signed_folder()).json()
        body = self.signed_folder(folder_uuid=created["uuid"], vault=self.other_vault)
        response = self.client.patch(
            f"{LIST_URL}/{created['uuid']}", body, "application/json"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            VaultFolder.objects.get(uuid=created["uuid"]).vault_id, self.vault.pk
        )

    def test_a_folder_cannot_become_its_own_parent(self):
        created = self._create(self.signed_folder()).json()
        body = self.signed_folder(folder_uuid=created["uuid"])
        body["parent"] = created["uuid"]
        body["metadata_sig"] = self.sign_folder(body)
        response = self.client.patch(
            f"{LIST_URL}/{created['uuid']}", body, "application/json"
        )
        self.assertEqual(response.status_code, 400)

    # --- authentication ---------------------------------------------------

    def test_an_anonymous_caller_is_refused(self):
        self.client.logout()
        response = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}")
        self.assertIn(response.status_code, (302, 403))
