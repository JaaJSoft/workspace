import base64
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from workspace.vault.models import AccountIdentity, Vault, VaultKeyWrap
from workspace.vault.tests.reference import ad, primitives
from workspace.vault.tests.reference.encoding import to_base64url

User = get_user_model()

INIT_URL = "/api/v1/vault/account/init"
ENVELOPE_URL = "/api/v1/vault/account/envelope"
FINALIZE_URL = "/api/v1/vault/account/finalize"
ROTATE_URL = "/api/v1/vault/account/rotate"


def build_identity_payload(account_uuid):
    """The body the browser posts at the end of onboarding."""
    kex = primitives.generate_kex_keypair()
    sig = primitives.generate_sig_keypair()
    kex_public = to_base64url(
        primitives.encode_public_key(kex.public_key(), primitives.PUBKEY_ALG_X25519)
    )
    sig_public = to_base64url(
        primitives.encode_public_key(sig.public_key(), primitives.PUBKEY_ALG_ED25519)
    )
    return {
        "kdf_algo": "argon2id",
        "kdf_params": {"m": 65536, "t": 3, "p": 2},
        "kex_public": kex_public,
        "sig_public": sig_public,
        "wrapped_kex_priv": "WKEXAAAABBBBCCCC",
        "wrapped_sig_priv": "WSIGAAAABBBBCCCC",
        "sig_over_kex_pub": to_base64url(
            primitives.sign_bytes(sig, ad.kex_pub_payload(account_uuid, kex_public))
        ),
    }


class AccountInitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _post(self):
        return self.client.post(INIT_URL, data={}, content_type="application/json")

    def test_creates_a_pending_identity_and_returns_the_salt(self):
        response = self._post()
        self.assertEqual(response.status_code, 201)
        identity = AccountIdentity.objects.get(user=self.user)
        self.assertEqual(identity.state, AccountIdentity.State.PENDING)
        self.assertEqual(response.json()["kdf_salt"], identity.kdf_salt)

    def test_returns_the_identity_uuid_as_the_account_identifier(self):
        """Every associated data string the browser builds is bound to this
        value. It is the identity row's UUID, not a user id: Django's user
        primary key is an integer another account could inherit."""
        response = self._post()
        identity = AccountIdentity.objects.get(user=self.user)
        self.assertEqual(response.json()["account_uuid"], str(identity.uuid))

    def test_the_salt_is_thirty_two_bytes_of_base64url(self):
        self._post()
        salt = AccountIdentity.objects.get(user=self.user).kdf_salt
        raw = base64.urlsafe_b64decode(salt + "=" * (-len(salt) % 4))
        self.assertEqual(len(raw), 32)

    def test_two_accounts_do_not_share_a_salt(self):
        self._post()
        other = User.objects.create_user(username="other", password="pw")
        self.client.force_login(other)
        self._post()
        salts = set(AccountIdentity.objects.values_list("kdf_salt", flat=True))
        self.assertEqual(len(salts), 2)

    def test_is_idempotent_while_pending(self):
        """An onboarding interrupted after init has to be resumable: a second
        call returns the same salt rather than orphaning the first."""
        first = self._post()
        second = self._post()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["kdf_salt"], first.json()["kdf_salt"])
        self.assertEqual(second.json()["account_uuid"], first.json()["account_uuid"])
        self.assertEqual(AccountIdentity.objects.filter(user=self.user).count(), 1)

    def test_refuses_once_the_identity_is_active(self):
        """Regenerating the salt of an active account is as destructive as
        recreating the identity: the stored envelope stops being derivable and
        nothing says so until the next unlock."""
        identity = AccountIdentity.objects.create(
            user=self.user, kdf_salt="original", state=AccountIdentity.State.ACTIVE
        )
        response = self._post()
        self.assertEqual(response.status_code, 409)
        identity.refresh_from_db()
        self.assertEqual(identity.kdf_salt, "original")

    def test_survives_a_concurrent_first_call(self):
        """`user` is a OneToOneField, so two calls that both find no identity
        and both insert make the loser raise IntegrityError - a 500 on nothing
        worse than a double-clicked onboarding button. The race window is
        between the lookup and the insert, which is where the salt is drawn."""

        def competing_write():
            AccountIdentity.objects.create(user=self.user, kdf_salt="COMPETITOR")
            return "MINE"

        with patch("workspace.vault.views._new_salt", side_effect=competing_write):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kdf_salt"], "COMPETITOR")
        self.assertEqual(AccountIdentity.objects.filter(user=self.user).count(), 1)

    def test_requires_authentication(self):
        self.client.logout()
        response = self._post()
        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(AccountIdentity.objects.exists())


class AccountEnvelopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.other = User.objects.create_user(username="other", password="pw")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _identity(self, user):
        return AccountIdentity.objects.create(
            user=user,
            kdf_params={"m": 65536, "t": 3, "p": 2},
            kdf_salt="SALT",
            kex_public="KEX",
            sig_public="SIG",
            wrapped_kex_priv="WKEX",
            wrapped_sig_priv="WSIG",
            sig_over_kex_pub="ATTEST",
            state=AccountIdentity.State.ACTIVE,
        )

    def test_returns_the_callers_envelope(self):
        identity = self._identity(self.user)
        response = self.client.get(ENVELOPE_URL)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["wrapped_kex_priv"], "WKEX")
        self.assertEqual(body["uuid"], str(identity.uuid))
        self.assertEqual(body["state"], "active")

    def test_is_never_cached(self):
        self._identity(self.user)
        self.assertEqual(self.client.get(ENVELOPE_URL)["Cache-Control"], "no-store")

    def test_answers_404_when_no_identity_exists(self):
        self.assertEqual(self.client.get(ENVELOPE_URL).status_code, 404)

    def test_never_returns_another_users_envelope(self):
        self._identity(self.other)
        self.assertEqual(self.client.get(ENVELOPE_URL).status_code, 404)

    def test_ignores_an_unknown_or_malformed_query_parameter(self):
        """These endpoints route on request.user alone, so a stray parameter
        is ignored rather than parsed - and never a 500. The posture PR 6
        inherits, when vault UUIDs start appearing in query strings."""
        self._identity(self.user)
        response = self.client.get(ENVELOPE_URL + "?user=not-a-uuid&page=%00")
        self.assertEqual(response.status_code, 200)

    @override_settings(DEBUG=True)
    def test_a_crash_renders_no_secret_on_the_technical_500_page(self):
        """This view is not decorated: its locals hold the identity model, and
        a traceback renders locals. Django prints a model by its __str__, so
        the sealed keys never reach the page - a property worth pinning rather
        than reasoning about."""
        self._identity(self.user)
        self.client.raise_request_exception = False
        with patch(
            "workspace.vault.views.AccountEnvelopeSerializer",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.get(ENVELOPE_URL)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("WKEX", response.content.decode())

    def test_requires_authentication(self):
        self._identity(self.user)
        self.client.logout()
        self.assertIn(self.client.get(ENVELOPE_URL).status_code, (401, 403))


class AccountFinalizeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.force_login(self.user)
        self.identity = AccountIdentity.objects.create(user=self.user, kdf_salt="SALT")
        self.payload = build_identity_payload(str(self.identity.uuid))

    def tearDown(self):
        cache.clear()

    def _post(self, payload=None):
        return self.client.post(
            FINALIZE_URL,
            data=self.payload if payload is None else payload,
            content_type="application/json",
        )

    def test_activates_the_identity(self):
        response = self._post()
        self.assertEqual(response.status_code, 201)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.state, AccountIdentity.State.ACTIVE)
        self.assertEqual(self.identity.kex_public, self.payload["kex_public"])
        self.assertEqual(
            self.identity.wrapped_sig_priv, self.payload["wrapped_sig_priv"]
        )

    def test_keeps_the_same_row_and_the_same_salt(self):
        original_pk = self.identity.pk
        self._post()
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.pk, original_pk)
        self.assertEqual(self.identity.kdf_salt, "SALT")
        self.assertEqual(AccountIdentity.objects.filter(user=self.user).count(), 1)

    def test_refuses_an_attestation_bound_to_another_account(self):
        payload = build_identity_payload("0192f3a4-9999-7d8e-9f01-23456789abcd")
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.state, AccountIdentity.State.PENDING)
        self.assertEqual(self.identity.kex_public, "")

    def test_refuses_a_signature_that_does_not_verify(self):
        payload = dict(self.payload)
        payload["sig_over_kex_pub"] = build_identity_payload(str(self.identity.uuid))[
            "sig_over_kex_pub"
        ]
        self.assertEqual(self._post(payload).status_code, 400)

    def test_refuses_an_unsigned_payload(self):
        payload = dict(self.payload)
        payload["sig_over_kex_pub"] = ""
        self.assertEqual(self._post(payload).status_code, 400)

    def test_refuses_a_sig_public_labelled_as_a_kex_key(self):
        payload = dict(self.payload)
        sig = primitives.generate_sig_keypair()
        payload["sig_public"] = to_base64url(
            bytes([primitives.PUBKEY_ALG_X25519])
            + primitives.public_bytes(sig.public_key())
        )
        self.assertEqual(self._post(payload).status_code, 400)

    def test_refuses_a_second_finalize(self):
        """Re-finalizing would overwrite the sealed private keys with a fresh
        pair, and every existing key wrap points at the old one."""
        self._post()
        response = self._post(build_identity_payload(str(self.identity.uuid)))
        self.assertEqual(response.status_code, 409)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.kex_public, self.payload["kex_public"])

    def test_refuses_a_finalize_that_lost_a_race(self):
        """The state check and the write are not one step. Attestation
        verification sits in the window between them, so activating the row
        from there reproduces the interleaving deterministically: without an
        atomic guard the loser overwrites the winner's sealed private keys,
        and every key wrap the winner made is orphaned."""
        winner = build_identity_payload(str(self.identity.uuid))

        def activate_meanwhile(*args, **kwargs):
            AccountIdentity.objects.filter(pk=self.identity.pk).update(
                state=AccountIdentity.State.ACTIVE, **winner
            )

        with patch(
            "workspace.vault.views.verify_kex_pub_attestation",
            side_effect=activate_meanwhile,
        ):
            response = self._post()

        self.assertEqual(response.status_code, 409)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.kex_public, winner["kex_public"])
        self.assertEqual(self.identity.wrapped_kex_priv, winner["wrapped_kex_priv"])

    def test_answers_404_without_an_init(self):
        AccountIdentity.objects.all().delete()
        self.assertEqual(self._post().status_code, 404)

    def test_requires_authentication(self):
        self.client.logout()
        self.assertIn(self._post().status_code, (401, 403))
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.state, AccountIdentity.State.PENDING)

    @override_settings(DEBUG=True)
    def test_a_crash_renders_no_secret_on_the_technical_500_page(self):
        """The body reaches this view as request.data, so it lives in the
        frame's locals rather than in request.POST - and a traceback renders
        every frame's locals."""
        self.client.raise_request_exception = False
        with patch(
            "workspace.vault.views.verify_kex_pub_attestation",
            side_effect=RuntimeError("boom"),
        ):
            response = self._post()

        self.assertEqual(response.status_code, 500)
        page = response.content.decode()
        for value in (
            self.payload["wrapped_kex_priv"],
            self.payload["wrapped_sig_priv"],
        ):
            self.assertNotIn(value, page)

    def test_leaves_no_wrapped_key_in_the_logs(self):
        import logging

        with self.assertLogs("django", level="DEBUG") as captured:
            self._post()
            logging.getLogger("django").debug("finalize done")
        blob = "\n".join(captured.output)
        for value in (
            self.payload["wrapped_kex_priv"],
            self.payload["wrapped_sig_priv"],
        ):
            self.assertNotIn(value, blob)


class AccountRotateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.force_login(self.user)
        self.identity = AccountIdentity.objects.create(
            user=self.user,
            kdf_algo="argon2id",
            kdf_params={"m": 65536, "t": 3, "p": 2},
            kdf_salt="SALT",
            kex_public="KEX",
            sig_public="SIG",
            wrapped_kex_priv="OLD-WKEX",
            wrapped_sig_priv="OLD-WSIG",
            sig_over_kex_pub="ATTEST",
            state=AccountIdentity.State.ACTIVE,
        )
        self.vault = Vault.objects.create(
            owner=self.user, encrypted_name="NAME", metadata_sig="SIG"
        )
        self.wrap = VaultKeyWrap.objects.create(
            vault=self.vault,
            recipient=self.user,
            wrapped_key="WRAPPED",
            key_version=1,
            hpke_suite={"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0},
        )
        self.body = {
            "kdf_params": {"m": 262144, "t": 4, "p": 2},
            "wrapped_kex_priv": "NEW-WKEX",
            "wrapped_sig_priv": "NEW-WSIG",
        }

    def tearDown(self):
        cache.clear()

    def _post(self, body=None):
        return self.client.post(
            ROTATE_URL,
            data=self.body if body is None else body,
            content_type="application/json",
        )

    def test_rewrites_the_envelope(self):
        self.assertEqual(self._post().status_code, 200)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.wrapped_kex_priv, "NEW-WKEX")
        self.assertEqual(self.identity.wrapped_sig_priv, "NEW-WSIG")
        self.assertEqual(self.identity.kdf_params, {"m": 262144, "t": 4, "p": 2})

    def test_updates_the_row_in_place_rather_than_recreating_it(self):
        """The identity UUID is what every associated data string of this
        account is bound to, and the sealed private keys are the only path back
        to every VaultKeyWrap. A delete-then-recreate is invisible in the data:
        VaultKeyWrap points at auth.User, not at the identity, so every wrap
        survives the deletion intact and merely stops being decryptable. The
        account can never open a vault again, and nothing says so until the
        next unlock. Only the UUID's stability catches it."""
        original_uuid = self.identity.uuid
        self.assertEqual(self._post().status_code, 200)

        identities = AccountIdentity.objects.filter(user=self.user)
        self.assertEqual(identities.count(), 1)
        self.assertEqual(identities.get().uuid, original_uuid)

    def test_keeps_every_key_wrap(self):
        self.assertEqual(self._post().status_code, 200)
        self.wrap.refresh_from_db()
        self.assertEqual(self.wrap.wrapped_key, "WRAPPED")
        self.assertEqual(VaultKeyWrap.objects.filter(recipient=self.user).count(), 1)

    def test_never_touches_the_public_keys_or_the_salt(self):
        response = self._post(
            {
                **self.body,
                "kex_public": "ATTACKER-KEX",
                "sig_public": "ATTACKER-SIG",
                "kdf_salt": "ATTACKER-SALT",
                "sig_over_kex_pub": "ATTACKER-ATTEST",
                "state": "pending",
            }
        )
        self.assertEqual(response.status_code, 200)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.kex_public, "KEX")
        self.assertEqual(self.identity.sig_public, "SIG")
        self.assertEqual(self.identity.kdf_salt, "SALT")
        self.assertEqual(self.identity.sig_over_kex_pub, "ATTEST")
        self.assertEqual(self.identity.state, AccountIdentity.State.ACTIVE)

    def test_refuses_a_body_missing_a_wrapped_key(self):
        body = dict(self.body)
        del body["wrapped_sig_priv"]
        self.assertEqual(self._post(body).status_code, 400)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.wrapped_kex_priv, "OLD-WKEX")

    def test_answers_404_when_the_identity_is_still_pending(self):
        AccountIdentity.objects.filter(user=self.user).update(
            state=AccountIdentity.State.PENDING
        )
        self.assertEqual(self._post().status_code, 404)

    def test_answers_404_without_an_identity(self):
        VaultKeyWrap.objects.all().delete()
        Vault.objects.all().delete()
        AccountIdentity.objects.all().delete()
        self.assertEqual(self._post().status_code, 404)

    def test_the_response_is_never_cached(self):
        self.assertEqual(self._post()["Cache-Control"], "no-store")

    def test_requires_authentication(self):
        self.client.logout()
        self.assertIn(self._post().status_code, (401, 403))
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.wrapped_kex_priv, "OLD-WKEX")
