"""The vault collection endpoint.

Every test that needs a signature makes one: the account's Ed25519 key is
generated in setUp and the payload is signed with the same builder the view
verifies against, so a change to the signed key set fails here rather than in
a browser.
"""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.vault.models import AccountIdentity, Vault, VaultKeyWrap
from workspace.vault.services.metadata import canonical_cbor, vault_metadata_payload
from workspace.vault.tests.reference.encoding import to_base64url

User = get_user_model()

VAULT_UUID = "0192f3a4-2222-7d8e-9f01-23456789abcd"
HPKE_SUITE = {"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0}


class VaultCreateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.force_login(self.user)
        self.signer = Ed25519PrivateKey.generate()
        self.identity = AccountIdentity.objects.create(
            user=self.user,
            kdf_salt="SALT",
            state=AccountIdentity.State.ACTIVE,
            sig_public=to_base64url(
                bytes([0x02]) + self.signer.public_key().public_bytes_raw()
            ),
        )
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
        payload = vault_metadata_payload(**fields)
        signature = bytes([0x01]) + self.signer.sign(canonical_cbor(payload))
        return {
            "uuid": fields["vault_uuid"],
            "encrypted_name": fields["encrypted_name"],
            "encrypted_description": fields["encrypted_description"],
            "icon": fields["icon"],
            "color": fields["color"],
            "metadata_sig": to_base64url(signature),
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
