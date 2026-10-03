"""The tags endpoint.

A tag's colour is plaintext, so its signature is the only thing covering it.
Deleting one is a transaction, like deleting a folder: every entry carrying
the tag arrives re-signed without it, and the server never writes a signature
it did not verify.
"""

import uuid

from django.test import TestCase
from django.utils import timezone

from workspace.vault.models import EntryType, VaultEntry, VaultTag
from workspace.vault.services.entries import entry_signature_payload
from workspace.vault.services.metadata import tag_metadata_payload, verify_record
from workspace.vault.tests.factories import make_account, make_vault, sealed, sign

LIST_URL = "/api/v1/vault/tags"
NAME = sealed("AQID")


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
            vault=self.vault, encrypted_name=sealed("AQID"), metadata_sig="AQ"
        )
        self.original_sig = "AXNpZ25hdHVyZQ"
        self.entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name=sealed("AQID"),
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
        encrypted_name=NAME,
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
        # The body has to name the unreachable vault, or the refusal comes from
        # the tag lookup and the vault guard is never asked anything.
        body = self.signed_tag(
            tag_uuid=self.other_vault_tag.uuid, vault=self.other_vault
        )
        response = self.client.patch(
            f"{LIST_URL}/{self.other_vault_tag.uuid}", body, "application/json"
        )
        self.assertEqual(response.status_code, 404)

    def test_renaming_a_tag_of_another_vault_of_ones_own_answers_404(self):
        """The other half: a vault the caller *can* open, naming a tag that is
        not in it. This is the tag lookup's refusal, not the vault guard's."""
        mine = make_vault(self.user)
        body = self.signed_tag(tag_uuid=self.tag.uuid, vault=mine)
        response = self.client.patch(
            f"{LIST_URL}/{self.tag.uuid}", body, "application/json"
        )
        self.assertEqual(response.status_code, 404)

    # --- authentication ---------------------------------------------------

    def test_an_anonymous_caller_is_refused(self):
        self.client.logout()
        response = self.client.get(f"{LIST_URL}?vault={self.vault.uuid}")
        self.assertIn(response.status_code, (302, 403))


class TagDeleteTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)
        self.tag = VaultTag.objects.create(
            vault=self.vault, encrypted_name=sealed("t"), metadata_sig="AQ"
        )
        self.carriers = [self._entry(i) for i in range(2)]
        VaultEntry.objects.filter(pk=self.carriers[1].pk).update(
            deleted_at=timezone.now()
        )

    def _entry(self, index):
        entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name=sealed(f"n{index}"),
            metadata_sig="AQ",
        )
        entry.tags.add(self.tag)
        return entry

    def resigned(self, entry):
        stored = VaultEntry.objects.get(pk=entry.pk)
        payload = entry_signature_payload(
            stored,
            signer_account_uuid=self.identity.uuid,
            tag_uuids=[t.uuid for t in stored.tags.all() if t.pk != self.tag.pk],
            fields=dict(stored.fields.values_list("field_id", "encrypted_value")),
        )
        return {"uuid": str(entry.uuid), "metadata_sig": sign(self.signer, payload)}

    def _post(self, entries):
        return self.client.post(
            f"{LIST_URL}/{self.tag.uuid}/delete",
            {"entries": entries},
            "application/json",
        )

    def test_removes_the_tag_and_keeps_every_signature_valid(self):
        before = {
            e.pk: e.updated_at for e in VaultEntry.objects.filter(vault=self.vault)
        }
        response = self._post([self.resigned(e) for e in self.carriers])
        self.assertEqual(response.status_code, 204)
        self.assertFalse(VaultTag.objects.filter(pk=self.tag.pk).exists())
        for entry in self.carriers:
            stored = VaultEntry.objects.get(pk=entry.pk)
            self.assertEqual(stored.updated_at, before[entry.pk])
            verify_record(
                entry_signature_payload(
                    stored,
                    signer_account_uuid=self.identity.uuid,
                    tag_uuids=[],
                    fields=dict(
                        stored.fields.values_list("field_id", "encrypted_value")
                    ),
                ),
                self.identity.sig_public,
                stored.metadata_sig,
            )

    def test_an_incomplete_set_is_409_and_changes_nothing(self):
        response = self._post([self.resigned(self.carriers[0])])
        self.assertEqual(response.status_code, 409)
        self.assertTrue(VaultTag.objects.filter(pk=self.tag.pk).exists())
        self.assertEqual(self.tag.entries.count(), 2)

    def test_a_bad_signature_is_400_and_changes_nothing(self):
        body = [self.resigned(e) for e in self.carriers]
        body[1]["metadata_sig"] = sign(self.signer, {"v": 1, "type": "x"})
        self.assertEqual(self._post(body).status_code, 400)
        self.assertEqual(
            VaultEntry.objects.get(pk=self.carriers[0].pk).metadata_sig, "AQ"
        )
        self.assertTrue(VaultTag.objects.filter(pk=self.tag.pk).exists())
        self.assertEqual(self.tag.entries.count(), 2)

    def test_a_tag_out_of_reach_is_404(self):
        stranger, _, _ = make_account("stranger")
        self.client.force_login(stranger)
        self.assertEqual(self._post([]).status_code, 404)
        self.assertTrue(VaultTag.objects.filter(pk=self.tag.pk).exists())

    def test_more_than_500_carriers_is_400(self):
        body = [
            {"uuid": f"00000000-0000-7000-8000-{i:012d}", "metadata_sig": "AQ"}
            for i in range(501)
        ]
        self.assertEqual(self._post(body).status_code, 400)

    def test_delete_is_gone(self):
        response = self.client.delete(f"{LIST_URL}/{self.tag.uuid}")
        self.assertEqual(response.status_code, 405)
