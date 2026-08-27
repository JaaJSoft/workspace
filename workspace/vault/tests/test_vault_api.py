"""The vault collection endpoint.

Every test that needs a signature makes one: the account's Ed25519 key is
generated in setUp and the payload is signed with the same builder the view
verifies against, so a change to the signed key set fails here rather than in
a browser.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.vault.models import AccountIdentity, Vault, VaultKeyWrap
from workspace.vault.services.metadata import vault_metadata_payload
from workspace.vault.tests.factories import HPKE_SUITE, make_account, sign
from workspace.vault.tests.reference.encoding import to_base64url

User = get_user_model()

VAULT_UUID = "0192f3a4-2222-7d8e-9f01-23456789abcd"


class VaultCreateTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.url = reverse("vault-list")

    def _body(self, **overrides):
        fields = {
            "vault_uuid": VAULT_UUID,
            "owner_account_uuid": str(self.identity.uuid),
            "encrypted_name": "AQEBAAABc2VhbGVk",
            "encrypted_description": "",
            "icon": "lock",
            "color": "primary",
            "key_version": 1,
            "is_favorite": False,
        }
        fields.update(overrides)
        signature = sign(self.signer, vault_metadata_payload(**fields))
        return {
            "uuid": fields["vault_uuid"],
            "encrypted_name": fields["encrypted_name"],
            "encrypted_description": fields["encrypted_description"],
            "icon": fields["icon"],
            "color": fields["color"],
            "metadata_sig": signature,
            "wrapped_key": to_base64url(bytes(range(64))),
            "hpke_suite": HPKE_SUITE,
        }

    def test_a_signed_vault_is_created_with_its_key_wrap(self):
        response = self.client.post(self.url, self._body(), "application/json")
        self.assertEqual(response.status_code, 201)
        vault = Vault.objects.get(uuid=VAULT_UUID)
        self.assertEqual(vault.owner, self.user)
        self.assertEqual(vault.key_version, 1)
        wrap = VaultKeyWrap.objects.get(vault=vault)
        self.assertEqual(wrap.recipient, self.user)
        self.assertEqual(wrap.hpke_suite, HPKE_SUITE)

    def test_the_response_carries_the_wrap_the_caller_needs_to_open_it(self):
        body = self._body()
        response = self.client.post(self.url, body, "application/json")
        self.assertEqual(response.json()["wrapped_key"], body["wrapped_key"])

    def test_an_unsigned_vault_is_refused(self):
        body = self._body()
        body["metadata_sig"] = ""
        response = self.client.post(self.url, body, "application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Vault.objects.exists())

    def test_a_signature_over_another_name_is_refused(self):
        body = self._body()
        body["encrypted_name"] = "AQEBAAABdGFtcGVyZWQ"
        response = self.client.post(self.url, body, "application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Vault.objects.exists())

    def test_nothing_is_written_when_the_signature_fails(self):
        """The vault and its wrap go in one transaction: a half-created vault
        is one nobody can open and nobody can delete from the interface."""
        body = self._body()
        body["icon"] = "star"
        self.client.post(self.url, body, "application/json")
        self.assertFalse(Vault.objects.exists())
        self.assertFalse(VaultKeyWrap.objects.exists())

    def test_a_second_vault_on_the_same_uuid_is_refused(self):
        self.client.post(self.url, self._body(), "application/json")
        response = self.client.post(self.url, self._body(), "application/json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Vault.objects.count(), 1)

    def test_an_unknown_hpke_suite_is_refused(self):
        body = self._body()
        body["hpke_suite"] = {"kem_id": 16, "kdf_id": 1, "aead_id": 2, "mode": 0}
        self.assertEqual(
            self.client.post(self.url, body, "application/json").status_code, 400
        )

    def test_an_icon_outside_the_allowed_shape_is_refused(self):
        body = self._body(icon="lock'; DROP")
        self.assertEqual(
            self.client.post(self.url, body, "application/json").status_code, 400
        )

    def test_an_account_without_an_active_identity_cannot_create_a_vault(self):
        AccountIdentity.objects.filter(pk=self.identity.pk).update(
            state=AccountIdentity.State.PENDING
        )
        self.assertEqual(
            self.client.post(self.url, self._body(), "application/json").status_code,
            404,
        )

    def test_the_endpoint_requires_authentication(self):
        self.client.logout()
        self.assertIn(
            self.client.post(self.url, self._body(), "application/json").status_code,
            (401, 403),
        )

    def test_the_response_is_never_cached(self):
        response = self.client.post(self.url, self._body(), "application/json")
        self.assertIn("no-store", response["Cache-Control"])


class VaultListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.other = User.objects.create_user(username="other", password="pw")
        self.client.force_login(self.user)
        self.url = reverse("vault-list")

    def _vault(self, owner, **overrides):
        fields = {"encrypted_name": "AQ", "metadata_sig": "AQ"}
        fields.update(overrides)
        return Vault.objects.create(owner=owner, **fields)

    def test_only_the_callers_vaults_come_back(self):
        mine = self._vault(self.user)
        self._vault(self.other)
        response = self.client.get(self.url)
        self.assertEqual([item["uuid"] for item in response.json()], [str(mine.uuid)])

    def test_a_vault_reached_through_a_key_wrap_is_listed(self):
        shared = self._vault(self.other)
        VaultKeyWrap.objects.create(
            vault=shared, recipient=self.user, wrapped_key="AQ", hpke_suite=HPKE_SUITE
        )
        response = self.client.get(self.url)
        self.assertEqual([item["uuid"] for item in response.json()], [str(shared.uuid)])

    def test_a_vault_with_no_wrap_for_the_caller_reports_it_rather_than_hiding(self):
        """An owner whose wrap is missing cannot open their own vault. Saying
        so is the only way the interface can explain the failure."""
        self._vault(self.user)
        self.assertIsNone(self.client.get(self.url).json()[0]["wrapped_key"])

    def _seed(self, count):
        for _ in range(count):
            vault = self._vault(self.user)
            VaultKeyWrap.objects.create(
                vault=vault,
                recipient=self.user,
                wrapped_key="AQ",
                hpke_suite=HPKE_SUITE,
            )

    def test_listing_costs_the_same_whatever_the_number_of_vaults(self):
        """The absolute count depends on the session backend, so what is
        pinned is the shape: it must not grow with the collection."""
        # A presence-tracking middleware writes a UserPresence row (plus a
        # couple of reads) only on the first request this process ever sees
        # for the user, then relies on a process-global cache to skip it on
        # every later one. Spend that one-time cost here, outside both
        # captures, so neither measurement absorbs it.
        self.client.get(self.url)
        self._seed(1)
        with CaptureQueriesContext(connection) as one:
            self.client.get(self.url)
        Vault.objects.all().delete()
        self._seed(10)
        with CaptureQueriesContext(connection) as ten:
            self.client.get(self.url)
        self.assertEqual(len(ten), len(one))

    def test_the_listing_is_never_cached(self):
        self.assertIn("no-store", self.client.get(self.url)["Cache-Control"])


class VaultUpdateTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.other = User.objects.create_user(username="other", password="pw")
        self.client.force_login(self.user)
        self.vault = Vault.objects.create(
            uuid=VAULT_UUID, owner=self.user, encrypted_name="AQ", metadata_sig="AQ"
        )
        self.url = reverse("vault-detail", args=[VAULT_UUID])

    def _body(self, **overrides):
        fields = {
            "vault_uuid": VAULT_UUID,
            "owner_account_uuid": str(self.identity.uuid),
            "encrypted_name": "AQEBAAABcmVuYW1lZA",
            "encrypted_description": "",
            "icon": "lock",
            "color": "primary",
            "key_version": 1,
            "is_favorite": False,
        }
        fields.update(overrides)
        signature = sign(self.signer, vault_metadata_payload(**fields))
        return {
            "encrypted_name": fields["encrypted_name"],
            "encrypted_description": fields["encrypted_description"],
            "icon": fields["icon"],
            "color": fields["color"],
            "is_favorite": fields["is_favorite"],
            "metadata_sig": signature,
        }

    def test_a_signed_rename_lands(self):
        response = self.client.patch(self.url, self._body(), "application/json")
        self.assertEqual(response.status_code, 200)
        self.vault.refresh_from_db()
        self.assertEqual(self.vault.encrypted_name, "AQEBAAABcmVuYW1lZA")

    def test_the_new_signature_replaces_the_old_one(self):
        body = self._body()
        self.client.patch(self.url, body, "application/json")
        self.vault.refresh_from_db()
        self.assertEqual(self.vault.metadata_sig, body["metadata_sig"])

    def test_a_favourite_is_re_signed_like_any_other_field(self):
        response = self.client.patch(
            self.url, self._body(is_favorite=True), "application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.vault.refresh_from_db()
        self.assertTrue(self.vault.is_favorite)

    def test_an_unsigned_rename_leaves_the_vault_alone(self):
        body = self._body()
        body["encrypted_name"] = "AQEBAAABdGFtcGVyZWQ"
        self.assertEqual(
            self.client.patch(self.url, body, "application/json").status_code, 400
        )
        self.vault.refresh_from_db()
        self.assertEqual(self.vault.encrypted_name, "AQ")

    def test_a_signature_from_another_key_version_is_refused(self):
        """key_version is inside the payload and the server takes it from the
        row: a client signing a version it invented cannot rewrite the vault."""
        self.assertEqual(
            self.client.patch(
                self.url, self._body(key_version=2), "application/json"
            ).status_code,
            400,
        )

    def test_someone_elses_vault_answers_404_not_403(self):
        theirs = Vault.objects.create(
            owner=self.other, encrypted_name="AQ", metadata_sig="AQ"
        )
        response = self.client.patch(
            reverse("vault-detail", args=[theirs.uuid]),
            self._body(),
            "application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_a_vault_that_does_not_exist_answers_the_same_404(self):
        response = self.client.patch(
            reverse("vault-detail", args=["0192f3a4-9999-7d8e-9f01-23456789abcd"]),
            self._body(),
            "application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_a_member_cannot_rename_a_vault_they_do_not_own(self):
        theirs = Vault.objects.create(
            owner=self.other, encrypted_name="AQ", metadata_sig="AQ"
        )
        VaultKeyWrap.objects.create(
            vault=theirs, recipient=self.user, wrapped_key="AQ", hpke_suite=HPKE_SUITE
        )
        response = self.client.patch(
            reverse("vault-detail", args=[theirs.uuid]),
            self._body(),
            "application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_the_owner_can_delete_their_vault(self):
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertFalse(Vault.objects.filter(uuid=VAULT_UUID).exists())

    def test_deleting_a_vault_takes_its_key_wraps_with_it(self):
        VaultKeyWrap.objects.create(
            vault=self.vault,
            recipient=self.user,
            wrapped_key="AQ",
            hpke_suite=HPKE_SUITE,
        )
        self.client.delete(self.url)
        self.assertFalse(VaultKeyWrap.objects.exists())

    def test_deleting_someone_elses_vault_answers_404_and_changes_nothing(self):
        theirs = Vault.objects.create(
            owner=self.other, encrypted_name="AQ", metadata_sig="AQ"
        )
        response = self.client.delete(reverse("vault-detail", args=[theirs.uuid]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Vault.objects.filter(uuid=theirs.uuid).exists())
