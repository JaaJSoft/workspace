import base64

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.vault.models import AccountIdentity

User = get_user_model()

INIT_URL = "/api/v1/vault/account/init"
ENVELOPE_URL = "/api/v1/vault/account/envelope"


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

    def test_requires_authentication(self):
        self._identity(self.user)
        self.client.logout()
        self.assertIn(self.client.get(ENVELOPE_URL).status_code, (401, 403))
